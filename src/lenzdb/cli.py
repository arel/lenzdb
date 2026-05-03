"""Typer CLI for LenzDB."""

from __future__ import annotations

import csv
import inspect
import os
import shlex
import shutil
import subprocess
import sys
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer
import yaml
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from lenzdb import __version__
from lenzdb.analysis import analyze_lens, analyze_resource
from lenzdb.engine import ResourceQuery, describe_resource_view, query_lens, query_resource, query_resource_view
from lenzdb.errors import LenzError
from lenzdb.models import ColumnSchema, TableSchema
from lenzdb.planner import (
    apply_mutation_plan,
    build_mutation_plan,
    clear_recovery_files,
    diff_snapshots,
    edit_lens,
    read_snapshot_csv,
    snapshot_rows,
)
from lenzdb.project import (
    Project,
    normalize_primary_key,
)
from lenzdb.render import render_analysis, render_diff, render_plan, render_view

app = typer.Typer(help="LenzDB CLI")
PROJECT_ROOT_ENV_VAR = "LENZDB_PROJECT_ROOT"
EDITOR_ENV_VAR = "LENZDB_EDITOR"
FALLBACK_EDITOR_ENV_VAR = "EDITOR"
PAGER_ENV_VAR = "LENZDB_PAGER"
FALLBACK_PAGER_ENV_VAR = "PAGER"
COLUMNS_ENV_VAR = "LENZDB_COLUMNS"
FALLBACK_COLUMNS_ENV_VAR = "COLUMNS"
PAGE_SIZE_ENV_VAR = "LENZDB_PAGE_SIZE"
OUTPUT_FORMAT_HELP = "Output format: table, list, markdown, csv, tsv, json, ndjson, yaml, html."

ProjectOption = Annotated[
    Path | None,
    typer.Option(
        "--project",
        help=f"Project root. Defaults to ${PROJECT_ROOT_ENV_VAR}, then the current working directory.",
        exists=False,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
    ),
]


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"lnz {__version__}")
        raise typer.Exit()


@app.callback()
def cli(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=version_callback,
            help="Show the version and exit.",
            is_eager=True,
        ),
    ] = False,
) -> None:
    pass


def load_project(
    project_root: Path | None,
    *,
    validate_configuration: bool = True,
    allow_incomplete: bool = False,
) -> Project:
    selected_root: Path | str | None = project_root
    if selected_root is None:
        selected_root = os.environ.get(PROJECT_ROOT_ENV_VAR) or None
    return Project.discover(
        selected_root,
        validate_configuration=validate_configuration,
        allow_incomplete=allow_incomplete,
    )


def complete_project_resource(incomplete: str) -> list[str]:
    try:
        project = load_project(None, validate_configuration=False)
    except LenzError:
        return []
    names = {
        *project.schemas,
        *project.lenses,
        *project.untracked_table_paths,
        *project.untracked_lens_paths,
    }
    return sorted(name for name in names if name.startswith(incomplete))


def complete_lens(incomplete: str) -> list[str]:
    try:
        project = load_project(None, validate_configuration=False)
    except LenzError:
        return []
    names = {*project.lenses, *project.untracked_lens_paths}
    return sorted(name for name in names if name.startswith(incomplete))


def add_current_dir_resources(project: Project) -> None:
    current_dir = Path.cwd().resolve()
    try:
        current_dir.relative_to(project.root)
    except ValueError:
        return
    if current_dir == project.root or not current_dir.is_dir():
        return

    for path in sorted(current_dir.glob("*.csv")):
        name = path.stem
        if project.table_paths.get(name) == path.resolve():
            continue
        project.untracked_table_paths.setdefault(name, [])
        if path.resolve() not in project.untracked_table_paths[name]:
            project.untracked_table_paths[name].append(path.resolve())

    for path in sorted(current_dir.glob("*.sql")):
        name = path.stem
        if project.lenses.get(name) == path.resolve():
            continue
        project.untracked_lens_paths.setdefault(name, [])
        if path.resolve() not in project.untracked_lens_paths[name]:
            project.untracked_lens_paths[name].append(path.resolve())


def stderr_echo(message: str) -> None:
    typer.echo(message, err=True)


def referenced_lens_tables(project: Project, resource_name: str) -> list[str]:
    try:
        resolved_lens_name = project.resolve_lens_name(resource_name)
    except LenzError:
        return []

    sql = project.lens_sql(resolved_lens_name)
    try:
        expression = parse_one(sql, read="duckdb")
    except ParseError as exc:
        raise LenzError(f"Failed to parse lens {resolved_lens_name!r}: {exc}") from exc

    cte_names = {cte.alias for cte in expression.find_all(exp.CTE)}
    tables: list[str] = []
    seen: set[str] = set()
    for table_expression in expression.find_all(exp.Table):
        if not table_expression.db and table_expression.name in cte_names:
            continue
        try:
            table_name = project.resolve_table_name(
                f"{table_expression.db}.{table_expression.name}"
                if table_expression.db
                else table_expression.name
            )
        except LenzError:
            raise
        if table_name not in seen:
            seen.add(table_name)
            tables.append(table_name)
    return tables


