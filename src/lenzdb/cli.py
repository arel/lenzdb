"""Typer CLI for LenzDB."""

from __future__ import annotations

import inspect
import os
import csv
import sys
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer
import yaml

from lenzdb.analysis import analyze_lens, analyze_resource
from lenzdb.engine import ResourceQuery, query_lens, query_resource, query_resource_view
from lenzdb.errors import LenzError
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
    DEFAULT_NAMESPACE,
    TABLES_CONFIG_KEY,
    Project,
    parse_namespaced_name,
    resource_key,
    serialize_value,
    split_resource_key,
)
from lenzdb.render import render_analysis, render_diff, render_plan, render_view

app = typer.Typer(help="LenzDB CLI")
PROJECT_ROOT_ENV_VAR = "LENZDB_PROJECT_ROOT"

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


def load_project(project_root: Path | None, *, validate_configuration: bool = True) -> Project:
    selected_root: Path | str | None = project_root
    if selected_root is None:
        selected_root = os.environ.get(PROJECT_ROOT_ENV_VAR) or None
    return Project.discover(selected_root, validate_configuration=validate_configuration)


def complete_project_resource(incomplete: str) -> list[str]:
    try:
        project = load_project(None, validate_configuration=False)
    except LenzError:
        return []
    names = {*project.lenses, *project.schemas}
    names.update(split_resource_key(name)[1] for name in list(names))
    return sorted(name for name in names if name.startswith(incomplete))


def complete_lens(incomplete: str) -> list[str]:
    try:
        project = load_project(None, validate_configuration=False)
    except LenzError:
        return []
    names = set(project.lenses)
    names.update(split_resource_key(name)[1] for name in list(names))
    return sorted(name for name in names if name.startswith(incomplete))


def project_namespaces(project: Project) -> list[str]:
    namespaces = {split_resource_key(key)[0] for key in project.schemas}
    namespaces.update(split_resource_key(key)[0] for key in project.table_paths)
    namespaces.update(split_resource_key(key)[0] for key in project.lenses)
    return sorted(namespaces)


def relative_path(project: Project, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(project.root))
    except ValueError:
        return str(path)


def state_for_namespace(project: Project, namespace: str) -> str:
    has_table = any(split_resource_key(key)[0] == namespace for key in project.schemas)
    has_table_path = any(split_resource_key(key)[0] == namespace for key in project.table_paths)
    has_lens = any(split_resource_key(key)[0] == namespace for key in project.lenses)
    return "added" if has_table or has_table_path or has_lens else "missing"


def header_error_for_table(project: Project, table_name: str) -> str | None:
    if table_name not in project.schemas or table_name not in project.table_paths:
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
    has_schema = table_name in project.schemas
    has_csv = table_name in project.table_paths
    if has_schema and has_csv:
        return "error" if header_error_for_table(project, table_name) else "added"
    if has_schema:
        return "missing"
    return "untracked"


def check_for_namespace(project: Project, namespace: str) -> str:
    return "ok" if state_for_namespace(project, namespace) == "added" else "missing"


def check_for_table(project: Project, table_name: str) -> str:
    state = state_for_table(project, table_name)
    if state != "added":
        if state == "error":
            return header_error_for_table(project, table_name) or "error"
        return state
    try:
        rows = project.load_table_rows(table_name)
        schema = project.schema_for(table_name)
        primary_column = schema.columns[schema.primary_key]
        seen: set[str] = set()
        for row in rows:
            key = serialize_value(row.get(schema.primary_key), primary_column)
            if key == "":
                return f"missing_pk: {schema.primary_key}"
            if key in seen:
                return f"duplicate_pk: {key}"
            seen.add(key)
        return "ok"
    except Exception as exc:
        return str(exc)


def state_for_lens(project: Project, lens_name: str) -> str:
    return "added" if project.lenses.get(lens_name) else "missing"


def check_for_lens(project: Project, lens_name: str) -> str:
    try:
        analyze_lens(project, lens_name)
        query_lens(project, lens_name)
        return "ok"
    except Exception as exc:
        return str(exc)


def build_list_rows(project: Project, check: bool) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for namespace in project_namespaces(project):
        row = {
            "kind": "namespace",
            "namespace": namespace,
            "name": namespace,
            "path": "",
            "state": state_for_namespace(project, namespace),
        }
        if check:
            row["check"] = check_for_namespace(project, namespace)
        rows.append(row)

    for table_name in sorted(set(project.schemas) | set(project.table_paths)):
        path = project.table_paths.get(table_name)
        namespace, name = split_resource_key(table_name)
        row = {
            "kind": "table",
            "namespace": namespace,
            "name": name,
            "path": relative_path(project, path),
            "state": state_for_table(project, table_name),
        }
        if check:
            row["check"] = check_for_table(project, table_name)
        rows.append(row)

    for lens_name, path in sorted(project.lenses.items()):
        namespace, name = split_resource_key(lens_name)
        row = {
            "kind": "lens",
            "namespace": namespace,
            "name": name,
            "path": relative_path(project, path),
            "state": state_for_lens(project, lens_name),
        }
        if check:
            row["check"] = check_for_lens(project, lens_name)
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


