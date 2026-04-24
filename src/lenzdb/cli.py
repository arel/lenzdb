"""Typer CLI for LenzDB."""

from __future__ import annotations

import inspect
import os
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer

from lenzdb.analysis import analyze_lens
from lenzdb.engine import query_lens
from lenzdb.errors import LenzError
from lenzdb.planner import (
    apply_mutation_plan,
    build_mutation_plan,
    diff_snapshots,
    edit_lens,
    read_snapshot_csv,
    snapshot_rows,
)
from lenzdb.project import Project
from lenzdb.render import render_analysis, render_diff, render_plan, render_view

app = typer.Typer(help="LenzDB CLI")

ProjectOption = Annotated[
    Path | None,
    typer.Option(
        None,
        "--project",
        help="Project root. Defaults to the current working directory.",
        exists=False,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
    ),
]


def load_project(project_root: Path | None) -> Project:
    return Project.discover(project_root)


def handle_errors(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except LenzError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    wrapper.__signature__ = inspect.signature(function)
    return wrapper


@app.command()
@handle_errors
def view(
    lens_name: str,
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
    result = query_lens(project_instance, lens_name)
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


@app.command()
@handle_errors
def explain(lens_name: str, project: ProjectOption = None) -> None:
    project_instance = load_project(project)
    analysis = analyze_lens(project_instance, lens_name)
    typer.echo(render_analysis(analysis), nl=False)


@app.command()
@handle_errors
def diff(lens_name: str, edited_csv: Path, project: ProjectOption = None) -> None:
    project_instance = load_project(project)
    result = query_lens(project_instance, lens_name)
    analysis = analyze_lens(project_instance, lens_name)
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
def plan(lens_name: str, edited_csv: Path, project: ProjectOption = None) -> None:
    project_instance = load_project(project)
    mutation_plan = build_mutation_plan(project_instance, lens_name, edited_csv)
    typer.echo(render_plan(mutation_plan), nl=False)


@app.command()
@handle_errors
def apply(
    lens_name: str,
    edited_csv: Path,
    yes: Annotated[bool, typer.Option(False, "--yes", help="Apply without prompting.")] = False,
    project: ProjectOption = None,
) -> None:
    project_instance = load_project(project)
    mutation_plan = build_mutation_plan(project_instance, lens_name, edited_csv)
    typer.echo(render_plan(mutation_plan), nl=False)
    if not mutation_plan.has_changes:
        typer.echo("No changes to apply.")
        return
    if not yes and not typer.confirm("Apply these changes?"):
        raise typer.Exit(code=1)
    apply_mutation_plan(project_instance, mutation_plan)
    typer.echo("Changes applied.")


@app.command()
@handle_errors
def edit(
    lens_name: str,
    editor: Annotated[str | None, typer.Option(None, "--editor", help="Override $EDITOR.")] = None,
    yes: Annotated[bool, typer.Option(False, "--yes", help="Apply without prompting.")] = False,
    project: ProjectOption = None,
) -> None:
    project_instance = load_project(project)
    editor_command = editor or os.environ.get("EDITOR")
    if not editor_command:
        raise LenzError("No editor configured. Set $EDITOR or pass --editor.")

    mutation_plan = edit_lens(project_instance, lens_name, editor_command)
    typer.echo(render_plan(mutation_plan), nl=False)
    if not mutation_plan.has_changes:
        typer.echo("No changes to apply.")
        return
    if not yes and not typer.confirm("Apply these changes?"):
        raise typer.Exit(code=1)
    apply_mutation_plan(project_instance, mutation_plan)
    typer.echo("Changes applied.")


def main() -> None:
    app()