def temporary_schema_for_table(project: Project, table_key: str) -> tuple[TableSchema, str]:
    path = project.table_path(table_key)
    header = read_csv_header(path)
    if "id" in header:
        primary_key = "id"
        note = f"Info: using temporary schema for untracked table {table_key} with primary key 'id'."
    else:
        primary_key = header[0]
        note = (
            f"Warning: using temporary schema for untracked table {table_key} with inferred "
            f"primary key {primary_key!r} from the first column."
        )
    schema = TableSchema(
        kind="table",
        name=table_key,
        path=relative_path(project, path),
        table=table_key,
        primary_key=primary_key,
        columns={
            column: ColumnSchema(type="string", immutable=(column == primary_key))
            for column in header
        },
    )
    return schema, note


def ensure_temporary_dependency_schemas(project: Project, resource_name: str) -> None:
    for table_name in referenced_lens_tables(project, resource_name):
        if table_name in project.schemas:
            continue
        schema, note = temporary_schema_for_table(project, table_name)
        project.schemas[table_name] = schema
        stderr_echo(note)


def ensure_temporary_resource_schema(project: Project, resource_name: str) -> None:
    try:
        resource_kind, resolved_name = project.resolve_resource_name(resource_name)
        if resource_kind == "lens":
            return
        if resolved_name in project.schemas:
            return
        schema, note = temporary_schema_for_table(project, resolved_name)
        project.schemas[resolved_name] = schema
        stderr_echo(note)
        return
    except LenzError:
        pass

    try:
        table_name = project.resolve_table_name(resource_name)
    except LenzError:
        return

    if table_name in project.schemas:
        return
    schema, note = temporary_schema_for_table(project, table_name)
    project.schemas[table_name] = schema
    stderr_echo(note)


def ensure_editable_resource_table(project: Project, resource_name: str) -> bool:
    try:
        resource_kind, resolved_name = project.resolve_resource_name(resource_name)
    except LenzError:
        return False

    if resource_kind == "lens":
        if resolved_name in project.lenses:
            return False
        path = project.lens_path(resolved_name)
        manifest_path = write_manifest_file(
            project,
            resolved_name,
            {
                "kind": "lens",
                "name": resolved_name,
                "path": relative_path(project, path),
            },
        )
        project.lenses[resolved_name] = path
        stderr_echo(f"Info: auto-added untracked lens {resolved_name}.")
        stderr_echo(f"Info: wrote manifest {relative_path(project, manifest_path)}.")
        return True

    if resolved_name in project.schemas:
        return False

    path = project.table_path(resolved_name)
    header = read_csv_header(path)
    if "id" not in header:
        raise LenzError(
            f"{resource_name} is an untracked table without a default primary key column 'id'. "
            "Run lnz add <table> --primary-key <column> and retry."
        )
    manifest_path = write_table_manifest(project, resolved_name, path, ["id"])
    stderr_echo(f"Info: auto-added untracked table {resolved_name} with primary key 'id'.")
    stderr_echo(f"Info: wrote manifest {relative_path(project, manifest_path)}.")
    return True


def ensure_editable_dependency_tables(project: Project, resource_name: str) -> None:
    blocked_tables: list[str] = []

    for table_name in referenced_lens_tables(project, resource_name):
        if table_name in project.schemas:
            continue
        path = project.table_path(table_name)
        header = read_csv_header(path)
        if "id" not in header:
            blocked_tables.append(table_name)
            continue
        try:
            schema_path = write_table_manifest(project, table_name, path, ["id"])
        except LenzError as exc:
            raise LenzError(
                f"Cannot auto-add untracked table {table_name!r} with default primary key 'id': {exc}"
            ) from exc
        stderr_echo(f"Info: auto-added untracked table {table_name} with primary key 'id'.")
        stderr_echo(f"Info: wrote manifest {relative_path(project, schema_path)}.")

    if blocked_tables:
        blocked = ", ".join(blocked_tables)
        raise LenzError(
            f"{resource_name} depends on untracked tables without a default primary key column 'id': "
            f"{blocked}. Run lnz add <table> --primary-key <column> for each table, then retry."
        )


def relative_path(project: Project, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(project.root))
    except ValueError:
        return str(path)


