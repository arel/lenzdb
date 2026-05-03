from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lenzdb.cli import app
from lenzdb.errors import MutationError
from lenzdb.engine import ResourceQuery
from lenzdb.planner import apply_mutation_plan, build_mutation_plan, build_mutation_plan_for_view
from lenzdb.project import Project


def test_build_mutation_plan_updates_primary_table_columns(example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "edited.csv"
    edited.write_text(
        "id,title,status,project_name\n"
        "t-1,Ship production CLI,doing,Core Platform\n"
        "t-2,Write getting started docs,doing,Docs Refresh\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    plan = build_mutation_plan(project, "open_tasks", edited)

    assert len(plan.updates) == 1
    assert plan.updates[0].changes == {"title": "Ship production CLI", "status": "doing"}
    assert not plan.inserts


def test_build_mutation_plan_rejects_joined_column_edits(example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "edited.csv"
    edited.write_text(
        "id,title,status,project_name\n"
        "t-1,Ship CLI skeleton,todo,New Project Name\n"
        "t-2,Write getting started docs,doing,Docs Refresh\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    with pytest.raises(MutationError, match="Joined lookup column 'project_name' is not writable"):
        build_mutation_plan(project, "open_tasks", edited)


def test_build_mutation_plan_for_view_applies_defaults(example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "edited.csv"
    edited.write_text(
        "id,title\n"
        "t-2,Write getting started docs\n"
        ",Add release checklist\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    query = ResourceQuery(columns=["id", "title"], where="status = 'doing'")
    plan = build_mutation_plan_for_view(project, "tasks", query, edited)

    assert len(plan.inserts) == 1
    assert plan.inserts[0].row["status"] == "doing"
    assert plan.inferred_defaults == {"status": "doing"}

    apply_mutation_plan(project, plan)
    tasks_csv = (example_project / "tasks.csv").read_text(encoding="utf-8")
    assert "Add release checklist" in tasks_csv
    assert ",Add release checklist,doing," in tasks_csv


def test_build_mutation_plan_for_composite_key_table(example_project: Path, tmp_path: Path) -> None:
    (example_project / "memberships.csv").write_text(
        "org_id,user_id,role\n"
        "o-1,u-1,admin\n"
        "o-1,u-2,member\n",
        encoding="utf-8",
    )
    (example_project / ".lenzdb" / "schema" / "memberships.yaml").write_text(
        "kind: table\n"
        "name: memberships\n"
        "path: memberships.csv\n"
        "table: memberships\n"
        "primary_key: [org_id, user_id]\n"
        "columns:\n"
        "  org_id:\n"
        "    type: string\n"
        "    immutable: true\n"
        "  user_id:\n"
        "    type: string\n"
        "    immutable: true\n"
        "  role:\n"
        "    type: string\n",
        encoding="utf-8",
    )
    edited = tmp_path / "memberships.csv"
    edited.write_text(
        "org_id,user_id,role\n"
        "o-1,u-1,owner\n"
        "o-1,u-2,member\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    plan = build_mutation_plan(project, "memberships", edited)

    assert plan.primary_key_outputs == ["org_id", "user_id"]
    assert len(plan.updates) == 1
    assert plan.updates[0].key == "o-1 | u-1"
    assert plan.updates[0].changes == {"role": "owner"}


def test_edit_command_auto_adds_untracked_lens_manifest(example_project: Path, tmp_path: Path) -> None:
    lens_path = example_project / "custom_report.sql"
    lens_path.write_text("select id, title from tasks order by id\n", encoding="utf-8")
    editor = tmp_path / "noop-editor.sh"
    editor.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    editor.chmod(0o755)

    result = CliRunner().invoke(
        app, ["edit", "custom_report", "--project", str(example_project), "--editor", str(editor)]
    )

    assert result.exit_code == 0
    assert "Info: auto-added untracked lens custom_report." in result.stderr
    assert (example_project / ".lenzdb" / "schema" / "custom_report.yaml").exists()