def choose_primary_key(header: list[str], primary_key: str | None) -> str:
    if primary_key is not None:
        if primary_key not in header:
            raise LenzError(
                f"Primary key column {primary_key!r} is not in the CSV header. "
                f"Available columns: {', '.join(header)}"
            )
        return primary_key
    if "id" in header:
        return "id"
    if sys.stdin.isatty():
        selected = typer.prompt("Primary key column", default=header[0])
        if selected not in header:
            raise LenzError(
                f"Primary key column {selected!r} is not in the CSV header. "
                f"Available columns: {', '.join(header)}"
            )
        return selected
    raise LenzError("CSV has no 'id' column. Pass --primary-key to choose one.")


def validate_csv_primary_key(path: Path, primary_key: str) -> None:
    seen: set[str] = set()
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for line_number, row in enumerate(reader, start=2):
                value = row.get(primary_key, "")
                if value == "":
                    raise LenzError(
                        f"CSV primary key {primary_key!r} is empty at {path}:{line_number}"
                    )
                if value in seen:
                    raise LenzError(
                        f"CSV primary key {primary_key!r} has duplicate value {value!r}"
                    )
                seen.add(value)
    except OSError as exc:
        raise LenzError(f"Cannot read CSV file {path}: {exc}") from exc


def project_relative_path(project: Project, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project.root))
    except ValueError as exc:
        raise LenzError(f"CSV path must be inside the project root: {path}") from exc


def is_auto_discovered_table_path(project: Project, path: Path) -> bool:
    resolved = path.resolve()
    return (
        resolved.parent == project.root
        or resolved.parent == project.root / ".lenzdb" / "data"
    )


def resolve_add_csv(project: Project, target: str) -> tuple[str, Path]:
    target_path = Path(target)
    if target_path.suffix == ".csv" or target_path.exists():
        path = (
            (project.root / target_path).resolve()
            if not target_path.is_absolute()
            else target_path.resolve()
        )
        if not path.exists():
            raise LenzError(f"CSV file does not exist: {path}")
        if not path.is_file() or path.suffix != ".csv":
            raise LenzError(f"Path must point to a .csv file: {path}")
        namespace, name = parse_namespaced_name(path.stem)
        return resource_key(namespace, name), path

    namespace, name = parse_namespaced_name(target)
    table_key = resource_key(namespace, name)
    path = project.table_paths.get(table_key)
    if path is None:
        raise LenzError(f"Unknown untracked CSV table {target!r}; pass a CSV path instead")
    return table_key, path


def schema_document(table_key: str, primary_key: str, header: list[str]) -> dict[str, object]:
    namespace, name = split_resource_key(table_key)
    table_name = name if namespace == DEFAULT_NAMESPACE else table_key
    return {
        "table": table_name,
        "primary_key": primary_key,
        "columns": {
            column: {"type": "string", **({"immutable": True} if column == primary_key else {})}
            for column in header
        },
    }


def write_schema_file(project: Project, table_key: str, document: dict[str, object]) -> Path:
    path = project.schema_dir / f"{table_key}.yaml"
    if path.exists():
        raise LenzError(f"Schema file already exists: {path}")
    project.schema_dir.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(document, handle, sort_keys=False)
    return path


def ensure_table_registered(project: Project, table_key: str, path: Path) -> bool:
    if is_auto_discovered_table_path(project, path):
        return False
    config_path = project.root / ".lenzdb" / "project.yaml"
    if config_path.exists():
        config = Project._load_yaml(config_path)
        if not isinstance(config, dict):
            raise LenzError(f"Project config must be a mapping: {config_path}")
    else:
        config = {}
    tables = config.setdefault(TABLES_CONFIG_KEY, [])
    if not isinstance(tables, list):
        raise LenzError(f"Project config field {TABLES_CONFIG_KEY!r} must be a list")

    relative = project_relative_path(project, path)
    namespace, name = split_resource_key(table_key)
    entry: dict[str, str] = {"path": relative}
    path_namespace, path_name = parse_namespaced_name(path.stem)
    if namespace != path_namespace:
        entry["namespace"] = namespace
    if name != path_name:
        entry["name"] = name

    for existing in tables:
        if isinstance(existing, dict) and existing.get("path") == relative:
            existing.update(entry)
            break
    else:
        tables.append(entry)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)
    return True