def table_row_state(project: Project, name: str, path: Path, index: int) -> str:
    if name in project.schemas:
        tracked = project.table_paths.get(name)
        return "added" if tracked == path else "shadowed"
    return "untracked" if index == 0 else "duplicate"


def lens_row_state(project: Project, name: str, path: Path, index: int) -> str:
    if name in project.lenses:
        tracked = project.lenses.get(name)
        return "added" if tracked == path else "shadowed"
    return "untracked" if index == 0 else "duplicate"


def header_error_for_table(project: Project, table_name: str) -> str | None:
    if table_name not in project.schemas:
        return None
    schema = project.schema_for(table_name)
    path = project.table_path(table_name)
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or []
    except Exception as exc:
        return str(exc)
    expected = list(schema.columns)
    missing = [name for name in expected if name not in headers]
    extra = [name for name in headers if name not in schema.columns]
    if missing or extra:
        return f"header_mismatch: missing={missing or '[]'}, extra={extra or '[]'}"
    return None


def state_for_table(project: Project, table_name: str) -> str:
    if table_name in project.schemas:
        return "error" if header_error_for_table(project, table_name) else "added"
    if table_name in project.untracked_table_paths:
        return "untracked"
    return "missing"


def check_for_table(project: Project, table_name: str) -> str:
    state = state_for_table(project, table_name)
    if state != "added":
        if state == "error":
            return header_error_for_table(project, table_name) or "error"
        return state
    try:
        rows = project.load_table_rows(table_name)
        schema = project.schema_for(table_name)
        primary_keys = normalize_primary_key(schema.primary_key)
        seen: set[tuple[str, ...]] = set()
        for row in rows:
            key = project.primary_key_tuple(table_name, row)
            missing_columns = [
                column_name
                for column_name, value in zip(primary_keys, key, strict=True)
                if value == ""
            ]
            if missing_columns:
                return f"missing_pk: {', '.join(missing_columns)}"
            if key in seen:
                return f"duplicate_pk: {project.primary_key_display(table_name, row)}"
            seen.add(key)
        return "ok"
    except Exception as exc:
        return str(exc)


def state_for_lens(project: Project, lens_name: str) -> str:
    if lens_name in project.lenses:
        return "added"
    if lens_name in project.untracked_lens_paths:
        return "untracked"
    return "missing"


def check_for_lens(project: Project, lens_name: str) -> str:
    if state_for_lens(project, lens_name) != "added":
        return state_for_lens(project, lens_name)
    try:
        analyze_lens(project, lens_name)
        query_lens(project, lens_name)
        return "ok"
    except Exception as exc:
        return str(exc)


def build_list_rows(project: Project, check: bool) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name, path in sorted(project.table_paths.items()):
        row = {
            "kind": "table",
            "name": name,
            "path": relative_path(project, path),
            "state": "added",
        }
        if check:
            row["check"] = check_for_table(project, name)
        rows.append(row)

        for index, untracked_path in enumerate(project.untracked_table_paths.get(name, [])):
            rows.append(
                {
                    "kind": "table",
                    "name": name,
                    "path": relative_path(project, untracked_path),
                    "state": "shadowed" if index == 0 else "duplicate",
                    **({"check": "shadowed"} if check else {}),
                }
            )

    for name, paths in sorted(project.untracked_table_paths.items()):
        if name in project.table_paths:
            continue
        for index, path in enumerate(paths):
            row = {
                "kind": "table",
                "name": name,
                "path": relative_path(project, path),
                "state": "untracked" if index == 0 else "duplicate",
            }
            if check:
                row["check"] = check_for_table(project, name)
            rows.append(row)

    for name, path in sorted(project.lenses.items()):
        row = {
            "kind": "lens",
            "name": name,
            "path": relative_path(project, path),
            "state": "added",
        }
        if check:
            row["check"] = check_for_lens(project, name)
        rows.append(row)

        for index, untracked_path in enumerate(project.untracked_lens_paths.get(name, [])):
            rows.append(
                {
                    "kind": "lens",
                    "name": name,
                    "path": relative_path(project, untracked_path),
                    "state": "shadowed" if index == 0 else "duplicate",
                    **({"check": "shadowed"} if check else {}),
                }
            )

    for name, paths in sorted(project.untracked_lens_paths.items()):
        if name in project.lenses:
            continue
        for index, path in enumerate(paths):
            row = {
                "kind": "lens",
                "name": name,
                "path": relative_path(project, path),
                "state": "untracked" if index == 0 else "duplicate",
            }
            if check:
                row["check"] = check_for_lens(project, name)
            rows.append(row)
    return rows


