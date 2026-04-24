"""Typer CLI for LenzDB."""

from __future__ import annotations

import inspect
import os
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer

from lenzdb.analysis import analyze_lens, analyze_resource
from lenzdb.engine import query_lens, query_resource
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
from lenzdb.project import Project, split_resource_key
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


def load_project(project_root: Path | None) -> Project:
    selected_root: Path | str | None = project_root
    if selected_root is None:
        selected_root = os.environ.get(PROJECT_ROOT_ENV_VAR) or None
    return Project.discover(selected_root)


def complete_project_resource(incomplete: str) -> list[str]:
    try:
        project = load_project(None)
    except LenzError:
        return []
    names = {*project.lenses, *project.schemas}
    names.update(split_resource_key(name)[1] for name in list(names))
    return sorted(name for name in names if name.startswith(incomplete))


def complete_lens(incomplete: str) -> list[str]:
    try:
        project = load_project(None)
    except LenzError:
        return []
    names = set(project.lenses)
    names.update(split_resource_key(name)[1] for name in list(names))
    return sorted(name for name in names if name.startswith(incomplete))


def project_namespaces(project: Project) -> list[str]:
    namespaces = {split_resource_key(key)[0] for key in project.schemas}
    namespaces.update(split_resource_key(key)[0] for key in project.lenses)
    return sorted(namespaces)


def relative_path(project: Project, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(project.root))
    except ValueError:
        return str(path)


def status_for_namespace(project: Project, namespace: str) -> str:
    has_table = any(split_resource_key(key)[0] == namespace for key in project.schemas)
    has_lens = any(split_resource_key(key)[0] == namespace for key in project.lenses)
    return "ok" if has_table or has_lens else "empty"


def status_for_table(project: Project, table_name: str) -> str:
    try:
        project.load_table_rows(table_name)
        return "ok"
    except Exception as exc:
        return str(exc)


def status_for_lens(project: Project, lens_name: str) -> str:
    try:
        analyze_lens(project, lens_name)
        query_lens(project, lens_name)
        return "ok"
    except Exception as exc:
        return str(exc)


def build_list_rows(project: Project, with_status: bool) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for namespace in project_namespaces(project):
        row = {"kind": "namespace", "namespace": namespace, "name": namespace, "path": ""}
        if with_status:
            row["status"] = status_for_namespace(project, namespace)
        rows.append(row)

    for table_name, path in sorted(project.table_paths.items()):
        namespace, name = split_resource_key(table_name)
        row = {
            "kind": "table",
            "namespace": namespace,
            "name": name,
            "path": relative_path(project, path),
        }
        if with_status:
            row["status"] = status_for_table(project, table_name)
        rows.append(row)

    for lens_name, path in sorted(project.lenses.items()):
        namespace, name = split_resource_key(lens_name)
        row = {
            "kind": "lens",
            "namespace": namespace,
            "name": name,
            "path": relative_path(project, path),
        }
        if with_status:
            row["status"] = status_for_lens(project, lens_name)
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
    project: ProjectOption = None,
) -> None:
    project_instance = load_project(project)
    result = query_resource(project_instance, name)
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
    with_status: Annotated[
        bool,
        typer.Option(
            "--with-status",
            "-s",
            help="Run validation/query checks and include a status column.",
        ),
    ] = False,
    project: ProjectOption = None,
) -> None:
    project_instance = load_project(project)
    columns = ["kind", "namespace", "name", "path"]
    if with_status:
        columns.append("status")
    rows = build_list_rows(project_instance, with_status)
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
    project_instance = load_project(project)
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
    project_instance = load_project(project)
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
    project_instance = load_project(project)
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
    project_instance = load_project(project)
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
    project_instance = load_project(project)
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
