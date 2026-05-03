from __future__ import annotations

from pathlib import Path

import pytest

from lenzdb.cli import app
from lenzdb.errors import MutationError
from lenzdb.engine import ResourceQuery
from lenzdb.planner import apply_mutation_plan, build_mutation_plan, build_mutation_plan_for_view
from lenzdb.project import Project


def add_memberships_table(example_project: Path) -> None:
    (example_project / "memberships.csv").write_text(
        "org_id,user_id,role\n"
        "o-1,u-1,admin\n"
        "o-1,u-2,member\n",
        encoding="utf-8",
    )
    (example_project / ".lenzdb" / "schema" / "memberships.yaml").write_text(
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


def test_plan_updates_existing_rows(example_project: Path, tmp_path: Path) -> None:
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


def test_plan_inserts_and_creates_reference(example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "edited.csv"
    edited.write_text(
        "id,title,status,project_name\n"
        "t-1,Ship CLI skeleton,todo,Core Platform\n"
        "t-2,Write getting started docs,doing,Docs Refresh\n"
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


def test_plan_applies_defaults_from_view_filters(example_project: Path, tmp_path: Path) -> None:
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


def test_apply_updates_source_files(example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "edited.csv"
    edited.write_text(
        "id,title,status,project_name\n"
        "t-1,Ship production CLI,doing,Core Platform\n"
        "t-2,Write getting started docs,doing,Docs Refresh\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    plan = build_mutation_plan(project, "open_tasks", edited)
    apply_mutation_plan(project, plan)

    tasks_csv = (example_project / "tasks.csv").read_text(encoding="utf-8")
    assert "Ship production CLI" in tasks_csv
    assert "t-1,Ship production CLI,doing,p-1" in tasks_csv
    assert b"\r\n" not in (example_project / "tasks.csv").read_bytes()


def test_plan_updates_primary_table_columns_without_policy_or_ref_join_metadata(
    example_project: Path, tmp_path: Path
) -> None:
    policy_path = example_project / ".lenzdb" / "policies" / "open_tasks.yaml"
    policy_path.unlink()
    schema_path = example_project / ".lenzdb" / "schema" / "tasks.yaml"
    schema_path.write_text(
        "table: tasks\n"
        "primary_key: id\n"
        "columns:\n"
        "  id:\n"
        "    type: string\n"
        "    immutable: true\n"
        "  title:\n"
        "    type: string\n"
        "  status:\n"
        "    type: enum\n"
        "    values: [todo, doing, done]\n"
        "  project_id:\n"
        "    type: string\n",
        encoding="utf-8",
    )
    edited = tmp_path / "edited.csv"
    edited.write_text(
        "id,title,status,project_name\n"
        "t-1,Ship CLI skeleton,done,Core Platform\n"
        "t-2,Write getting started docs,doing,Docs Refresh\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    plan = build_mutation_plan(project, "open_tasks", edited)

    assert len(plan.updates) == 1
    assert plan.updates[0].changes == {"status": "done"}


def test_plan_rejects_joined_column_edits_without_policy(
    example_project: Path, tmp_path: Path
) -> None:
    policy_path = example_project / ".lenzdb" / "policies" / "open_tasks.yaml"
    policy_path.unlink()
    schema_path = example_project / ".lenzdb" / "schema" / "tasks.yaml"
    schema_path.write_text(
        "table: tasks\n"
        "primary_key: id\n"
        "columns:\n"
        "  id:\n"
        "    type: string\n"
        "    immutable: true\n"
        "  title:\n"
        "    type: string\n"
        "  status:\n"
        "    type: enum\n"
        "    values: [todo, doing, done]\n"
        "  project_id:\n"
        "    type: string\n",
        encoding="utf-8",
    )
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


def test_table_resource_plan_and_apply(example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "tasks.csv"
    edited.write_text(
        "id,title,status,project_id\n"
        "t-1,Ship production CLI,doing,p-1\n"
        "t-2,Write getting started docs,doing,p-2\n"
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


def test_composite_key_table_resource_plan_and_apply(
    example_project: Path, tmp_path: Path
) -> None:
    add_memberships_table(example_project)
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

    apply_mutation_plan(project, plan)
    memberships_csv = (example_project / "memberships.csv").read_text(encoding="utf-8")
    assert "o-1,u-1,owner" in memberships_csv


def test_composite_key_table_resource_inserts_require_all_key_values(
    example_project: Path, tmp_path: Path
) -> None:
    add_memberships_table(example_project)
    edited = tmp_path / "memberships.csv"
    edited.write_text(
        "org_id,user_id,role\n"
        "o-1,u-1,admin\n"
        "o-1,u-2,member\n"
        "o-2,,member\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    with pytest.raises(MutationError, match="must supply primary key output"):
        build_mutation_plan(project, "memberships", edited)


def test_composite_key_table_resource_rejects_key_edits(
    example_project: Path, tmp_path: Path
) -> None:
    add_memberships_table(example_project)
    edited = tmp_path / "memberships.csv"
    edited.write_text(
        "org_id,user_id,role\n"
        "o-2,u-1,admin\n"
        "o-1,u-2,member\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    with pytest.raises(MutationError, match="Deleting rows"):
        build_mutation_plan(project, "memberships", edited)


def test_composite_key_lens_plan_and_apply(example_project: Path, tmp_path: Path) -> None:
    add_memberships_table(example_project)
    (example_project / "membership_roles.sql").write_text(
        "select org_id, user_id, role from memberships\n",
        encoding="utf-8",
    )
    edited = tmp_path / "membership_roles.csv"
    edited.write_text(
        "org_id,user_id,role\n"
        "o-1,u-1,admin\n"
        "o-1,u-2,owner\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    plan = build_mutation_plan(project, "membership_roles", edited)

    assert len(plan.updates) == 1
    assert plan.updates[0].key == "o-1 | u-2"
    assert plan.updates[0].changes == {"role": "owner"}

    apply_mutation_plan(project, plan)
    assert "o-1,u-2,owner" in (example_project / "memberships.csv").read_text(
        encoding="utf-8"
    )


def test_table_resource_rejects_primary_key_edits(example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "tasks.csv"
    edited.write_text(
        "id,title,status,project_id\n"
        "renamed,Ship CLI skeleton,todo,p-1\n"
        "t-2,Write getting started docs,doing,p-2\n"
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
        "t-2,Write getting started docs,doing,Docs Refresh\n",
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
        "path.write_text(text.replace('Write getting started docs', 'Refresh docs'), encoding='utf-8')\n"
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


def test_edit_columns_dynamic_view(runner, example_project: Path, tmp_path: Path) -> None:
    editor_script = tmp_path / "edit_columns.sh"
    editor_script.write_text(
        "#!/usr/bin/env bash\n"
        "python - \"$1\" <<'PY'\n"
        "from pathlib import Path\n"
        "path = Path(__import__('sys').argv[1])\n"
        "text = path.read_text(encoding='utf-8')\n"
        "assert 'id,title\\n' in text\n"
        "assert 'status' not in text\n"
        "path.write_text(text.replace('Ship CLI skeleton', 'Ship focused edit'), encoding='utf-8')\n"
        "PY\n",
        encoding="utf-8",
    )
    editor_script.chmod(0o755)

    result = runner.invoke(
        app,
        [
            "edit",
            "open_tasks",
            "--project",
            str(example_project),
            "--editor",
            str(editor_script),
            "--columns",
            "id,title",
        ],
    )

    assert result.exit_code == 0
    tasks_csv = (example_project / "tasks.csv").read_text(encoding="utf-8")
    assert "t-1,Ship focused edit,todo,p-1" in tasks_csv


def test_edit_dynamic_view_requires_primary_key_before_editor(
    runner, example_project: Path, tmp_path: Path
) -> None:
    marker = tmp_path / "editor_ran"
    editor_script = tmp_path / "edit_without_pk.sh"
    editor_script.write_text(
        "#!/usr/bin/env bash\n"
        f"touch {marker}\n",
        encoding="utf-8",
    )
    editor_script.chmod(0o755)

    result = runner.invoke(
        app,
        [
            "edit",
            "open_tasks",
            "--project",
            str(example_project),
            "--editor",
            str(editor_script),
            "--columns",
            "title",
        ],
    )

    assert result.exit_code == 1
    assert "dynamic edit view must include primary key output column" in result.stderr
    assert not marker.exists()


def test_edit_filter_dynamic_view(runner, example_project: Path, tmp_path: Path) -> None:
    editor_script = tmp_path / "edit_filter.sh"
    editor_script.write_text(
        "#!/usr/bin/env bash\n"
        "python - \"$1\" <<'PY'\n"
        "from pathlib import Path\n"
        "path = Path(__import__('sys').argv[1])\n"
        "text = path.read_text(encoding='utf-8')\n"
        "assert 't-2,Write getting started docs' in text\n"
        "assert 't-1,Ship CLI skeleton' not in text\n"
        "path.write_text(text.replace('Write getting started docs', 'Write filtered docs'), encoding='utf-8')\n"
        "PY\n",
        encoding="utf-8",
    )
    editor_script.chmod(0o755)

    result = runner.invoke(
        app,
        [
            "edit",
            "open_tasks",
            "--project",
            str(example_project),
            "--editor",
            str(editor_script),
            "--filter",
            "status = 'doing'",
        ],
    )

    assert result.exit_code == 0
    tasks_csv = (example_project / "tasks.csv").read_text(encoding="utf-8")
    assert "t-2,Write filtered docs,doing,p-2" in tasks_csv


def test_edit_page_dynamic_view(runner, example_project: Path, tmp_path: Path) -> None:
    editor_script = tmp_path / "edit_page.sh"
    editor_script.write_text(
        "#!/usr/bin/env bash\n"
        "python - \"$1\" <<'PY'\n"
        "from pathlib import Path\n"
        "path = Path(__import__('sys').argv[1])\n"
        "text = path.read_text(encoding='utf-8')\n"
        "assert 't-2,Write getting started docs' in text\n"
        "assert 't-1,Ship CLI skeleton' not in text\n"
        "path.write_text(text.replace('Write getting started docs', 'Write paged docs'), encoding='utf-8')\n"
        "PY\n",
        encoding="utf-8",
    )
    editor_script.chmod(0o755)

    result = runner.invoke(
        app,
        [
            "edit",
            "tasks",
            "--project",
            str(example_project),
            "--editor",
            str(editor_script),
            "--order",
            "id",
            "--page",
            "2",
            "--page-size",
            "1",
        ],
    )

    assert result.exit_code == 0
    tasks_csv = (example_project / "tasks.csv").read_text(encoding="utf-8")
    assert "t-2,Write paged docs,doing,p-2" in tasks_csv


def test_edit_dynamic_recovery_files_are_shape_specific(
    runner, example_project: Path, tmp_path: Path
) -> None:
    editor_script = tmp_path / "fail_dynamic_edit.sh"
    editor_script.write_text(
        "#!/usr/bin/env bash\n"
        "python - \"$1\" <<'PY'\n"
        "from pathlib import Path\n"
        "path = Path(__import__('sys').argv[1])\n"
        "path.write_text('id,title\\n"
        "t-1,Ship CLI skeleton\\n', encoding='utf-8')\n"
        "PY\n",
        encoding="utf-8",
    )
    editor_script.chmod(0o755)

    result = runner.invoke(
        app,
        [
            "edit",
            "open_tasks",
            "--project",
            str(example_project),
            "--editor",
            str(editor_script),
            "--columns",
            "id,title",
        ],
    )

    assert result.exit_code == 1
    recovery_dir = example_project / ".lenzdb" / "recovery"
    assert list(recovery_dir.glob("main.open_tasks.view.*-*.csv"))
    assert not list(recovery_dir.glob("main.open_tasks-*.csv"))


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


def test_edit_untracked_table_without_lenzdb_uses_temporary_schema(
    runner, example_project: Path, tmp_path: Path
) -> None:
    lenz_dir = example_project / ".lenzdb"
    for path in sorted(lenz_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()
    lenz_dir.rmdir()

    editor_script = tmp_path / "edit_tasks.sh"
    editor_script.write_text(
        "#!/usr/bin/env bash\n"
        "python - \"$1\" <<'PY'\n"
        "from pathlib import Path\n"
        "path = Path(__import__('sys').argv[1])\n"
        "text = path.read_text(encoding='utf-8')\n"
        "path.write_text(text.replace('Write getting started docs', 'Write docs without schema'), encoding='utf-8')\n"
        "PY\n",
        encoding="utf-8",
    )
    editor_script.chmod(0o755)

    result = runner.invoke(
        app,
        ["edit", "tasks", "--project", str(example_project), "--editor", str(editor_script)],
    )

    assert result.exit_code == 0
    assert "Changes applied." in result.stdout
    assert "Info: auto-added untracked table main.tasks with primary key 'id'." in result.stderr
    assert (example_project / ".lenzdb" / "schema" / "main.tasks.yaml").exists()
    assert "Write docs without schema" in (example_project / "tasks.csv").read_text(
        encoding="utf-8"
    )


def test_edit_auto_adds_untracked_dependencies_with_default_id_pk(
    runner, example_project: Path, tmp_path: Path
) -> None:
    (example_project / ".lenzdb" / "schema" / "tasks.yaml").unlink()
    (example_project / ".lenzdb" / "schema" / "projects.yaml").unlink()

    editor_script = tmp_path / "edit_untracked.sh"
    editor_script.write_text(
        "#!/usr/bin/env bash\n"
        "python - \"$1\" <<'PY'\n"
        "from pathlib import Path\n"
        "path = Path(__import__('sys').argv[1])\n"
        "text = path.read_text(encoding='utf-8')\n"
        "path.write_text(text.replace('Ship CLI skeleton,todo', 'Ship CLI skeleton,done'), encoding='utf-8')\n"
        "PY\n",
        encoding="utf-8",
    )
    editor_script.chmod(0o755)

    result = runner.invoke(
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

    assert result.exit_code == 0
    assert "Info: auto-added untracked table main.tasks with primary key 'id'." in result.stderr
    assert "Info: auto-added untracked table main.projects with primary key 'id'." in result.stderr
    assert (example_project / ".lenzdb" / "schema" / "main.tasks.yaml").exists()
    assert (example_project / ".lenzdb" / "schema" / "main.projects.yaml").exists()
    assert "t-1,Ship CLI skeleton,done,p-1" in (example_project / "tasks.csv").read_text(
        encoding="utf-8"
    )


def test_edit_errors_when_untracked_dependency_lacks_default_id_pk(
    runner, example_project: Path, tmp_path: Path
) -> None:
    (example_project / ".lenzdb" / "schema" / "tasks.yaml").unlink()
    (example_project / ".lenzdb" / "schema" / "projects.yaml").unlink()
    (example_project / "projects.csv").write_text(
        "key,name\n"
        "p-1,Core Platform\n"
        "p-2,Docs Refresh\n",
        encoding="utf-8",
    )
    (example_project / "open_tasks.sql").write_text(
        "select\n"
        "  t.id,\n"
        "  t.title,\n"
        "  t.status,\n"
        "  p.name as project_name\n"
        "from tasks as t\n"
        "join projects as p on p.key = t.project_id\n"
        "where t.status != 'done'\n"
        "order by t.id\n",
        encoding="utf-8",
    )
    editor_script = tmp_path / "noop.sh"
    editor_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    editor_script.chmod(0o755)

    result = runner.invoke(
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

    assert result.exit_code == 1
    assert "Info: auto-added untracked table main.tasks with primary key 'id'." in result.stderr
    assert (
        "depends on untracked tables without a default primary key column 'id': main.projects"
        in result.stderr
    )


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
        "    text += 't-2,Write getting started docs,doing,Docs Refresh\\n'\n"
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