def handle_errors(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except LenzError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    wrapper.__signature__ = inspect.signature(function, eval_str=True)
    return wrapper


def parse_comma_list(value: str | None, *, option_name: str) -> list[str] | None:
    if value is None:
        return None
    items = [item.strip() for item in value.split(",")]
    if not items or any(not item for item in items):
        raise LenzError(f"{option_name} must be a comma-separated list without empty items")
    return items


def validate_non_negative(value: int | None, *, option_name: str) -> None:
    if value is not None and value < 0:
        raise LenzError(f"{option_name} must be zero or greater")


def validate_positive(value: int | None, *, option_name: str) -> None:
    if value is not None and value < 1:
        raise LenzError(f"{option_name} must be one or greater")


def parse_positive_env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise LenzError(f"${name} must be a positive integer") from exc
    if parsed < 1:
        raise LenzError(f"${name} must be a positive integer")
    return parsed


def parse_page_size_env() -> int | None:
    value = os.environ.get(PAGE_SIZE_ENV_VAR)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise LenzError(f"${PAGE_SIZE_ENV_VAR} must be an integer") from exc
    return parsed


def resolve_page_size(
    project: Project, explicit_page: int | None, explicit_page_size: int | None
) -> tuple[int | None, int | None]:
    if explicit_page_size is not None:
        return explicit_page or 1, explicit_page_size
    env_page_size = parse_page_size_env()
    if env_page_size is not None:
        if env_page_size > 0:
            return explicit_page or 1, env_page_size
        if explicit_page is None:
            return None, None
    if explicit_page is not None:
        return explicit_page, project.view_page_size
    return None, None


def output_width() -> int:
    for env_var in [COLUMNS_ENV_VAR, FALLBACK_COLUMNS_ENV_VAR]:
        value = parse_positive_env_int(env_var)
        if value is not None:
            return value
    return shutil.get_terminal_size(fallback=(120, 24)).columns


def selected_pager() -> str | None:
    return os.environ.get(PAGER_ENV_VAR) or os.environ.get(FALLBACK_PAGER_ENV_VAR)


def should_page_view_output(output_format: str) -> bool:
    return output_format == "table" and sys.stdout.isatty() and selected_pager() is not None


def page_output(text: str) -> None:
    pager = selected_pager()
    if pager is None:
        typer.echo(text, nl=False)
        return
    command = shlex.split(pager)
    if not command:
        raise LenzError("Pager command must not be empty")
    try:
        subprocess.run(command, input=text, text=True, check=False)
    except FileNotFoundError as exc:
        raise LenzError(f"Pager command not found: {pager!r}") from exc


def write_view_output(text: str, output_format: str) -> None:
    if should_page_view_output(output_format):
        page_output(text)
        return
    typer.echo(text, nl=False)


def build_view_query(
    project: Project,
    *,
    columns: str | None = None,
    distinct: str | None = None,
    where: str | None = None,
    order: str | None = None,
    count: bool = False,
    limit: int | None = None,
    offset: int | None = None,
    page: int | None = None,
    page_size: int | None = None,
    sql: str | None = None,
) -> ResourceQuery:
    selected_columns = parse_comma_list(columns, option_name="--columns")
    distinct_columns = parse_comma_list(distinct, option_name="--distinct")
    order_columns = parse_comma_list(order, option_name="--order")
    if sql is not None and not sql.strip():
        raise LenzError("--sql must not be empty")
    validate_positive(limit, option_name="--limit")
    validate_non_negative(offset, option_name="--offset")
    validate_positive(page, option_name="--page")
    validate_positive(page_size, option_name="--page-size")

    convenience_options = [
        option is not None
        for option in [
            selected_columns,
            distinct_columns,
            where,
            order_columns,
            limit,
            offset,
            page,
            page_size,
        ]
    ]
    if sql is not None and (count or any(convenience_options)):
        raise LenzError("--sql cannot be combined with view convenience options")
    if count and any(
        option is not None
        for option in [
            selected_columns,
            distinct_columns,
            order_columns,
            limit,
            offset,
            page,
            page_size,
        ]
    ):
        raise LenzError("--count may only be combined with --filter")
    if distinct_columns is not None and selected_columns is not None:
        raise LenzError("--distinct cannot be combined with --columns")
    if page is not None and (limit is not None or offset is not None):
        raise LenzError("--page cannot be combined with --limit or --offset")

    effective_limit = limit
    effective_offset = offset
    effective_page, effective_page_size = resolve_page_size(project, page, page_size)
    if effective_page is not None and effective_page_size is not None:
        effective_limit = effective_page_size
        effective_offset = (effective_page - 1) * effective_page_size

    return ResourceQuery(
        columns=selected_columns,
        distinct=distinct_columns,
        where=where,
        order=order_columns,
        limit=effective_limit,
        offset=effective_offset,
        count=count,
        sql=sql,
    )


def is_identity_query(query: ResourceQuery) -> bool:
    return query == ResourceQuery()


def read_csv_header(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
    except OSError as exc:
        raise LenzError(f"Cannot read CSV file {path}: {exc}") from exc
    if not header:
        raise LenzError(f"CSV file has no header row: {path}")
    if any(not column for column in header):
        raise LenzError(f"CSV header must not contain empty column names: {path}")
    duplicates = sorted({column for column in header if header.count(column) > 1})
    if duplicates:
        raise LenzError(f"CSV header contains duplicate column names: {', '.join(duplicates)}")
    return header


def choose_primary_key(header: list[str], primary_key: str | None) -> list[str]:
    if primary_key is not None:
        selected = parse_comma_list(primary_key, option_name="--primary-key") or []
        missing = [column for column in selected if column not in header]
        if missing:
            raise LenzError(
                f"Primary key column(s) {', '.join(missing)!r} are not in the CSV header. "
                f"Available columns: {', '.join(header)}"
            )
        return selected
    if "id" in header:
        return ["id"]
    if sys.stdin.isatty():
        selected = typer.prompt("Primary key column", default=header[0])
        if selected not in header:
            raise LenzError(
                f"Primary key column {selected!r} is not in the CSV header. "
                f"Available columns: {', '.join(header)}"
            )
        return [selected]
    raise LenzError("CSV has no 'id' column. Pass --primary-key to choose one.")


def validate_csv_primary_key(path: Path, primary_key: list[str]) -> None:
    seen: set[tuple[str, ...]] = set()
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for line_number, row in enumerate(reader, start=2):
                value = tuple(row.get(column, "") for column in primary_key)
                missing_columns = [
                    column
                    for column, column_value in zip(primary_key, value, strict=True)
                    if column_value == ""
                ]
                if missing_columns:
                    raise LenzError(
                        f"CSV primary key column(s) {', '.join(missing_columns)!r} "
                        f"are empty at {path}:{line_number}"
                    )
                if value in seen:
                    raise LenzError(
                        f"CSV primary key {primary_key!r} has duplicate value {' | '.join(value)!r}"
                    )
                seen.add(value)
    except OSError as exc:
        raise LenzError(f"Cannot read CSV file {path}: {exc}") from exc


def project_relative_path(project: Project, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project.root))
    except ValueError as exc:
        raise LenzError(f"CSV path must be inside the project root: {path}") from exc


def write_manifest_file(project: Project, name: str, document: dict[str, object]) -> Path:
    path = project.schema_dir / f"{name}.yaml"
    if path.exists():
        raise LenzError(f"Manifest file already exists: {path}")
    project.schema_dir.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(document, handle, sort_keys=False)
    return path


def write_table_manifest(project: Project, name: str, path: Path, primary_key: list[str]) -> Path:
    header = read_csv_header(path)
    validate_csv_primary_key(path, primary_key)
    document = {
        "kind": "table",
        "name": name,
        "path": project_relative_path(project, path),
        "table": name,
        "primary_key": primary_key[0] if len(primary_key) == 1 else primary_key,
        "columns": {
            column: {"type": "string", **({"immutable": True} if column in primary_key else {})}
            for column in header
        },
    }
    manifest_path = write_manifest_file(project, name, document)
    project.schemas[name] = TableSchema.model_validate(document)
    project.table_paths[name] = path.resolve()
    return manifest_path


def write_lens_manifest(project: Project, name: str, path: Path) -> Path:
    document = {
        "kind": "lens",
        "name": name,
        "path": project_relative_path(project, path),
    }
    manifest_path = write_manifest_file(project, name, document)
    project.lenses[name] = path.resolve()
    return manifest_path


def resolve_add_resource(project: Project, target: str) -> tuple[str, str, Path]:
    target_path = Path(target)
    if target_path.suffix in {".csv", ".sql"} or target_path.exists():
        if target_path.is_absolute():
            path = target_path.resolve()
        else:
            cwd_path = (Path.cwd() / target_path).resolve()
            project_path = (project.root / target_path).resolve()
            path = cwd_path if cwd_path.exists() else project_path
        if not path.exists():
            raise LenzError(f"Resource file does not exist: {path}")
        if not path.is_file() or path.suffix not in {".csv", ".sql"}:
            raise LenzError(f"Path must point to a .csv or .sql file: {path}")
        return ("table" if path.suffix == ".csv" else "lens"), path.stem, path

    if target in project.schemas or target in project.table_paths:
        return "table", target, project.table_path(target)
    if target in project.lenses or target in project.untracked_lens_paths:
        return "lens", target, project.lens_path(target)
    if target in project.untracked_table_paths:
        return "table", target, project.table_path(target)
    raise LenzError(f"Unknown untracked resource {target!r}; pass a file path instead")


@app.command()
@handle_errors
def add(
    target: Annotated[
        str,
        typer.Argument(help="Untracked resource name or path to a CSV/SQL file to add."),
    ],
    primary_key: Annotated[
        str | None,
        typer.Option(
            "--primary-key",
            "-p",
            help="Primary key column. Defaults to 'id' when present; otherwise prompts on a TTY.",
        ),
    ] = None,
    project: ProjectOption = None,
) -> None:
    project_instance = load_project(
        project,
        validate_configuration=False,
        allow_incomplete=True,
    )
    add_current_dir_resources(project_instance)
    kind, name, path = resolve_add_resource(project_instance, target)
    if kind == "table":
        if name in project_instance.schemas:
            raise LenzError(f"Table {name!r} already has a manifest")
        header = read_csv_header(path)
        selected_primary_key = choose_primary_key(header, primary_key)
        manifest_path = write_table_manifest(project_instance, name, path, selected_primary_key)
        typer.echo(f"Added table {name}")
        typer.echo(f"Manifest: {relative_path(project_instance, manifest_path)}")
        return

    if name in project_instance.lenses:
        raise LenzError(f"Lens {name!r} already has a manifest")
    if primary_key is not None:
        raise LenzError("--primary-key is only valid for CSV tables")
    manifest_path = write_lens_manifest(project_instance, name, path)
    typer.echo(f"Added lens {name}")
    typer.echo(f"Manifest: {relative_path(project_instance, manifest_path)}")


@app.command()
@handle_errors
def view(
    name: Annotated[
        str,
        typer.Argument(
            help="Lens or table name to view.",
            autocompletion=complete_project_resource,
        ),
    ],
    output_format: str = typer.Option(
        "table",
        "--format",
        help=OUTPUT_FORMAT_HELP,
        case_sensitive=False,
        show_default=True,
    ),
    columns: Annotated[
        str | None,
        typer.Option(
            "--columns",
            help="Comma-separated columns to show, e.g. id,title,status.",
        ),
    ] = None,
    distinct: Annotated[
        str | None,
        typer.Option(
            "--distinct",
            help="Comma-separated columns whose distinct values should be returned.",
        ),
    ] = None,
    where: Annotated[
        str | None,
        typer.Option(
            "--filter",
            help="SQL WHERE fragment evaluated against the resource.",
        ),
    ] = None,
    order: Annotated[
        str | None,
        typer.Option(
            "--order",
            help="Comma-separated order columns. Prefix with '-' for descending.",
        ),
    ] = None,
    count: Annotated[
        bool,
        typer.Option(
            "--count",
            help="Return the row count. May be combined with --filter.",
        ),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Maximum number of rows to return."),
    ] = None,
    offset: Annotated[
        int | None,
        typer.Option("--offset", help="Number of rows to skip."),
    ] = None,
    page: Annotated[
        int | None,
        typer.Option(
            "--page",
            help=(
                "One-based page number using the configured page size. Defaults to page 1 "
                "when pagination is enabled by --page-size or $LENZDB_PAGE_SIZE."
            ),
        ),
    ] = None,
    page_size: Annotated[
        int | None,
        typer.Option(
            "--page-size",
            help="Rows per page. Also enables pagination and defaults to page 1.",
        ),
    ] = None,
    sql: Annotated[
        str | None,
        typer.Option(
            "--sql",
            help="SQL query over the selected resource, exposed as table name 'resource'.",
        ),
    ] = None,
    project: ProjectOption = None,
) -> None:
    project_instance = load_project(
        project,
        validate_configuration=False,
        allow_incomplete=True,
    )
    add_current_dir_resources(project_instance)
    ensure_temporary_resource_schema(project_instance, name)
    ensure_temporary_dependency_schemas(project_instance, name)
    query = build_view_query(
        project_instance,
        columns=columns,
        distinct=distinct,
        where=where,
        order=order,
        count=count,
        limit=limit,
        offset=offset,
        page=page,
        page_size=page_size,
        sql=sql,
    )
    result = query_resource_view(project_instance, name, query)
    write_view_output(
        render_view(result.columns, result.rows, output_format, width=output_width()),
        output_format,
    )


@app.command()
@handle_errors
def describe(
    name: Annotated[
        str,
        typer.Argument(
            help="Lens or table name to describe.",
            autocompletion=complete_project_resource,
        ),
    ],
    output_format: str = typer.Option(
        "table",
        "--format",
        help=OUTPUT_FORMAT_HELP,
        case_sensitive=False,
        show_default=True,
    ),
    columns: Annotated[
        str | None,
        typer.Option(
            "--columns",
            help="Comma-separated columns to describe, e.g. id,title,status.",
        ),
    ] = None,
    distinct: Annotated[
        str | None,
        typer.Option(
            "--distinct",
            help="Comma-separated columns whose distinct values should be described.",
        ),
    ] = None,
    where: Annotated[
        str | None,
        typer.Option(
            "--filter",
            help="SQL WHERE fragment evaluated against the resource.",
        ),
    ] = None,
    order: Annotated[
        str | None,
        typer.Option(
            "--order",
            help="Comma-separated order columns. Prefix with '-' for descending.",
        ),
    ] = None,
    count: Annotated[
        bool,
        typer.Option(
            "--count",
            help="Describe the row count shape. May be combined with --filter.",
        ),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Maximum number of rows to consider."),
    ] = None,
    offset: Annotated[
        int | None,
        typer.Option("--offset", help="Number of rows to skip."),
    ] = None,
    page: Annotated[
        int | None,
        typer.Option(
            "--page",
            help=(
                "One-based page number using the configured page size. Defaults to page 1 "
                "when pagination is enabled by --page-size or $LENZDB_PAGE_SIZE."
            ),
        ),
    ] = None,
    page_size: Annotated[
        int | None,
        typer.Option(
            "--page-size",
            help="Rows per page. Also enables pagination and defaults to page 1.",
        ),
    ] = None,
    sql: Annotated[
        str | None,
        typer.Option(
            "--sql",
            help="SQL query over the selected resource, exposed as table name 'resource'.",
        ),
    ] = None,
    project: ProjectOption = None,
) -> None:
    project_instance = load_project(
        project,
        validate_configuration=False,
        allow_incomplete=True,
    )
    add_current_dir_resources(project_instance)
    ensure_temporary_resource_schema(project_instance, name)
    ensure_temporary_dependency_schemas(project_instance, name)
    query = build_view_query(
        project_instance,
        columns=columns,
        distinct=distinct,
        where=where,
        order=order,
        count=count,
        limit=limit,
        offset=offset,
        page=page,
        page_size=page_size,
        sql=sql,
    )
    result = describe_resource_view(project_instance, name, query)
    write_view_output(
        render_view(result.columns, result.rows, output_format, width=output_width()),
        output_format,
    )


@app.command()
@handle_errors
def check(project: ProjectOption = None) -> None:
    project_instance = load_project(project)
    project_instance.validate()
    lenses = []
    for lens_name in sorted(project_instance.lenses):
        analysis = analyze_lens(project_instance, lens_name)
        query_lens(project_instance, lens_name)
        lenses.append(
            f"{lens_name}: writable={'yes' if analysis.writable else 'no'} "
            f"({len(analysis.columns)} columns)"
        )
    typer.echo("Project check passed.")
    for line in lenses:
        typer.echo(line)


@app.command(name="list")
@handle_errors
def list_resources(
    output_format: str = typer.Option(
        "table",
        "--format",
        help=OUTPUT_FORMAT_HELP,
        case_sensitive=False,
        show_default=True,
    ),
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            "-c",
            help="Run validation/query checks and include a check column.",
        ),
    ] = False,
    project: ProjectOption = None,
) -> None:
    project_instance = load_project(
        project,
        validate_configuration=False,
        allow_incomplete=True,
    )
    add_current_dir_resources(project_instance)
    columns = ["kind", "name", "path", "state"]
    if check:
        columns.append("check")
    rows = build_list_rows(project_instance, check)
    typer.echo(render_view(columns, rows, output_format, width=output_width()), nl=False)


