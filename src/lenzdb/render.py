"""Output renderers for lens data and plans."""

from __future__ import annotations

import csv
import io
import json
from html import escape
from typing import Any

import yaml
from rich.console import Console
from rich.table import Table

from lenzdb.analysis import LensAnalysis
from lenzdb.planner import DiffEntry, MutationPlan
from lenzdb.project import canonical_scalar


def normalize_rows(columns: list[str], rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{column: canonical_scalar(row.get(column)) for column in columns} for row in rows]


def render_csv(columns: list[str], rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in normalize_rows(columns, rows):
        writer.writerow(row)
    return buffer.getvalue()


def render_tsv(columns: list[str], rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for row in normalize_rows(columns, rows):
        writer.writerow(row)
    return buffer.getvalue()


def render_json(columns: list[str], rows: list[dict[str, Any]]) -> str:
    return json.dumps(normalize_rows(columns, rows), indent=2) + "\n"


def render_yaml(columns: list[str], rows: list[dict[str, Any]]) -> str:
    return yaml.safe_dump(normalize_rows(columns, rows), sort_keys=False)


def render_ndjson(columns: list[str], rows: list[dict[str, Any]]) -> str:
    normalized = normalize_rows(columns, rows)
    return "".join(json.dumps(row) + "\n" for row in normalized)


def render_markdown(columns: list[str], rows: list[dict[str, Any]]) -> str:
    normalized = normalize_rows(columns, rows)
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(row.get(column, "") for column in columns) + " |" for row in normalized
    ]
    return "\n".join([header, separator, *body]) + "\n"


def render_list(columns: list[str], rows: list[dict[str, Any]]) -> str:
    normalized = normalize_rows(columns, rows)
    return "".join(
        f"- [{' | '.join(row.get(column, '') for column in columns)}]\n" for row in normalized
    )


def render_html(columns: list[str], rows: list[dict[str, Any]]) -> str:
    normalized = normalize_rows(columns, rows)
    parts = [
        "<table>",
        "  <thead>",
        "    <tr>",
        *[f"      <th>{escape(column)}</th>" for column in columns],
        "    </tr>",
        "  </thead>",
        "  <tbody>",
    ]
    for row in normalized:
        parts.extend(
            [
                "    <tr>",
                *[f"      <td>{escape(row.get(column, ''))}</td>" for column in columns],
                "    </tr>",
            ]
        )
    parts.extend(["  </tbody>", "</table>"])
    return "\n".join(parts) + "\n"


def render_table(columns: list[str], rows: list[dict[str, Any]], *, width: int | None = None) -> str:
    normalized = normalize_rows(columns, rows)
    table = Table(show_header=True, header_style="bold")
    for column in columns:
        table.add_column(column)
    for row in normalized:
        table.add_row(*(row.get(column, "") for column in columns))
    console = Console(color_system=None, force_terminal=False, width=width or 120)
    with console.capture() as capture:
        console.print(table)
    return capture.get()


def render_view(
    columns: list[str],
    rows: list[dict[str, Any]],
    output_format: str,
    *,
    width: int | None = None,
) -> str:
    if output_format == "csv":
        return render_csv(columns, rows)
    if output_format == "tsv":
        return render_tsv(columns, rows)
    if output_format == "json":
        return render_json(columns, rows)
    if output_format == "yaml":
        return render_yaml(columns, rows)
    if output_format == "ndjson":
        return render_ndjson(columns, rows)
    if output_format == "markdown":
        return render_markdown(columns, rows)
    if output_format == "list":
        return render_list(columns, rows)
    if output_format == "html":
        return render_html(columns, rows)
    return render_table(columns, rows, width=width)


def render_analysis(analysis: LensAnalysis) -> str:
    primary_key_output = (
        ", ".join(analysis.primary_key_outputs)
        if analysis.primary_key_outputs
        else analysis.primary_key_output or "(missing)"
    )
    lines = [
        f"Resource: {analysis.lens_name}",
        f"Primary table: {analysis.primary_table or 'unknown'}",
        f"Writable: {'yes' if analysis.writable else 'no'}",
        f"Primary key output: {primary_key_output}",
        "",
        "Columns:",
    ]
    for column in analysis.columns:
        source = (
            f"{column.source_table}.{column.source_column}"
            if column.source_table and column.source_column
            else "-"
        )
        lines.append(
            f"  - {column.output_name}: kind={column.kind}, source={source}, "
            f"writable={'yes' if column.writable else 'no'} ({column.reason})"
        )
    if analysis.reasons:
        lines.extend(["", "Blocking reasons:"])
        for reason in analysis.reasons:
            lines.append(f"  - {reason}")
    if analysis.warnings:
        lines.extend(["", "Notes:"])
        for warning in analysis.warnings:
            lines.append(f"  - {warning}")
    return "\n".join(lines) + "\n"


def render_diff(entries: list[DiffEntry]) -> str:
    if not entries:
        return "No changes.\n"
    lines: list[str] = []
    for entry in entries:
        header = f"{entry.change_type.upper()} {entry.key}"
        lines.append(header)
        for column_name, before, after in entry.changes:
            lines.append(f"  {column_name}: {before!r} -> {after!r}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_plan(plan: MutationPlan) -> str:
    lines = [
        f"Resource: {plan.lens_name}",
        f"Primary table: {plan.primary_table}",
        f"Updates: {len(plan.updates)}",
        f"Inserts: {len(plan.inserts)}",
        f"Reference creates: {len(plan.reference_creations)}",
    ]
    if plan.generated_primary_keys:
        lines.append("Generated primary keys:")
        for key in plan.generated_primary_keys:
            lines.append(f"  - {key}")
    if plan.updates:
        lines.append("Updates:")
        for action in plan.updates:
            lines.append(f"  - {action.table}[{action.key}] {action.changes}")
    if plan.inserts:
        lines.append("Inserts:")
        for action in plan.inserts:
            lines.append(f"  - {action.table} {action.row}")
    if plan.reference_creations:
        lines.append("Reference creates:")
        for action in plan.reference_creations:
            lines.append(f"  - {action.table} {action.row}")
    if plan.diff_entries:
        lines.append("Diff:")
        lines.append(render_diff(plan.diff_entries).rstrip())
    return "\n".join(lines) + "\n"
