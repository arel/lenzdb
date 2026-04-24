from __future__ import annotations

from pathlib import Path

import pytest

from lenzdb.cli import app
from lenzdb.errors import MutationError
from lenzdb.planner import apply_mutation_plan, build_mutation_plan
from lenzdb.project import Project


def test_plan_updates_existing_rows(example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "edited.csv"
    edited.write_text(
        "id,title,status,project_name\n"
        "t-1,Ship production CLI,doing,Core Platform\n"
        "t-2,Write GETTING started docs,doing,Docs Refresh\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    plan = build_mutation_plan(project, "open_tasks", edited)

    assert len(plan.updates) == 1
    assert plan.updates[0].changes == {"title": "Ship production CLI", "status": "doing"}
    assert not plan.inserts


def test_plan_inserts_and_creates_reference(example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "edited.csv"
    edited.write_text(
        "id,title,status,project_name\n"
        "t-1,Ship CLI skeleton,todo,Core Platform\n"
        "t-2,Write GETTING started docs,doing,Docs Refresh\n"
        ",Add release checklist,todo,Launch Ops\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    plan = build_mutation_plan(project, "open_tasks", edited)

    assert len(plan.inserts) == 1
    assert len(plan.reference_creations) == 1
    assert len(plan.generated_primary_keys) == 1
    new_task = plan.inserts[0].row
    assert new_task["title"] == "Add release checklist"
    assert new_task["status"] == "todo"


def test_apply_updates_source_files(example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "edited.csv"
    edited.write_text(
        "id,title,status,project_name\n"
        "t-1,Ship production CLI,doing,Core Platform\n"
        "t-2,Write GETTING started docs,doing,Docs Refresh\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    plan = build_mutation_plan(project, "open_tasks", edited)
    apply_mutation_plan(project, plan)

    tasks_csv = (example_project / "tasks.csv").read_text(encoding="utf-8")
    assert "Ship production CLI" in tasks_csv
    assert "t-1,Ship production CLI,doing,p-1" in tasks_csv
    assert b"\r\n" not in (example_project / "tasks.csv").read_bytes()


def test_table_resource_plan_and_apply(example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "tasks.csv"
    edited.write_text(
        "id,title,status,project_id\n"
        "t-1,Ship production CLI,doing,p-1\n"
        "t-2,Write GETTING started docs,doing,p-2\n"
        "t-3,Close phase zero,done,p-1\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    plan = build_mutation_plan(project, "tasks", edited)

    assert plan.primary_table == "main.tasks"
    assert len(plan.updates) == 1
    assert plan.updates[0].changes == {"title": "Ship production CLI", "status": "doing"}

    apply_mutation_plan(project, plan)
    tasks_csv = (example_project / "tasks.csv").read_text(encoding="utf-8")
    assert "t-1,Ship production CLI,doing,p-1" in tasks_csv


def test_table_resource_rejects_primary_key_edits(example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "tasks.csv"
    edited.write_text(
        "id,title,status,project_id\n"
        "renamed,Ship CLI skeleton,todo,p-1\n"
        "t-2,Write GETTING started docs,doing,p-2\n"
        "t-3,Close phase zero,done,p-1\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    with pytest.raises(MutationError, match="Deleting rows"):
        build_mutation_plan(project, "tasks", edited)


def test_deletions_are_rejected(example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "edited.csv"
    edited.write_text(
        "id,title,status,project_name\nt-1,Ship CLI skeleton,todo,Core Platform\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    with pytest.raises(MutationError, match="Deleting rows"):
        build_mutation_plan(project, "open_tasks", edited)


def test_computed_column_edits_are_rejected(example_project: Path, tmp_path: Path) -> None:
    lens_path = example_project / "computed.sql"
    lens_path.write_text(
        "select t.id, upper(t.title) as loud_title from tasks t where t.status != 'done'",
        encoding="utf-8",
    )
    edited = tmp_path / "edited.csv"
    edited.write_text(
        "id,loud_title\nt-1,LOUDER\nt-2,WRITE GETTING STARTED DOCS\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    with pytest.raises(MutationError, match="not writable"):
        build_mutation_plan(project, "computed", edited)


def test_apply_command_and_edit_command(runner, example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "edited.csv"
    edited.write_text(
        "id,title,status,project_name\n"
        "t-1,Ship production CLI,doing,Core Platform\n"
        "t-2,Write GETTING started docs,doing,Docs Refresh\n",
        encoding="utf-8",
    )

    apply_result = runner.invoke(
        app,
        ["apply", "open_tasks", str(edited), "--project", str(example_project)],
    )
    assert apply_result.exit_code == 0
    assert "Changes applied." in apply_result.stdout
    assert "Apply these changes?" not in apply_result.stdout

    editor_script = tmp_path / "edit.sh"
    editor_script.write_text(
        "#!/usr/bin/env bash\n"
        "python - \"$1\" <<'PY'\n"
        "from pathlib import Path\n"
        "path = Path(__import__('sys').argv[1])\n"
        "text = path.read_text(encoding='utf-8')\n"
        "path.write_text(text.replace('Write GETTING started docs', 'Refresh docs'), encoding='utf-8')\n"
        "PY\n",
        encoding="utf-8",
    )
    editor_script.chmod(0o755)

    edit_result = runner.invoke(
        app,
        [
            "edit",
            "open_tasks",
            "--project",
            str(example_project),
            "--editor",
            str(editor_script),
        ],
    )
    assert edit_result.exit_code == 0
    assert "Changes applied." in edit_result.stdout
    assert "Apply these changes?" not in edit_result.stdout

    tasks_csv = (example_project / "tasks.csv").read_text(encoding="utf-8")
    assert "Refresh docs" in tasks_csv