@app.command()
@handle_errors
def explain(
    resource_name: Annotated[
        str,
        typer.Argument(help="Table or lens name to explain.", autocompletion=complete_project_resource),
    ],
    project: ProjectOption = None,
) -> None:
    project_instance = load_project(project, validate_configuration=False)
    analysis = analyze_resource(project_instance, resource_name)
    typer.echo(render_analysis(analysis), nl=False)


@app.command()
@handle_errors
def diff(
    resource_name: Annotated[
        str,
        typer.Argument(help="Table or lens name to diff.", autocompletion=complete_project_resource),
    ],
    edited_csv: Path,
    project: ProjectOption = None,
) -> None:
    project_instance = load_project(project, validate_configuration=False)
    result = query_resource(project_instance, resource_name)
    analysis = analyze_resource(project_instance, resource_name)
    current_rows = snapshot_rows(result.columns, result.rows)
    edited_rows = read_snapshot_csv(edited_csv, result.columns)
    diff_entries = diff_snapshots(
        current_rows,
        edited_rows,
        analysis.primary_key_outputs,
        result.columns,
    )
    typer.echo(render_diff(diff_entries), nl=False)


@app.command()
@handle_errors
def plan(
    resource_name: Annotated[
        str,
        typer.Argument(help="Table or lens name to plan.", autocompletion=complete_project_resource),
    ],
    edited_csv: Path,
    project: ProjectOption = None,
) -> None:
    project_instance = load_project(project, validate_configuration=False)
    mutation_plan = build_mutation_plan(project_instance, resource_name, edited_csv)
    typer.echo(render_plan(mutation_plan), nl=False)


