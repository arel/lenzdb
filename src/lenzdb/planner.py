"""Lens diffing, mutation planning, and writeback."""

from __future__ import annotations

import csv
import shlex
import subprocess
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lenzdb.analysis import AnalyzedColumn, LensAnalysis, analyze_lens
from lenzdb.engine import query_lens
from lenzdb.errors import MutationError
from lenzdb.models import LensPolicy, ReferencePolicy
from lenzdb.project import (
    Project,
    canonical_scalar,
    parse_column_value,
    parse_qualified_name,
    serialize_value,
)


@dataclass(slots=True)
class DiffEntry:
    key: str
    change_type: str
    changes: list[tuple[str, str, str]]


@dataclass(slots=True)
class TableMutation:
    table: str
    key: str
    changes: dict[str, Any]


@dataclass(slots=True)
class TableInsert:
    table: str
    row: dict[str, Any]


@dataclass(slots=True)
class MutationPlan:
    lens_name: str
    primary_table: str
    primary_key_output: str
    diff_entries: list[DiffEntry]
    updates: list[TableMutation]
    inserts: list[TableInsert]
    reference_creations: list[TableInsert]
    generated_primary_keys: list[str]
    rows_by_table: dict[str, list[dict[str, Any]]] = field(repr=False)
    touched_tables: set[str] = field(repr=False)

    @property
    def has_changes(self) -> bool:
        return bool(self.updates or self.inserts or self.reference_creations)


def snapshot_rows(columns: list[str], rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{column: canonical_scalar(row.get(column)) for column in columns} for row in rows]


