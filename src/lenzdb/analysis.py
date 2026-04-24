"""Lens analysis using SQLGlot."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from lenzdb.engine import query_lens
from lenzdb.errors import LensAnalysisError, ProjectError
from lenzdb.models import LensPolicy
from lenzdb.project import Project, canonical_scalar, parse_qualified_name

ColumnKind = Literal[
    "direct_base",
    "aliased_base",
    "joined_lookup",
    "computed",
    "aggregate",
    "wildcard",
]


@dataclass(slots=True)
class AnalyzedColumn:
    output_name: str
    kind: ColumnKind
    source_table: str | None = None
    source_column: str | None = None
    writable: bool = False
    reason: str = ""


@dataclass(slots=True)
class LensAnalysis:
    lens_name: str
    primary_table: str | None
    primary_alias: str | None
    primary_key_output: str | None
    columns: list[AnalyzedColumn]
    writable: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def column_map(self) -> dict[str, AnalyzedColumn]:
        return {column.output_name: column for column in self.columns}


def split_and(expression: exp.Expression | None) -> list[exp.Expression]:
    if expression is None:
        return []
    if isinstance(expression, exp.And):
        return [*split_and(expression.this), *split_and(expression.expression)]
    return [expression]


def resolve_column_table(
    column_expression: exp.Column, aliases: dict[str, str], project: Project, primary_table: str
) -> str | None:
    explicit_table = column_expression.table
    if explicit_table:
        if explicit_table in aliases:
            return aliases[explicit_table]
        explicit_namespace = column_expression.args.get("db")
        try:
            return project.resolve_table_name(
                explicit_table,
                explicit_namespace.name if explicit_namespace is not None else None,
            )
        except ProjectError:
            return None

    column_name = column_expression.name
    matches = [
        table_name
        for alias, table_name in aliases.items()
        if column_name in project.schema_for(table_name).columns
    ]
    if len(matches) == 1:
        return matches[0]
    if column_name in project.schema_for(primary_table).columns:
        return primary_table
    return None


def safe_join_reason(
    primary_table: str,
    primary_alias: str,
    join_alias: str,
    join_table: str,
    join_on: exp.Expression | None,
    project: Project,
) -> tuple[bool, str]:
    if join_on is None:
        return False, "join is missing an ON clause"

    primary_schema = project.schema_for(primary_table)
    join_schema = project.schema_for(join_table)

    for condition in split_and(join_on):
        if not isinstance(condition, exp.EQ):
            continue
        left = condition.this
        right = condition.expression
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue

        left_table = resolve_column_table(left, {primary_alias: primary_table, join_alias: join_table}, project, primary_table)
        right_table = resolve_column_table(right, {primary_alias: primary_table, join_alias: join_table}, project, primary_table)
        if {left_table, right_table} != {primary_table, join_table}:
            continue

        if left_table == primary_table:
            primary_column_name = left.name
            join_column_name = right.name
        else:
            primary_column_name = right.name
            join_column_name = left.name

        primary_column = primary_schema.columns.get(primary_column_name)
        if primary_column is None or primary_column.type != "ref":
            continue
        if project.resolve_table_name(primary_column.table or "") != join_table:
            continue
        if join_column_name != join_schema.primary_key:
            continue

        return True, (
            f"join is safe because {primary_table}.{primary_column_name} references "
            f"{join_table}.{join_column_name}"
        )

    return False, (
        f"join to {join_table!r} is not recognized as a many-to-one lookup from the primary table"
    )


def _column_reason(kind: ColumnKind) -> str:
    return {
        "direct_base": "direct base column",
        "aliased_base": "aliased base column",
        "joined_lookup": "joined lookup",
        "computed": "computed expression",
        "aggregate": "aggregate expression",
        "wildcard": "wildcard selection",
    }[kind]


def analyze_lens(project: Project, lens_name: str) -> LensAnalysis:
    sql = project.lens_sql(lens_name)
    try:
        expression = parse_one(sql, read="duckdb")
    except ParseError as exc:
        raise LensAnalysisError(f"Failed to parse lens {lens_name!r}: {exc}") from exc

    if not isinstance(expression, exp.Select):
        raise LensAnalysisError(
            f"Lens {lens_name!r} must be a single top-level SELECT for writable analysis"
        )

    if expression.args.get("distinct"):
        raise LensAnalysisError(f"Lens {lens_name!r} uses DISTINCT, which is not writable")
    if expression.args.get("group"):
        raise LensAnalysisError(f"Lens {lens_name!r} uses GROUP BY, which is not writable")
    if expression.args.get("having"):
        raise LensAnalysisError(f"Lens {lens_name!r} uses HAVING, which is not writable")
    if expression.args.get("with"):
        raise LensAnalysisError(f"Lens {lens_name!r} uses WITH/CTEs, which are not writable in v1")

    from_expression = expression.args.get("from_")
    if from_expression is None or not isinstance(from_expression.this, exp.Table):
        raise LensAnalysisError(f"Lens {lens_name!r} must read from a base table")

    primary_table_expression = from_expression.this
    primary_table = project.resolve_table_name(
        primary_table_expression.name, primary_table_expression.db or None
    )
    primary_alias = primary_table_expression.alias_or_name

    aliases: dict[str, str] = {primary_alias: primary_table}
    joins = expression.args.get("joins") or []
    reasons: list[str] = []
    warnings: list[str] = []

    for join in joins:
        if not isinstance(join.this, exp.Table):
            reasons.append("join target must be a concrete table")
            continue
        join_table = project.resolve_table_name(join.this.name, join.this.db or None)
        join_alias = join.this.alias_or_name
        aliases[join_alias] = join_table
        if join.args.get("side") not in {None, "LEFT", "RIGHT", "INNER"}:
            reasons.append(
                f"join to {join_table!r} uses unsupported join side {join.args.get('side')!r}"
            )
            continue
        is_safe, reason = safe_join_reason(
            primary_table,
            primary_alias,
            join_alias,
            join_table,
            join.args.get("on"),
            project,
        )
        if not is_safe:
            reasons.append(reason)
        else:
            warnings.append(reason)

    policy = project.policy_for(lens_name)
    columns: list[AnalyzedColumn] = []
    primary_key_output: str | None = None

    for select_expression in expression.expressions:
        output_name = select_expression.alias_or_name or select_expression.sql(dialect="duckdb")
        inner = (
            select_expression.this
            if isinstance(select_expression, exp.Alias)
            else select_expression
        )

        if isinstance(inner, exp.Star):
            column = AnalyzedColumn(
                output_name=output_name,
                kind="wildcard",
                writable=False,
                reason="wildcard selections are not writable",
            )
            columns.append(column)
            reasons.append("wildcard selections are not writable")
            continue

        if inner.find(exp.AggFunc):
            column = AnalyzedColumn(
                output_name=output_name,
                kind="aggregate",
                writable=False,
                reason="aggregate expressions are not writable",
            )
            columns.append(column)
            reasons.append(f"column {output_name!r} is an aggregate expression")
            continue

        if isinstance(inner, exp.Column):
            source_table = resolve_column_table(inner, aliases, project, primary_table)
            source_column = inner.name
            if source_table == primary_table:
                kind: ColumnKind = "direct_base" if output_name == source_column else "aliased_base"
                writable = True
                reason = _column_reason(kind)
                source_schema = project.schema_for(primary_table).columns[source_column]
                if source_schema.immutable:
                    writable = False
                    reason = "column is immutable in schema"
                if source_column == project.schema_for(primary_table).primary_key:
                    primary_key_output = output_name
            elif source_table in aliases.values():
                kind = "joined_lookup"
                writable = bool(policy and output_name in policy.references)
                reason = (
                    "reference policy allows writeback"
                    if writable
                    else "joined lookup columns are read-only without a reference policy"
                )
            else:
                kind = "computed"
                writable = False
                reason = "unresolved column reference"

            columns.append(
                AnalyzedColumn(
                    output_name=output_name,
                    kind=kind,
                    source_table=source_table,
                    source_column=source_column,
                    writable=writable,
                    reason=reason,
                )
            )
            continue

        columns.append(
            AnalyzedColumn(
                output_name=output_name,
                kind="computed",
                writable=False,
                reason="computed expressions are not writable",
            )
        )

    if primary_key_output is None:
        reasons.append(
            f"primary key {project.schema_for(primary_table).primary_key!r} is not selected by the lens"
        )

    result = query_lens(project, lens_name)
    if primary_key_output is not None:
        seen_keys: set[str] = set()
        for row in result.rows:
            key = canonical_scalar(row.get(primary_key_output))
            if key == "":
                reasons.append(f"lens row is missing primary key output {primary_key_output!r}")
                break
            if key in seen_keys:
                reasons.append(
                    f"lens rows do not map one-to-one to the primary table because {primary_key_output!r} repeats"
                )
                break
            seen_keys.add(key)

    if policy is not None:
        validate_policy_against_analysis(project, policy, columns)

    writable = not reasons and primary_key_output is not None
    return LensAnalysis(
        lens_name=lens_name,
        primary_table=primary_table,
        primary_alias=primary_alias,
        primary_key_output=primary_key_output,
        columns=columns,
        writable=writable,
        reasons=reasons,
        warnings=warnings,
    )


def validate_policy_against_analysis(
    project: Project, policy: LensPolicy, columns: list[AnalyzedColumn]
) -> None:
    available = {column.output_name: column for column in columns}
    primary_table = project.resolve_table_name(policy.primary_table)
    schema = project.schema_for(primary_table)

    for output_name, target in policy.editable.items():
        analyzed = available.get(output_name)
        if analyzed is None:
            raise ProjectError(f"Policy references missing output column {output_name!r}")
        if analyzed.kind not in {"direct_base", "aliased_base"}:
            raise ProjectError(
                f"Editable policy column {output_name!r} must select a primary-table base column"
            )
        table_name, column_name = parse_qualified_name(target)
        target_table = project.resolve_table_name(table_name)
        if target_table != primary_table or column_name not in schema.columns:
            raise ProjectError(
                f"Editable policy column {output_name!r} targets invalid path {target!r}"
            )

    for output_name, _reference in policy.references.items():
        analyzed = available.get(output_name)
        if analyzed is None:
            raise ProjectError(f"Reference policy references missing output column {output_name!r}")
        if analyzed.kind not in {"joined_lookup", "direct_base", "aliased_base"}:
            raise ProjectError(
                f"Reference policy column {output_name!r} must come from a concrete selected column"
            )