@app.command()
@handle_errors
def apply(
    resource_name: Annotated[
        str,
        typer.Argument(help="Table or lens name to apply.", autocompletion=complete_project_resource),
    ],
    edited_csv: Path,
    project: ProjectOption = None,
) -> None:
    project_instance = load_project(project, validate_configuration=False)
    mutation_plan = build_mutation_plan(project_instance, resource_name, edited_csv)
    typer.echo(render_plan(mutation_plan), nl=False)
    if not mutation_plan.has_changes:
        typer.echo("No changes to apply.")
        return
    apply_mutation_plan(project_instance, mutation_plan)
    typer.echo("Changes applied.")


@app.command()
@handle_errors
def edit(
    resource_name: Annotated[
        str,
        typer.Argument(help="Table or lens name to edit.", autocompletion=complete_project_resource),
    ],
    editor: Annotated[
        str | None,
        typer.Option("--editor", help=f"Override ${EDITOR_ENV_VAR} and ${FALLBACK_EDITOR_ENV_VAR}."),
    ] = None,
    discard_recovery: Annotated[
        bool,
        typer.Option(
            "--discard-recovery",
            help="Ignore any preserved failed edit and start from current data.",
        ),
    ] = False,
    columns: Annotated[
        str | None,
        typer.Option(
            "--columns",
            help="Comma-separated columns to edit, e.g. id,title,status.",
        ),
    ] = None,
    where: Annotated[
        str | None,
        typer.Option(
            "--filter",
            help="SQL WHERE fragment evaluated against the resource.",
        ),
    ] = None,
    order: Annotated[
        str | None,
        typer.Option(
            "--order",
            help="Comma-separated order columns. Prefix with '-' for descending.",
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Maximum number of rows to edit."),
    ] = None,
    offset: Annotated[
        int | None,
        typer.Option("--offset", help="Number of rows to skip."),
    ] = None,
    page: Annotated[
        int | None,
        typer.Option(
            "--page",
            help=(
                "One-based page number using the configured page size. Defaults to page 1 "
                "when pagination is enabled by --page-size or $LENZDB_PAGE_SIZE."
            ),
        ),
    ] = None,
    page_size: Annotated[
        int | None,
        typer.Option(
            "--page-size",
            help="Rows per page. Also enables pagination and defaults to page 1.",
        ),
    ] = None,
    project: ProjectOption = None,
) -> None:
    project_instance = load_project(
        project,
        validate_configuration=False,
        allow_incomplete=True,
    )
    add_current_dir_resources(project_instance)
    auto_added_resource = ensure_editable_resource_table(project_instance, resource_name)
    ensure_temporary_resource_schema(project_instance, resource_name)
    ensure_editable_dependency_tables(project_instance, resource_name)
    if auto_added_resource or referenced_lens_tables(project_instance, resource_name):
        project_instance = load_project(
            project,
            validate_configuration=False,
            allow_incomplete=True,
        )
        add_current_dir_resources(project_instance)
    query = build_view_query(
        project_instance,
        columns=columns,
        where=where,
        order=order,
        limit=limit,
        offset=offset,
        page=page,
        page_size=page_size,
    )
    edit_query = None if is_identity_query(query) else query
    editor_command = (
        editor
        or os.environ.get(EDITOR_ENV_VAR)
        or os.environ.get(FALLBACK_EDITOR_ENV_VAR)
    )
    if not editor_command:
        raise LenzError(
            f"No editor configured. Set ${EDITOR_ENV_VAR}, set ${FALLBACK_EDITOR_ENV_VAR}, "
            "or pass --editor."
        )

    edit_result = edit_lens(
        project_instance,
        resource_name,
        editor_command,
        discard_recovery=discard_recovery,
        query=edit_query,
    )
    if edit_result.recovered_from is not None:
        typer.echo(f"Recovered previous failed edit: {edit_result.recovered_from}")
    mutation_plan = edit_result.plan
    typer.echo(render_plan(mutation_plan), nl=False)
    if not mutation_plan.has_changes:
        clear_recovery_files(project_instance, resource_name, query=edit_query)
        typer.echo("No changes to apply.")
        return
    try:
        apply_mutation_plan(project_instance, mutation_plan)
    except LenzError as exc:
        raise LenzError(f"{exc}\nEdited file preserved at: {edit_result.recovery_path}") from exc
    clear_recovery_files(project_instance, resource_name, query=edit_query)
    typer.echo("Changes applied.")


def main() -> None:
    app()
