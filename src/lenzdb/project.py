"""Project discovery, schema handling, and CSV persistence."""

from __future__ import annotations

import csv
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from lenzdb.errors import MutationError, ProjectError
from lenzdb.models import ColumnSchema, LensPolicy, TableSchema


def parse_qualified_name(value: str) -> tuple[str, str]:
    parts = value.split(".", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ProjectError(f"Expected a qualified name like table.column, got {value!r}")
    return parts[0], parts[1]


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
    schemas: dict[str, TableSchema]
    lenses: dict[str, Path]
    policies: dict[str, LensPolicy]

    @classmethod
    def discover(cls, root: str | Path | None = None) -> Project:
        project_root = Path(root or Path.cwd()).resolve()
        lenz_dir = project_root / ".lenzdb"
        data_dir = lenz_dir / "data"
        schema_dir = lenz_dir / "schema"
        lenses_dir = lenz_dir / "lenses"
        policies_dir = lenz_dir / "policies"

        if not schema_dir.exists():
            raise ProjectError(f"Missing schema directory: {schema_dir}")

        schemas = cls._load_schemas(schema_dir)
        table_paths = cls._load_tables(project_root, data_dir)
        lenses = cls._load_lenses(project_root, lenses_dir)
        policies = cls._load_policies(policies_dir)

        project = cls(
            root=project_root,
            schema_dir=schema_dir,
            policies_dir=policies_dir,
            table_paths=table_paths,
            schemas=schemas,
            lenses=lenses,
            policies=policies,
        )
        project.validate_configuration()
        return project

    @staticmethod
    def _load_yaml(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    @classmethod
    def _load_schemas(cls, schema_dir: Path) -> dict[str, TableSchema]:
        schemas: dict[str, TableSchema] = {}
        for path in sorted(schema_dir.glob("*.y*ml")):
            schema = TableSchema.model_validate(cls._load_yaml(path))
            if schema.table in schemas:
                raise ProjectError(f"Duplicate schema for table {schema.table!r}")
            schemas[schema.table] = schema
        if not schemas:
            raise ProjectError(f"No schema files found in {schema_dir}")
        return schemas

    @classmethod
    def _load_tables(cls, project_root: Path, data_dir: Path) -> dict[str, Path]:
        table_paths: dict[str, Path] = {}
        for source_dir in [project_root, data_dir]:
            if not source_dir.exists():
                continue
            for path in sorted(source_dir.glob("*.csv")):
                table_name = path.stem
                if table_name in table_paths:
                    raise ProjectError(
                        f"Duplicate CSV table {table_name!r}: {table_paths[table_name]} and {path}"
                    )
                table_paths[table_name] = path
        if not table_paths:
            raise ProjectError(
                f"No CSV table files found in {project_root} or {data_dir}"
            )
        return table_paths

    @classmethod
    def _load_lenses(cls, project_root: Path, lenses_dir: Path) -> dict[str, Path]:
        lenses: dict[str, Path] = {}
        for source_dir in [project_root, lenses_dir]:
            if not source_dir.exists():
                continue
            for path in sorted(source_dir.glob("*.sql")):
                lens_name = path.stem
                if lens_name in lenses:
                    raise ProjectError(
                        f"Duplicate lens {lens_name!r}: {lenses[lens_name]} and {path}"
                    )
                lenses[lens_name] = path
        if not lenses:
            raise ProjectError(
                f"No lens SQL files found in {project_root} or {lenses_dir}"
            )
        return lenses

    @classmethod
    def _load_policies(cls, policies_dir: Path) -> dict[str, LensPolicy]:
        if not policies_dir.exists():
            return {}
        policies: dict[str, LensPolicy] = {}
        for path in sorted(policies_dir.glob("*.y*ml")):
            policy = LensPolicy.model_validate(cls._load_yaml(path))
            if policy.lens in policies:
                raise ProjectError(f"Duplicate policy for lens {policy.lens!r}")
            policies[policy.lens] = policy
        return policies

    def lens_sql(self, lens_name: str) -> str:
        path = self.lenses.get(lens_name)
        if path is None:
            raise ProjectError(f"Unknown lens {lens_name!r}")
        return path.read_text(encoding="utf-8")

    def schema_for(self, table: str) -> TableSchema:
        try:
            return self.schemas[table]
        except KeyError as exc:
            raise ProjectError(f"Unknown table {table!r}") from exc

    def policy_for(self, lens_name: str) -> LensPolicy | None:
        return self.policies.get(lens_name)

    def table_path(self, table: str) -> Path:
        try:
            return self.table_paths[table]
        except KeyError as exc:
            raise ProjectError(f"Unknown table {table!r}") from exc

    def table_headers(self, table: str) -> list[str]:
        return list(self.schema_for(table).columns)

    def blank_row(self, table: str) -> dict[str, Any]:
        schema = self.schema_for(table)
        return {column_name: None for column_name in schema.columns}

    def generate_primary_key(self, table: str) -> str:
        schema = self.schema_for(table)
        column = schema.columns[schema.primary_key]
        if column.type != "string":
            raise MutationError(
                f"Cannot auto-generate primary keys for non-string column {table}.{schema.primary_key}"
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
        missing_tables = sorted(table for table in self.schemas if table not in self.table_paths)
        extra_tables = sorted(table for table in self.table_paths if table not in self.schemas)
        if missing_tables or extra_tables:
            raise ProjectError(
                f"CSV/schema mismatch: missing_csv={missing_tables or '[]'}, extra_csv={extra_tables or '[]'}"
            )

        for table, schema in self.schemas.items():
            for column_name, column in schema.columns.items():
                if column.type == "ref":
                    referenced_schema = self.schemas.get(column.table or "")
                    if referenced_schema is None:
                        raise ProjectError(
                            f"Column {table}.{column_name} references missing table {column.table!r}"
                        )

        for lens_name, policy in self.policies.items():
            if lens_name not in self.lenses:
                raise ProjectError(f"Policy references missing lens {lens_name!r}")
            schema = self.schema_for(policy.primary_table)
            if policy.primary_key != schema.primary_key:
                raise ProjectError(
                    f"Policy {lens_name!r} primary key {policy.primary_key!r} does not match "
                    f"schema primary key {schema.primary_key!r}"
                )
            for output_name, target in policy.editable.items():
                table_name, column_name = parse_qualified_name(target)
                if table_name != policy.primary_table:
                    raise ProjectError(
                        f"Editable mapping {output_name!r} must target the primary table "
                        f"{policy.primary_table!r}, got {table_name!r}"
                    )
                if column_name not in schema.columns:
                    raise ProjectError(
                        f"Editable mapping {output_name!r} targets missing column {target!r}"
                    )
            for output_name, ref_policy in policy.references.items():
                table_name, column_name = parse_qualified_name(ref_policy.write_to)
                if table_name != policy.primary_table:
                    raise ProjectError(
                        f"Reference mapping {output_name!r} must write to the primary table "
                        f"{policy.primary_table!r}, got {table_name!r}"
                    )
                source_column = schema.columns.get(column_name)
                if source_column is None:
                    raise ProjectError(
                        f"Reference mapping {output_name!r} targets missing column {ref_policy.write_to!r}"
                    )
                if source_column.type != "ref":
                    raise ProjectError(
                        f"Reference mapping {output_name!r} must target a ref column, got "
                        f"{policy.primary_table}.{column_name}"
                    )
                lookup_schema = self.schemas.get(ref_policy.lookup.table)
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
        referenced_keys: dict[str, set[str]] = {}
        for table, rows in rows_by_table.items():
            schema = self.schema_for(table)
            primary_column = schema.columns[schema.primary_key]
            seen: set[str] = set()
            for row in rows:
                raw_key = row.get(schema.primary_key)
                serialized_key = serialize_value(raw_key, primary_column)
                if serialized_key == "":
                    raise ProjectError(
                        f"Row in {table!r} is missing primary key {schema.primary_key!r}"
                    )
                if serialized_key in seen:
                    raise ProjectError(
                        f"Duplicate primary key {serialized_key!r} in table {table!r}"
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
                        referenced_table = column.table or ""
                        if lookup_key not in referenced_keys[referenced_table]:
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
                    writer = csv.DictWriter(handle, fieldnames=list(schema.columns))
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
