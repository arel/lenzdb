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


def test_edit_prefers_lenzdb_editor_over_editor(
    runner, example_project: Path, tmp_path: Path
) -> None:
    editor_script = tmp_path / "lenzdb_edit.sh"
    editor_script.write_text(
        "#!/usr/bin/env bash\n"
        "python - \"$1\" <<'PY'\n"
        "from pathlib import Path\n"
        "path = Path(__import__('sys').argv[1])\n"
        "text = path.read_text(encoding='utf-8')\n"
        "path.write_text(text.replace('Ship CLI skeleton', 'Ship env edit'), encoding='utf-8')\n"
        "PY\n",
        encoding="utf-8",
    )
    editor_script.chmod(0o755)

    result = runner.invoke(
        app,
        ["edit", "open_tasks", "--project", str(example_project)],
        env={"LENZDB_EDITOR": str(editor_script), "EDITOR": "false"},
    )

    assert result.exit_code == 0
    assert "Changes applied." in result.stdout
    assert "Ship env edit" in (example_project / "tasks.csv").read_text(encoding="utf-8")


def test_edit_preserves_failed_edit_and_recovers_next_time(
    runner, example_project: Path, tmp_path: Path
) -> None:
    failing_editor = tmp_path / "fail_edit.sh"
    failing_editor.write_text(
        "#!/usr/bin/env bash\n"
        "python - \"$1\" <<'PY'\n"
        "from pathlib import Path\n"
        "path = Path(__import__('sys').argv[1])\n"
        "path.write_text('id,title,status,project_name\\n"
        "t-1,Ship CLI skeleton,todo,Core Platform\\n', encoding='utf-8')\n"
        "PY\n",
        encoding="utf-8",
    )
    failing_editor.chmod(0o755)

    failed_result = runner.invoke(
        app,
        [
            "edit",
            "open_tasks",
            "--project",
            str(example_project),
            "--editor",
            str(failing_editor),
        ],
    )

    assert failed_result.exit_code == 1
    assert "Edited file preserved at:" in failed_result.stderr
    recovery_files = sorted((example_project / ".lenzdb" / "recovery").glob("main.open_tasks-*.csv"))
    assert len(recovery_files) == 1
    assert "t-2" not in recovery_files[0].read_text(encoding="utf-8")

    marker = tmp_path / "used_recovery"
    recovering_editor = tmp_path / "recover_edit.sh"
    recovering_editor.write_text(
        "#!/usr/bin/env bash\n"
        "python - \"$2\" \"$1\" <<'PY'\n"
        "from pathlib import Path\n"
        "path = Path(__import__('sys').argv[1])\n"
        "marker = Path(__import__('sys').argv[2])\n"
        "text = path.read_text(encoding='utf-8')\n"
        "if 't-2' not in text:\n"
        "    marker.write_text('yes', encoding='utf-8')\n"
        "    text += 't-2,Write GETTING started docs,doing,Docs Refresh\\n'\n"
        "text = text.replace('Ship CLI skeleton', 'Ship recovered edit')\n"
        "path.write_text(text, encoding='utf-8')\n"
        "PY\n",
        encoding="utf-8",
    )
    recovering_editor.chmod(0o755)

    recovered_result = runner.invoke(
        app,
        [
            "edit",
            "open_tasks",
            "--project",
            str(example_project),
            "--editor",
            f"{recovering_editor} {marker}",
        ],
    )

    assert recovered_result.exit_code == 0
    assert "Recovered previous failed edit:" in recovered_result.stdout
    assert marker.read_text(encoding="utf-8") == "yes"
    assert "Ship recovered edit" in (example_project / "tasks.csv").read_text(encoding="utf-8")
    assert not list((example_project / ".lenzdb" / "recovery").glob("main.open_tasks-*.csv"))
