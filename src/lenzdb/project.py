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
from lenzdb.models import ColumnSchema, TableSchema

DEFAULT_VIEW_PAGE_SIZE = 100


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
    table_paths: dict[str, Path]
    schemas: dict[str, TableSchema]
    lenses: dict[str, Path]
    untracked_table_paths: dict[str, list[Path]] = field(default_factory=dict)
    untracked_lens_paths: dict[str, list[Path]] = field(default_factory=dict)
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

        schemas, table_paths = cls._load_table_manifests(
            project_root,
            schema_dir,
            require_schema=not allow_incomplete,
        )
        lenses = cls._load_lens_manifests(
            project_root,
            schema_dir,
        )
        untracked_table_paths = cls._load_untracked_tables(
            project_root,
            data_dir,
            table_paths=table_paths,
            lenses=lenses,
        )
        untracked_lens_paths = cls._load_untracked_lenses(
            project_root,
            schema_dir,
            table_paths=table_paths,
            lenses=lenses,
        )
        view_page_size = cls._load_view_page_size(project_config)

        project = cls(
            root=project_root,
            schema_dir=schema_dir,
            table_paths=table_paths,
            schemas=schemas,
            lenses=lenses,
            untracked_table_paths=untracked_table_paths,
            untracked_lens_paths=untracked_lens_paths,
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
    def _load_table_manifests(
        cls,
        project_root: Path,
        schema_dir: Path,
        *,
        require_schema: bool = True,
    ) -> tuple[dict[str, TableSchema], dict[str, Path]]:
        schemas: dict[str, TableSchema] = {}
        table_paths: dict[str, Path] = {}
        if schema_dir.exists():
            for path in sorted(schema_dir.glob("*.y*ml")):
                manifest = cls._load_yaml(path)
                if not isinstance(manifest, dict):
                    raise ProjectError(f"Manifest must be a mapping: {path}")
                if manifest.get("kind") != "table":
                    continue
                schema = TableSchema.model_validate(manifest)
                name = schema.name or schema.table
                if name in schemas or name in table_paths:
                    raise ProjectError(f"Duplicate schema for table {name!r}")
                schema.name = name
                if schema.path is None:
                    schema.path = f"{name}.csv"
                schema_path = (project_root / schema.path).resolve()
                schemas[name] = schema
                table_paths[name] = schema_path
        if require_schema and not schemas:
            raise ProjectError(f"No schema files found in {schema_dir}")
        return schemas, table_paths

    @classmethod
    def _load_lens_manifests(cls, project_root: Path, schema_dir: Path) -> dict[str, Path]:
        lenses: dict[str, Path] = {}
        if not schema_dir.exists():
            return lenses
        for path in sorted(schema_dir.glob("*.y*ml")):
            manifest = cls._load_yaml(path)
            if not isinstance(manifest, dict):
                raise ProjectError(f"Manifest must be a mapping: {path}")
            if manifest.get("kind") != "lens":
                continue
            from lenzdb.models import LensManifest

            lens_manifest = LensManifest.model_validate(manifest)
            name = lens_manifest.name
            if name in lenses:
                raise ProjectError(f"Duplicate manifest for lens {name!r}")
            lenses[name] = (project_root / lens_manifest.path).resolve()
        return lenses

    @classmethod
    def _load_untracked_tables(
        cls,
        project_root: Path,
        data_dir: Path,
        *,
        table_paths: dict[str, Path],
        lenses: dict[str, Path],
    ) -> dict[str, list[Path]]:
        untracked: dict[str, list[Path]] = {}
        for source_dir in [project_root, data_dir]:
            if not source_dir.exists():
                continue
            for path in sorted(source_dir.glob("*.csv")):
                cls._record_untracked_path(
                    untracked,
                    name=path.stem,
                    path=path.resolve(),
                    tracked_paths=table_paths,
                    tracked_other_paths=lenses,
                )
        return untracked

    @classmethod
    def _load_untracked_lenses(
        cls,
        project_root: Path,
        schema_dir: Path,
        *,
        table_paths: dict[str, Path],
        lenses: dict[str, Path],
    ) -> dict[str, list[Path]]:
        untracked: dict[str, list[Path]] = {}
        lenses_dir = project_root / ".lenzdb" / "lenses"
        for source_dir in [project_root, lenses_dir]:
            if not source_dir.exists():
                continue
            for path in sorted(source_dir.glob("*.sql")):
                cls._record_untracked_path(
                    untracked,
                    name=path.stem,
                    path=path.resolve(),
                    tracked_paths=lenses,
                    tracked_other_paths=table_paths,
                )
        return untracked

    @staticmethod
    def _record_untracked_path(
        untracked: dict[str, list[Path]],
        *,
        name: str,
        path: Path,
        tracked_paths: dict[str, Path],
        tracked_other_paths: dict[str, Path],
    ) -> None:
        tracked_path = tracked_paths.get(name)
        if tracked_path is not None and path == tracked_path:
            return
        other_path = tracked_other_paths.get(name)
        if other_path is not None and path == other_path:
            return
        untracked.setdefault(name, []).append(path)

    def lens_sql(self, lens_name: str) -> str:
        resolved_lens_name = self.resolve_lens_name(lens_name)
        path = self.lenses.get(resolved_lens_name)
        if path is None:
            candidates = self.untracked_lens_paths.get(resolved_lens_name, [])
            path = candidates[0] if candidates else None
        if path is None:
            raise ProjectError(f"Unknown lens {lens_name!r}")
        return path.read_text(encoding="utf-8")

    def schema_for(self, table: str) -> TableSchema:
        resolved_table = self.resolve_table_name(table)
        try:
            return self.schemas[resolved_table]
        except KeyError as exc:
            raise ProjectError(f"Unknown table {table!r}") from exc

    def table_path(self, table: str) -> Path:
        resolved_table = self.resolve_table_name(table)
        path = self.table_paths.get(resolved_table)
        if path is not None:
            return path
        candidates = self.untracked_table_paths.get(resolved_table, [])
        if candidates:
            return candidates[0]
        raise ProjectError(f"Unknown table {table!r}")

    def lens_path(self, lens: str) -> Path:
        resolved_lens = self.resolve_lens_name(lens)
        path = self.lenses.get(resolved_lens)
        if path is not None:
            return path
        candidates = self.untracked_lens_paths.get(resolved_lens, [])
        if candidates:
            return candidates[0]
        raise ProjectError(f"Unknown lens {lens!r}")

    def resolve_table_name(self, table_name: str) -> str:
        return self._resolve_resource_name(table_name, self.table_paths, self.untracked_table_paths, "table")

    def resolve_lens_name(self, lens_name: str) -> str:
        return self._resolve_resource_name(lens_name, self.lenses, self.untracked_lens_paths, "lens")

    def resolve_resource_name(self, resource_name: str) -> tuple[str, str]:
        if resource_name in self.table_paths:
            return "table", resource_name
        if resource_name in self.lenses:
            return "lens", resource_name
        if resource_name in self.untracked_table_paths and resource_name not in self.untracked_lens_paths:
            return "table", resource_name
        if resource_name in self.untracked_lens_paths and resource_name not in self.untracked_table_paths:
            return "lens", resource_name
        if resource_name in self.untracked_table_paths and resource_name in self.untracked_lens_paths:
            raise ProjectError(f"Ambiguous resource {resource_name!r}")
        raise ProjectError(f"Unknown resource {resource_name!r}")

    def _resolve_resource_name(
        self,
        resource_name: str,
        resources: dict[str, Any],
        untracked: dict[str, list[Path]],
        resource_kind: str,
    ) -> str:
        if resource_name in resources:
            return resource_name
        if resource_name in untracked:
            return resource_name
        raise ProjectError(f"Unknown {resource_kind} {resource_name!r}")

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