def read_snapshot_csv(path: Path, expected_columns: list[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        if set(headers) != set(expected_columns):
            raise MutationError(
                f"Edited CSV columns must match the lens output exactly. "
                f"expected={expected_columns}, got={headers}"
            )
        return [{column: row.get(column, "") for column in expected_columns} for row in reader]


def diff_snapshots(
    current_rows: list[dict[str, str]],
    edited_rows: list[dict[str, str]],
    key_column: str | None,
    columns: list[str],
) -> list[DiffEntry]:
    if key_column:
        current_by_key = {row.get(key_column, ""): row for row in current_rows}
        edited_by_key = {row.get(key_column, ""): row for row in edited_rows}
        keys = list(dict.fromkeys([*current_by_key, *edited_by_key]))
        entries: list[DiffEntry] = []
        for key in keys:
            current = current_by_key.get(key)
            edited = edited_by_key.get(key)
            if current is None and edited is not None:
                changes = [(column, "", edited.get(column, "")) for column in columns]
                entries.append(
                    DiffEntry(key=key or "<new>", change_type="inserted", changes=changes)
                )
                continue
            if current is not None and edited is None:
                changes = [(column, current.get(column, ""), "") for column in columns]
                entries.append(
                    DiffEntry(key=key or "<missing>", change_type="deleted", changes=changes)
                )
                continue
            assert current is not None and edited is not None
            cell_changes = []
            for column in columns:
                before = current.get(column, "")
                after = edited.get(column, "")
                if before != after:
                    cell_changes.append((column, before, after))
            if cell_changes:
                entries.append(
                    DiffEntry(key=key or "<blank>", change_type="updated", changes=cell_changes)
                )
        return entries

    entries: list[DiffEntry] = []
    max_rows = max(len(current_rows), len(edited_rows))
    for index in range(max_rows):
        current = current_rows[index] if index < len(current_rows) else None
        edited = edited_rows[index] if index < len(edited_rows) else None
        if current is None and edited is not None:
            entries.append(
                DiffEntry(
                    key=f"row {index + 1}",
                    change_type="inserted",
                    changes=[(column, "", edited.get(column, "")) for column in columns],
                )
            )
            continue
        if current is not None and edited is None:
            entries.append(
                DiffEntry(
                    key=f"row {index + 1}",
                    change_type="deleted",
                    changes=[(column, current.get(column, ""), "") for column in columns],
                )
            )
            continue
        assert current is not None and edited is not None
        cell_changes = []
        for column in columns:
            before = current.get(column, "")
            after = edited.get(column, "")
            if before != after:
                cell_changes.append((column, before, after))
        if cell_changes:
            entries.append(
                DiffEntry(key=f"row {index + 1}", change_type="updated", changes=cell_changes)
            )
    return entries


def resolve_target_column(
    analyzed_column: AnalyzedColumn,
    policy: LensPolicy | None,
) -> tuple[str, str] | None:
    if policy and analyzed_column.output_name in policy.editable:
        return parse_qualified_name(policy.editable[analyzed_column.output_name])
    if analyzed_column.source_table and analyzed_column.source_column:
        return analyzed_column.source_table, analyzed_column.source_column
    return None


def parse_input_value(project: Project, table: str, column_name: str, raw_value: str) -> Any:
    column = project.schema_for(table).columns[column_name]
    return parse_column_value(raw_value, column, location=f"{table}.{column_name}")


def find_reference_match(
    project: Project,
    rows_by_table: dict[str, list[dict[str, Any]]],
    reference_policy: ReferencePolicy,
    display_value: str,
    reference_creations: list[TableInsert],
) -> Any:
    lookup_table = reference_policy.lookup.table
    lookup_schema = project.schema_for(lookup_table)
    lookup_key = lookup_schema.primary_key
    match_column = reference_policy.lookup.match

    matches = [
        row
        for row in rows_by_table[lookup_table]
        if serialize_value(row.get(match_column), lookup_schema.columns[match_column])
        == display_value
    ]
    if len(matches) == 1:
        return matches[0][lookup_key]
    if len(matches) > 1:
        raise MutationError(
            f"Reference value {display_value!r} is ambiguous for {lookup_table}.{match_column}"
        )
    if not reference_policy.lookup.create_if_missing:
        raise MutationError(
            f"Reference value {display_value!r} does not exist in {lookup_table}.{match_column}"
        )

    new_row = project.blank_row(lookup_table)
    primary_column = lookup_schema.columns[lookup_key]
    if primary_column.type != "string":
        raise MutationError(
            f"Cannot create missing lookup rows for {lookup_table!r}: "
            f"primary key {lookup_key!r} is not a string column"
        )

    new_key = project.generate_primary_key(lookup_table)
    new_row[lookup_key] = new_key
    new_row[match_column] = parse_input_value(project, lookup_table, match_column, display_value)
    rows_by_table[lookup_table].append(new_row)
    reference_creations.append(TableInsert(table=lookup_table, row=deepcopy(new_row)))
    return new_row[lookup_key]


def collect_row_changes(
    *,
    project: Project,
    analysis: LensAnalysis,
    current_row: dict[str, str] | None,
    edited_row: dict[str, str],
    is_insert: bool,
    rows_by_table: dict[str, list[dict[str, Any]]],
    reference_creations: list[TableInsert],
) -> dict[str, Any]:
    policy = project.policy_for(analysis.lens_name)
    column_map = analysis.column_map()
    primary_schema = project.schema_for(analysis.primary_table or "")
    changes: dict[str, Any] = {}

    for output_name, new_value in edited_row.items():
        if output_name == analysis.primary_key_output:
            if current_row is not None and new_value != current_row.get(output_name, ""):
                raise MutationError("Updating a lens row primary key is not supported")
            continue

        old_value = "" if current_row is None else current_row.get(output_name, "")
        if not is_insert and old_value == new_value:
            continue
        if is_insert and new_value == "":
            continue

        analyzed_column = column_map[output_name]
        if analyzed_column.kind in {"computed", "aggregate", "wildcard"}:
            raise MutationError(f"Column {output_name!r} is not writable")
        if analyzed_column.kind == "joined_lookup" and not (
            policy and output_name in policy.references
        ):
            raise MutationError(
                f"Joined lookup column {output_name!r} is not writable without a policy"
            )
        if not analyzed_column.writable and not (policy and output_name in policy.references):
            raise MutationError(f"Column {output_name!r} is not writable")

        if policy and output_name in policy.references:
            ref_policy = policy.references[output_name]
            target_table, target_column = parse_qualified_name(ref_policy.write_to)
            if new_value == "":
                parsed_value = None
            else:
                parsed_value = find_reference_match(
                    project,
                    rows_by_table,
                    ref_policy,
                    new_value,
                    reference_creations,
                )
        else:
            target = resolve_target_column(analyzed_column, policy)
            if target is None:
                raise MutationError(f"Column {output_name!r} does not map to a writable target")
            target_table, target_column = target
            parsed_value = parse_input_value(project, target_table, target_column, new_value)

        if target_table != analysis.primary_table:
            raise MutationError(
                f"Writable output {output_name!r} maps outside the primary table, which is unsupported in v1"
            )

        source_column = primary_schema.columns[target_column]
        if source_column.immutable:
            raise MutationError(f"Column {analysis.primary_table}.{target_column} is immutable")

        previous_value = changes.get(target_column)
        if previous_value is not None and previous_value != parsed_value:
            raise MutationError(
                f"Edited outputs conflict on the target column {analysis.primary_table}.{target_column}"
            )
        changes[target_column] = parsed_value

    return changes


def build_mutation_plan(
    project: Project, lens_name: str, edited_csv_path: str | Path
) -> MutationPlan:
    analysis = analyze_lens(project, lens_name)
    if not analysis.writable:
        raise MutationError(
            "Lens is not writable: "
            + "; ".join(analysis.reasons or ["missing primary key mapping"])
        )
    if analysis.primary_table is None or analysis.primary_key_output is None:
        raise MutationError("Lens is missing primary table or primary key information")

    result = query_lens(project, lens_name)
    current_rows = snapshot_rows(result.columns, result.rows)
    edited_rows = read_snapshot_csv(Path(edited_csv_path), result.columns)
    diff_entries = diff_snapshots(
        current_rows, edited_rows, analysis.primary_key_output, result.columns
    )

    current_by_key: dict[str, dict[str, str]] = {}
    for row in current_rows:
        key = row.get(analysis.primary_key_output, "")
        if key == "":
            raise MutationError("Lens contains a row without the primary key output")
        if key in current_by_key:
            raise MutationError("Lens rows do not map one-to-one to the primary table")
        current_by_key[key] = row

    edited_keys = [row.get(analysis.primary_key_output, "") for row in edited_rows if row]
    deleted_keys = sorted(set(current_by_key) - {key for key in edited_keys if key})
    if deleted_keys:
        raise MutationError(
            f"Deleting rows through edited lens snapshots is not supported in v1: {deleted_keys}"
        )

    rows_by_table = project.load_all_rows()
    working_rows = deepcopy(rows_by_table)
    primary_schema = project.schema_for(analysis.primary_table)
    primary_key = primary_schema.primary_key
    base_index = {
        serialize_value(row.get(primary_key), primary_schema.columns[primary_key]): row
        for row in working_rows[analysis.primary_table]
    }

    updates: list[TableMutation] = []
    inserts: list[TableInsert] = []
    reference_creations: list[TableInsert] = []
    generated_primary_keys: list[str] = []
    touched_tables: set[str] = set()
    pending_insert_keys: set[str] = set()

    for edited_row in edited_rows:
        key = edited_row.get(analysis.primary_key_output, "")
        current_row = current_by_key.get(key)

        if current_row is not None:
            base_row = base_index.get(key)
            if base_row is None:
                raise MutationError(
                    f"Lens row {key!r} does not map back to a source row in {analysis.primary_table}"
                )
            changes = collect_row_changes(
                project=project,
                analysis=analysis,
                current_row=current_row,
                edited_row=edited_row,
                is_insert=False,
                rows_by_table=working_rows,
                reference_creations=reference_creations,
            )
            if changes:
                base_row.update(changes)
                updates.append(
                    TableMutation(table=analysis.primary_table, key=key, changes=deepcopy(changes))
                )
                touched_tables.add(analysis.primary_table)
            continue

        new_row = project.blank_row(analysis.primary_table)
        pk_value = key
        if pk_value == "":
            pk_column = primary_schema.columns[primary_key]
            if pk_column.type != "string":
                raise MutationError(
                    f"Inserted rows for {analysis.primary_table!r} must supply primary key "
                    f"{primary_key!r} because it is not a string column"
                )
            pk_value = project.generate_primary_key(analysis.primary_table)
            generated_primary_keys.append(pk_value)
            edited_row = dict(edited_row)
            edited_row[analysis.primary_key_output] = pk_value

        if pk_value in base_index or pk_value in pending_insert_keys:
            raise MutationError(
                f"Inserted row primary key {pk_value!r} already exists in {analysis.primary_table!r}"
            )

        new_row[primary_key] = parse_input_value(
            project, analysis.primary_table, primary_key, pk_value
        )
        changes = collect_row_changes(
            project=project,
            analysis=analysis,
            current_row=None,
            edited_row=edited_row,
            is_insert=True,
            rows_by_table=working_rows,
            reference_creations=reference_creations,
        )
        new_row.update(changes)
        working_rows[analysis.primary_table].append(new_row)
        base_index[pk_value] = new_row
        pending_insert_keys.add(pk_value)
        inserts.append(TableInsert(table=analysis.primary_table, row=deepcopy(new_row)))
        touched_tables.add(analysis.primary_table)

    if reference_creations:
        touched_tables.update(insert.table for insert in reference_creations)

    project.validate_rows_map(working_rows)

    return MutationPlan(
        lens_name=lens_name,
        primary_table=analysis.primary_table,
        primary_key_output=analysis.primary_key_output,
        diff_entries=diff_entries,
        updates=updates,
        inserts=inserts,
        reference_creations=reference_creations,
        generated_primary_keys=generated_primary_keys,
        rows_by_table=working_rows,
        touched_tables=touched_tables,
    )


def apply_mutation_plan(project: Project, plan: MutationPlan) -> MutationPlan:
    if plan.has_changes:
        project.write_rows_map_atomic(plan.rows_by_table, plan.touched_tables)
        Project.discover(project.root).validate()
    return plan


def export_lens_csv(project: Project, lens_name: str, target: Path) -> None:
    result = query_lens(project, lens_name)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=result.columns)
        writer.writeheader()
        for row in snapshot_rows(result.columns, result.rows):
            writer.writerow(row)


def run_editor(editor: str, path: Path) -> None:
    command = [*shlex.split(editor), str(path)]
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise MutationError(f"Editor command not found: {editor!r}") from exc
    except subprocess.CalledProcessError as exc:
        raise MutationError(f"Editor command failed with exit code {exc.returncode}") from exc


def edit_lens(project: Project, lens_name: str, editor: str) -> MutationPlan:
    with tempfile.TemporaryDirectory(prefix="lenzdb-") as temp_dir:
        temp_path = Path(temp_dir) / f"{lens_name}.csv"
        export_lens_csv(project, lens_name, temp_path)
        run_editor(editor, temp_path)
        return build_mutation_plan(project, lens_name, temp_path)