@app.command()
@handle_errors
def add(
    target: Annotated[
        str,
        typer.Argument(help="Untracked table name or path to a CSV file to add."),
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
    project_instance = load_project(project, validate_configuration=False)
    table_key, path = resolve_add_csv(project_instance, target)
    if table_key in project_instance.schemas:
        raise LenzError(f"Table {table_key!r} already has a schema")

    header = read_csv_header(path)
    selected_primary_key = choose_primary_key(header, primary_key)
    validate_csv_primary_key(path, selected_primary_key)

    schema_path = write_schema_file(
        project_instance,
        table_key,
        schema_document(table_key, selected_primary_key, header),
    )
    registered = ensure_table_registered(project_instance, table_key, path)

    typer.echo(f"Added table {table_key}")
    typer.echo(f"Schema: {relative_path(project_instance, schema_path)}")
    if registered:
        typer.echo("Updated: .lenzdb/project.yaml")


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
        help="Output format.",
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
        typer.Option("--page", help="One-based page number using the configured page size."),
    ] = None,
    page_size: Annotated[
        int | None,
        typer.Option("--page-size", help="Rows per page. Defaults to project view.page_size."),
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
    project_instance = load_project(project, validate_configuration=False)
    selected_columns = parse_comma_list(columns, option_name="--columns")
    order_columns = parse_comma_list(order, option_name="--order")
    if sql is not None and not sql.strip():
        raise LenzError("--sql must not be empty")
    validate_positive(limit, option_name="--limit")
    validate_non_negative(offset, option_name="--offset")
    validate_positive(page, option_name="--page")
    validate_positive(page_size, option_name="--page-size")

    convenience_options = [
        option is not None
        for option in [selected_columns, where, order_columns, limit, offset, page, page_size]
    ]
    if sql is not None and (count or any(convenience_options)):
        raise LenzError("--sql cannot be combined with view convenience options")
    if count and any(
        option is not None for option in [selected_columns, order_columns, limit, offset, page, page_size]
    ):
        raise LenzError("--count may only be combined with --filter")
    if page is not None and (limit is not None or offset is not None):
        raise LenzError("--page cannot be combined with --limit or --offset")
    if page_size is not None and page is None:
        raise LenzError("--page-size requires --page")

    effective_limit = limit
    effective_offset = offset
    if page is not None:
        effective_page_size = page_size or project_instance.view_page_size
        effective_limit = effective_page_size
        effective_offset = (page - 1) * effective_page_size

    query = ResourceQuery(
        columns=selected_columns,
        where=where,
        order=order_columns,
        limit=effective_limit,
        offset=effective_offset,
        count=count,
        sql=sql,
    )
    result = query_resource_view(project_instance, name, query)
    typer.echo(render_view(result.columns, result.rows, output_format), nl=False)


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
        help="Output format.",
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
    project_instance = load_project(project, validate_configuration=False)
    columns = ["kind", "namespace", "name", "path", "state"]
    if check:
        columns.append("check")
    rows = build_list_rows(project_instance, check)
    typer.echo(render_view(columns, rows, output_format), nl=False)


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
        analysis.primary_key_output,
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
    editor: Annotated[str | None, typer.Option("--editor", help="Override $EDITOR.")] = None,
    discard_recovery: Annotated[
        bool,
        typer.Option(
            "--discard-recovery",
            help="Ignore any preserved failed edit and start from current data.",
        ),
    ] = False,
    project: ProjectOption = None,
) -> None:
    project_instance = load_project(project, validate_configuration=False)
    editor_command = editor or os.environ.get("EDITOR")
    if not editor_command:
        raise LenzError("No editor configured. Set $EDITOR or pass --editor.")

    edit_result = edit_lens(
        project_instance,
        resource_name,
        editor_command,
        discard_recovery=discard_recovery,
    )
    if edit_result.recovered_from is not None:
        typer.echo(f"Recovered previous failed edit: {edit_result.recovered_from}")
    mutation_plan = edit_result.plan
    typer.echo(render_plan(mutation_plan), nl=False)
    if not mutation_plan.has_changes:
        clear_recovery_files(project_instance, resource_name)
        typer.echo("No changes to apply.")
        return
    try:
        apply_mutation_plan(project_instance, mutation_plan)
    except LenzError as exc:
        raise LenzError(f"{exc}\nEdited file preserved at: {edit_result.recovery_path}") from exc
    clear_recovery_files(project_instance, resource_name)
    typer.echo("Changes applied.")


def main() -> None:
    app()
