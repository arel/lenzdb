"""Project discovery, schema handling, and CSV persistence."""

from __future__ import annotations

import csv
import os
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from lenzdb.errors import MutationError, ProjectError
from lenzdb.models import ColumnSchema, LensPolicy, TableSchema

DEFAULT_NAMESPACE = "main"
TABLES_CONFIG_KEY = "tables"
LENSES_CONFIG_KEY = "lenses"
DEFAULT_VIEW_PAGE_SIZE = 100


def parse_qualified_name(value: str) -> tuple[str, str]:
    parts = value.rsplit(".", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ProjectError(f"Expected a qualified name like table.column, got {value!r}")
    return parts[0], parts[1]


def parse_namespaced_name(value: str, *, default_namespace: str = DEFAULT_NAMESPACE) -> tuple[str, str]:
    parts = value.rsplit(".", 1)
    if len(parts) == 1 and parts[0]:
        return default_namespace, parts[0]
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    raise ProjectError(f"Expected a name like resource or namespace.resource, got {value!r}")


def resource_key(namespace: str, name: str) -> str:
    return f"{namespace}.{name}"


def split_resource_key(key: str) -> tuple[str, str]:
    return parse_namespaced_name(key)


@dataclass(slots=True)
class InvalidResource:
    namespace: str
    name: str
    path: Path
    message: str


def has_glob_pattern(value: str) -> bool:
    return any(character in value for character in "*?[")


def canonical_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def parse_column_value(raw: Any, column: ColumnSchema, *, location: str = "value") -> Any:
    if raw is None:
        return None

    if not isinstance(raw, str):
        text = canonical_scalar(raw)
    else:
        text = raw

    if column.type == "string":
        return text

    if text == "":
        return None

    try:
        if column.type == "integer":
            return int(text)
        if column.type == "float":
            return float(text)
        if column.type == "boolean":
            lowered = text.lower()
            if lowered in {"true", "1", "yes", "y"}:
                return True
            if lowered in {"false", "0", "no", "n"}:
                return False
            raise ValueError("expected a boolean value")
        if column.type == "enum":
            if text not in column.values:
                raise ValueError(f"expected one of {column.values!r}")
            return text
        if column.type == "ref":
            return text
        if column.type == "date":
            return date.fromisoformat(text)
        if column.type == "timestamp":
            return datetime.fromisoformat(text)
    except ValueError as exc:  # pragma: no cover - exercised by callers
        raise ProjectError(f"Invalid {location}: {exc}") from exc

    return text


def serialize_value(value: Any, column: ColumnSchema | None = None) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def normalize_primary_key(primary_key: str | list[str]) -> list[str]:
    return [primary_key] if isinstance(primary_key, str) else list(primary_key)


def clone_rows_map(
    rows_by_table: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    return deepcopy(rows_by_table)


@dataclass(slots=True)
class Project:
    root: Path
    schema_dir: Path
    policies_dir: Path
    table_paths: dict[str, Path]
    invalid_table_paths: dict[Path, InvalidResource]
    schemas: dict[str, TableSchema]
    lenses: dict[str, Path]
    invalid_lens_paths: dict[Path, InvalidResource]
    policies: dict[str, LensPolicy]
    view_page_size: int = DEFAULT_VIEW_PAGE_SIZE

    @classmethod
    def discover(
        cls,
        root: str | Path | None = None,
        *,
        validate_configuration: bool = True,
        allow_incomplete: bool = False,
    ) -> Project:
        project_root = cls._resolve_project_root(root)
        lenz_dir = project_root / ".lenzdb"
        project_config = cls._load_project_config(lenz_dir / "project.yaml")
        data_dir = lenz_dir / "data"
        schema_dir = lenz_dir / "schema"
        lenses_dir = lenz_dir / "lenses"
        policies_dir = lenz_dir / "policies"

        if not schema_dir.exists():
            if not allow_incomplete:
                raise ProjectError(
                    f"No LenzDB project found at {project_root}. Expected {schema_dir}. "
                    "Run from a project root, pass --project, or set $LENZDB_PROJECT_ROOT."
                )
            schemas = {}
        else:
            schemas = cls._load_schemas(schema_dir, require_schema=not allow_incomplete)

        table_paths, invalid_table_paths = cls._load_tables(
            project_root,
            data_dir,
            project_config,
            require_tables=not allow_incomplete,
        )
        lenses, invalid_lens_paths = cls._load_lenses(
            project_root,
            lenses_dir,
            project_config,
        )
        policies = cls._load_policies(policies_dir)
        view_page_size = cls._load_view_page_size(project_config)

        project = cls(
            root=project_root,
            schema_dir=schema_dir,
            policies_dir=policies_dir,
            table_paths=table_paths,
            invalid_table_paths=invalid_table_paths,
            schemas=schemas,
            lenses=lenses,
            invalid_lens_paths=invalid_lens_paths,
            policies=policies,
            view_page_size=view_page_size,
        )
        if validate_configuration:
            project.validate_configuration()
        return project

    @staticmethod
    def _resolve_project_root(root: str | Path | None) -> Path:
        if root is not None:
            return Path(root).resolve()
        cwd = Path.cwd().resolve()
        for candidate in [cwd, *cwd.parents]:
            if (candidate / ".lenzdb").exists():
                return candidate
        return cwd

    @staticmethod
    def _load_yaml(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    @classmethod
    def _load_project_config(cls, config_path: Path) -> dict[str, Any]:
        if not config_path.exists():
            return {}
        config = cls._load_yaml(config_path)
        if not isinstance(config, dict):
            raise ProjectError(f"Project config must be a mapping: {config_path}")
        for section_name in [TABLES_CONFIG_KEY, LENSES_CONFIG_KEY]:
            section = config.get(section_name, [])
            if not isinstance(section, list):
                raise ProjectError(
                    f"Project config field {section_name!r} must be a list: {config_path}"
                )
        view_section = config.get("view", {})
        if not isinstance(view_section, dict):
            raise ProjectError(f"Project config field 'view' must be a mapping: {config_path}")
        return config

    @staticmethod
    def _load_view_page_size(project_config: dict[str, Any]) -> int:
        view_section = project_config.get("view", {})
        page_size = view_section.get("page_size", DEFAULT_VIEW_PAGE_SIZE)
        if not isinstance(page_size, int) or isinstance(page_size, bool) or page_size < 1:
            raise ProjectError("Project config field 'view.page_size' must be a positive integer")
        return page_size

    @classmethod
    def _load_schemas(cls, schema_dir: Path, *, require_schema: bool = True) -> dict[str, TableSchema]:
        schemas: dict[str, TableSchema] = {}
        for path in sorted(schema_dir.glob("*.y*ml")):
            schema = TableSchema.model_validate(cls._load_yaml(path))
            namespace, table_name = parse_namespaced_name(schema.table)
            table_key = resource_key(namespace, table_name)
            if table_key in schemas:
                raise ProjectError(f"Duplicate schema for table {table_key!r}")
            schemas[table_key] = schema
        if require_schema and not schemas:
            raise ProjectError(f"No schema files found in {schema_dir}")
        return schemas

    @classmethod
    def _load_tables(
        cls,
        project_root: Path,
        data_dir: Path,
        project_config: dict[str, Any],
        *,
        require_tables: bool = True,
    ) -> tuple[dict[str, Path], dict[Path, InvalidResource]]:
        table_paths: dict[str, Path] = {}
        invalid_table_paths: dict[Path, InvalidResource] = {}
        for source_dir in [project_root, data_dir]:
            if not source_dir.exists():
                continue
            for path in sorted(source_dir.glob("*.csv")):
                cls._add_discovered_path(
                    table_paths,
                    invalid_table_paths,
                    path=path,
                    resource_kind="CSV table",
                )
        cls._load_registered_paths(
            project_root,
            project_config.get(TABLES_CONFIG_KEY, []),
            suffix=".csv",
            resource_kind="CSV table",
            resources=table_paths,
            invalid_resources=invalid_table_paths,
            require_existing=require_tables,
        )
        if require_tables and not table_paths:
            raise ProjectError(
                f"No CSV table files found in {project_root} or {data_dir}"
            )
        return table_paths, invalid_table_paths

    @classmethod
    def _load_lenses(
        cls,
        project_root: Path,
        lenses_dir: Path,
        project_config: dict[str, Any],
    ) -> tuple[dict[str, Path], dict[Path, InvalidResource]]:
        lenses: dict[str, Path] = {}
        invalid_lens_paths: dict[Path, InvalidResource] = {}
        for source_dir in [project_root, lenses_dir]:
            if not source_dir.exists():
                continue
            for path in sorted(source_dir.glob("*.sql")):
                cls._add_discovered_path(
                    lenses,
                    invalid_lens_paths,
                    path=path,
                    resource_kind="lens",
                )
        cls._load_registered_paths(
            project_root,
            project_config.get(LENSES_CONFIG_KEY, []),
            suffix=".sql",
            resource_kind="lens",
            resources=lenses,
            invalid_resources=invalid_lens_paths,
            require_existing=True,
        )
        return lenses, invalid_lens_paths

    @classmethod
    def _add_discovered_path(
        cls,
        resources: dict[str, Path],
        invalid_resources: dict[Path, InvalidResource],
        *,
        path: Path,
        resource_kind: str,
        namespace: str | None = None,
        name: str | None = None,
    ) -> None:
        if name is not None:
            cls._add_resource_path(
                resources,
                namespace=namespace or DEFAULT_NAMESPACE,
                name=name,
                path=path,
                resource_kind=resource_kind,
            )
            return

        try:
            parsed_namespace, parsed_name = parse_namespaced_name(path.stem)
        except ProjectError as exc:
            invalid_resources[path] = InvalidResource(
                namespace=namespace or DEFAULT_NAMESPACE,
                name=path.stem,
                path=path,
                message=str(exc),
            )
            return

        cls._add_resource_path(
            resources,
            namespace=namespace or parsed_namespace,
            name=parsed_name,
            path=path,
            resource_kind=resource_kind,
        )

    @classmethod
    def _load_registered_paths(
        cls,
        project_root: Path,
        entries: list[Any],
        *,
        suffix: str,
        resource_kind: str,
        resources: dict[str, Path],
        invalid_resources: dict[Path, InvalidResource],
        require_existing: bool = True,
    ) -> None:
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                raise ProjectError(f"Registered {resource_kind} entry #{index} must be a mapping")
            path_value = entry.get("path")
            if not isinstance(path_value, str) or not path_value:
                raise ProjectError(
                    f"Registered {resource_kind} entry #{index} must include a non-empty path"
                )
            path_pattern = Path(path_value)
            if path_pattern.is_absolute():
                raise ProjectError(
                    f"Registered {resource_kind} path must be relative to the project root: {path_value}"
                )

            configured_namespace = entry.get("namespace")
            if configured_namespace is not None and (
                not isinstance(configured_namespace, str) or not configured_namespace
            ):
                raise ProjectError(
                    f"Registered {resource_kind} entry #{index} has an invalid namespace"
                )

            configured_name = entry.get("name")
            if configured_name is not None and (
                not isinstance(configured_name, str) or not configured_name
            ):
                raise ProjectError(f"Registered {resource_kind} entry #{index} has an invalid name")
            if isinstance(configured_name, str) and "." in configured_name:
                raise ProjectError(
                    f"Registered {resource_kind} entry #{index} name must not include a namespace"
                )

            is_glob = has_glob_pattern(path_value)
            resolved_path = (project_root / path_pattern).resolve()
            is_directory = not is_glob and resolved_path.is_dir()
            if (is_glob or is_directory) and not configured_namespace:
                raise ProjectError(
                    f"Registered {resource_kind} folders and globs must specify a namespace: {path_value}"
                )
            if (is_glob or is_directory) and configured_name:
                raise ProjectError(
                    f"Registered {resource_kind} folders and globs cannot specify a single name: {path_value}"
                )

            namespace = configured_namespace
            if is_glob:
                paths = sorted(
                    path.resolve()
                    for path in project_root.glob(path_value)
                    if path.is_file() and path.suffix == suffix
                )
            elif is_directory:
                paths = sorted(path.resolve() for path in resolved_path.glob(f"*{suffix}"))
            else:
                paths = [resolved_path]

            if not paths:
                if require_existing:
                    raise ProjectError(f"Registered {resource_kind} path matched no {suffix} files: {path_value}")
                continue

            for path in paths:
                if not path.exists():
                    if require_existing:
                        raise ProjectError(f"Registered {resource_kind} path does not exist: {path}")
                    if path.suffix != suffix:
                        raise ProjectError(
                            f"Registered {resource_kind} path must point to a {suffix} file: {path}"
                        )
                    cls._add_discovered_path(
                        resources,
                        invalid_resources,
                        path=path,
                        resource_kind=resource_kind,
                        namespace=namespace,
                        name=configured_name,
                    )
                    continue
                if not path.is_file() or path.suffix != suffix:
                    raise ProjectError(
                        f"Registered {resource_kind} path must point to a {suffix} file: {path}"
                    )
                cls._add_discovered_path(
                    resources,
                    invalid_resources,
                    path=path,
                    resource_kind=resource_kind,
                    namespace=namespace,
                    name=configured_name,
                )

    @staticmethod
    def _add_resource_path(
        resources: dict[str, Path],
        *,
        namespace: str,
        name: str,
        path: Path,
        resource_kind: str,
    ) -> None:
        key = resource_key(namespace, name)
        if key in resources:
            raise ProjectError(
                f"Duplicate {resource_kind} {key!r}: {resources[key]} and {path}"
            )
        resources[key] = path

    @classmethod
    def _load_policies(cls, policies_dir: Path) -> dict[str, LensPolicy]:
        if not policies_dir.exists():
            return {}
        policies: dict[str, LensPolicy] = {}
        for path in sorted(policies_dir.glob("*.y*ml")):
            policy = LensPolicy.model_validate(cls._load_yaml(path))
            namespace, lens_name = parse_namespaced_name(policy.lens)
            lens_key = resource_key(namespace, lens_name)
            if lens_key in policies:
                raise ProjectError(f"Duplicate policy for lens {lens_key!r}")
            policies[lens_key] = policy
        return policies

    def lens_sql(self, lens_name: str) -> str:
        resolved_lens_name = self.resolve_lens_name(lens_name)
        path = self.lenses.get(resolved_lens_name)
        if path is None:
            raise ProjectError(f"Unknown lens {lens_name!r}")
        return path.read_text(encoding="utf-8")

    def schema_for(self, table: str) -> TableSchema:
        resolved_table = self.resolve_table_name(table)
        try:
            return self.schemas[resolved_table]
        except KeyError as exc:
            raise ProjectError(f"Unknown table {table!r}") from exc

    def policy_for(self, lens_name: str) -> LensPolicy | None:
        try:
            resolved_lens = self.resolve_lens_name(lens_name)
        except ProjectError:
            return None
        return self.policies.get(resolved_lens)

    def table_path(self, table: str) -> Path:
        resolved_table = self.resolve_table_name(table)
        try:
            return self.table_paths[resolved_table]
        except KeyError as exc:
            raise ProjectError(f"Unknown table {table!r}") from exc

    def resolve_table_name(self, table_name: str, namespace: str | None = None) -> str:
        return self._resolve_resource_name(table_name, self.schemas, "table", namespace)

    def resolve_lens_name(self, lens_name: str, namespace: str | None = None) -> str:
        return self._resolve_resource_name(lens_name, self.lenses, "lens", namespace)

    def resolve_resource_name(self, resource_name: str) -> tuple[str, str]:
        resources: dict[str, str] = {
            **{table_name: "table" for table_name in self.schemas},
            **{lens_name: "lens" for lens_name in self.lenses},
        }
        resolved_name = self._resolve_resource_name(resource_name, resources, "resource")
        return resources[resolved_name], resolved_name

    def _resolve_resource_name(
        self,
        resource_name: str,
        resources: dict[str, Any],
        resource_kind: str,
        namespace: str | None = None,
    ) -> str:
        if namespace is not None:
            resolved_namespace = namespace or DEFAULT_NAMESPACE
            name = resource_name
        else:
            if "." not in resource_name and resource_name:
                matches = sorted(
                    key for key in resources if split_resource_key(key)[1] == resource_name
                )
                if len(matches) == 1:
                    return matches[0]
                if len(matches) > 1:
                    raise ProjectError(
                        f"Ambiguous {resource_kind} {resource_name!r}; use one of: "
                        + ", ".join(matches)
                    )
                raise ProjectError(f"Unknown {resource_kind} {resource_name!r}")
            resolved_namespace, name = parse_namespaced_name(resource_name)

        key = resource_key(resolved_namespace, name)
        if key not in resources and resolved_namespace not in self._resource_namespaces(resources):
            raise ProjectError(
                f"Unknown {resource_kind} namespace {resolved_namespace!r}; "
                f"available namespaces: {', '.join(self._resource_namespaces(resources))}"
            )
        if key not in resources:
            raise ProjectError(f"Unknown {resource_kind} {resource_name!r}")
        return key

    @staticmethod
    def _resource_namespaces(resources: dict[str, Any]) -> list[str]:
        return sorted({split_resource_key(key)[0] for key in resources})

    def table_headers(self, table: str) -> list[str]:
        return list(self.schema_for(table).columns)

    def primary_key_columns(self, table: str) -> list[str]:
        return normalize_primary_key(self.schema_for(table).primary_key)

    def is_primary_key_column(self, table: str, column_name: str) -> bool:
        return column_name in self.primary_key_columns(table)

    def primary_key_tuple(self, table: str, row: dict[str, Any]) -> tuple[str, ...]:
        schema = self.schema_for(table)
        return tuple(
            serialize_value(row.get(column_name), schema.columns[column_name])
            for column_name in normalize_primary_key(schema.primary_key)
        )

    def primary_key_display(self, table: str, row: dict[str, Any]) -> str:
        values = self.primary_key_tuple(table, row)
        if len(values) == 1:
            return values[0]
        return " | ".join(values)

    def blank_row(self, table: str) -> dict[str, Any]:
        schema = self.schema_for(table)
        return {column_name: None for column_name in schema.columns}

    def generate_primary_key(self, table: str) -> str:
        schema = self.schema_for(table)
        primary_keys = normalize_primary_key(schema.primary_key)
        if len(primary_keys) != 1:
            raise MutationError(f"Cannot auto-generate composite primary keys for {table}")
        primary_key = primary_keys[0]
        column = schema.columns[primary_key]
        if column.type != "string":
            raise MutationError(
                f"Cannot auto-generate primary keys for non-string column {table}.{primary_key}"
            )
        return str(uuid4())

    def load_table_rows(self, table: str) -> list[dict[str, Any]]:
        schema = self.schema_for(table)
        path = self.table_path(table)
        if not path.exists():
            raise ProjectError(f"Missing CSV file for table {table!r}: {path}")

        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or []
            expected = list(schema.columns)
            missing = [name for name in expected if name not in headers]
            extra = [name for name in headers if name not in schema.columns]
            if missing or extra:
                raise ProjectError(
                    f"CSV header mismatch for {path}: missing={missing or '[]'}, extra={extra or '[]'}"
                )

            rows: list[dict[str, Any]] = []
            for line_number, raw_row in enumerate(reader, start=2):
                parsed_row: dict[str, Any] = {}
                for column_name, column in schema.columns.items():
                    try:
                        parsed_row[column_name] = parse_column_value(
                            raw_row.get(column_name),
                            column,
                            location=f"{path}:{line_number}:{column_name}",
                        )
                    except ProjectError:
                        raise
                    except Exception as exc:  # pragma: no cover - defensive
                        raise ProjectError(
                            f"Invalid value at {path}:{line_number}:{column_name}: {exc}"
                        ) from exc
                rows.append(parsed_row)
        return rows

    def load_all_rows(self) -> dict[str, list[dict[str, Any]]]:
        return {table: self.load_table_rows(table) for table in self.schemas}

    def validate_configuration(self) -> None:
        colliding_resources = sorted(set(self.schemas) & set(self.lenses))
        if colliding_resources:
            raise ProjectError(
                "Table and lens names must be distinct; collisions="
                f"{colliding_resources}"
            )

        missing_tables = sorted(table for table in self.schemas if table not in self.table_paths)
        extra_tables = sorted(table for table in self.table_paths if table not in self.schemas)
        if missing_tables or extra_tables:
            raise ProjectError(
                f"CSV/schema mismatch: missing_csv={missing_tables or '[]'}, extra_csv={extra_tables or '[]'}"
            )

        for table, schema in self.schemas.items():
            for column_name, column in schema.columns.items():
                if column.type == "ref":
                    referenced_table = self.resolve_table_name(column.table or "")
                    referenced_schema = self.schemas.get(referenced_table)
                    if referenced_schema is None:
                        raise ProjectError(
                            f"Column {table}.{column_name} references missing table {column.table!r}"
                        )
                    referenced_primary_key = normalize_primary_key(referenced_schema.primary_key)
                    if len(referenced_primary_key) != 1:
                        raise ProjectError(
                            f"Column {table}.{column_name} references composite-key table "
                            f"{referenced_table!r}, which is not supported"
                        )

        for lens_name, policy in self.policies.items():
            if lens_name not in self.lenses:
                raise ProjectError(f"Policy references missing lens {lens_name!r}")
            primary_table = self.resolve_table_name(policy.primary_table)
            schema = self.schema_for(primary_table)
            if normalize_primary_key(policy.primary_key) != normalize_primary_key(schema.primary_key):
                raise ProjectError(
                    f"Policy {lens_name!r} primary key {policy.primary_key!r} does not match "
                    f"schema primary key {schema.primary_key!r}"
                )
            for output_name, target in policy.editable.items():
                table_name, column_name = parse_qualified_name(target)
                target_table = self.resolve_table_name(table_name)
                if target_table != primary_table:
                    raise ProjectError(
                        f"Editable mapping {output_name!r} must target the primary table "
                        f"{primary_table!r}, got {target_table!r}"
                    )
                if column_name not in schema.columns:
                    raise ProjectError(
                        f"Editable mapping {output_name!r} targets missing column {target!r}"
                    )
            for output_name, ref_policy in policy.references.items():
                table_name, column_name = parse_qualified_name(ref_policy.write_to)
                target_table = self.resolve_table_name(table_name)
                if target_table != primary_table:
                    raise ProjectError(
                        f"Reference mapping {output_name!r} must write to the primary table "
                        f"{primary_table!r}, got {target_table!r}"
                    )
                source_column = schema.columns.get(column_name)
                if source_column is None:
                    raise ProjectError(
                        f"Reference mapping {output_name!r} targets missing column {ref_policy.write_to!r}"
                    )
                if source_column.type != "ref":
                    raise ProjectError(
                        f"Reference mapping {output_name!r} must target a ref column, got "
                        f"{primary_table}.{column_name}"
                    )
                lookup_table = self.resolve_table_name(ref_policy.lookup.table)
                lookup_schema = self.schemas.get(lookup_table)
                if lookup_schema is None:
                    raise ProjectError(
                        f"Reference mapping {output_name!r} targets missing lookup table "
                        f"{ref_policy.lookup.table!r}"
                    )
                if ref_policy.lookup.match not in lookup_schema.columns:
                    raise ProjectError(
                        f"Reference mapping {output_name!r} matches missing column "
                        f"{ref_policy.lookup.table}.{ref_policy.lookup.match}"
                    )

    def validate_rows_map(self, rows_by_table: dict[str, list[dict[str, Any]]]) -> None:
        referenced_keys: dict[str, set[tuple[str, ...]]] = {}
        for table, rows in rows_by_table.items():
            schema = self.schema_for(table)
            primary_keys = normalize_primary_key(schema.primary_key)
            seen: set[tuple[str, ...]] = set()
            for row in rows:
                serialized_key = self.primary_key_tuple(table, row)
                missing_columns = [
                    column_name
                    for column_name, value in zip(primary_keys, serialized_key, strict=True)
                    if value == ""
                ]
                if missing_columns:
                    raise ProjectError(
                        f"Row in {table!r} is missing primary key column(s) "
                        f"{', '.join(missing_columns)}"
                    )
                if serialized_key in seen:
                    raise ProjectError(
                        f"Duplicate primary key {self.primary_key_display(table, row)!r} in table {table!r}"
                    )
                seen.add(serialized_key)
            referenced_keys[table] = seen

        for table, rows in rows_by_table.items():
            schema = self.schema_for(table)
            for row in rows:
                for column_name, column in schema.columns.items():
                    value = row.get(column_name)
                    try:
                        parse_column_value(serialize_value(value, column), column)
                    except ProjectError as exc:
                        raise ProjectError(f"Invalid {table}.{column_name}: {exc}") from exc
                    if column.type == "ref" and value is not None:
                        lookup_key = serialize_value(value, column)
                        referenced_table = self.resolve_table_name(column.table or "")
                        if (lookup_key,) not in referenced_keys[referenced_table]:
                            raise ProjectError(
                                f"Invalid reference {table}.{column_name}={lookup_key!r}: "
                                f"missing row in {referenced_table!r}"
                            )

    def validate(self) -> None:
        self.validate_rows_map(self.load_all_rows())

    def write_rows_map_atomic(
        self, rows_by_table: dict[str, list[dict[str, Any]]], tables_to_write: set[str]
    ) -> None:
        temp_paths: dict[str, Path] = {}
        try:
            for table in tables_to_write:
                schema = self.schema_for(table)
                target = self.table_path(table)
                target.parent.mkdir(parents=True, exist_ok=True)
                temp_path = target.with_suffix(f"{target.suffix}.tmp-{os.getpid()}")
                temp_paths[table] = temp_path
                with temp_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle, fieldnames=list(schema.columns), lineterminator="\n"
                    )
                    writer.writeheader()
                    for row in rows_by_table[table]:
                        writer.writerow(
                            {
                                column_name: serialize_value(row.get(column_name), column)
                                for column_name, column in schema.columns.items()
                            }
                        )
            for table in tables_to_write:
                os.replace(temp_paths[table], self.table_path(table))
        finally:
            for temp_path in temp_paths.values():
                if temp_path.exists():
                    temp_path.unlink()
