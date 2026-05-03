from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from lenzdb.cli import app, complete_project_resource, output_width, selected_pager


def test_version_flag() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "lnz 0.1.5"


def test_view_table_and_lens_markdown(example_project: Path) -> None:
    runner = CliRunner()
    table_result = runner.invoke(
        app, ["view", "tasks", "--project", str(example_project), "--format", "markdown"]
    )
    lens_result = runner.invoke(
        app, ["view", "open_tasks", "--project", str(example_project), "--format", "markdown"]
    )

    assert table_result.exit_code == 0
    assert "| id | title | status | project_id |" in table_result.stdout
    assert "Ship CLI skeleton" in table_result.stdout
    assert lens_result.exit_code == 0
    assert "| id | title | status | project_name |" in lens_result.stdout
    assert "Docs Refresh" in lens_result.stdout


def test_view_untracked_table_without_lenzdb_uses_temporary_schema(example_project: Path) -> None:
    lenz_dir = example_project / ".lenzdb"
    for path in sorted(lenz_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()
    lenz_dir.rmdir()

    result = CliRunner().invoke(
        app, ["view", "tasks", "--project", str(example_project), "--format", "markdown"]
    )

    assert result.exit_code == 0
    assert "| id | title | status | project_id |" in result.stdout
    assert "Info: using temporary schema for untracked table tasks" in result.stderr


def test_describe_lens_and_table(example_project: Path) -> None:
    runner = CliRunner()
    table_result = runner.invoke(
        app, ["describe", "tasks", "--project", str(example_project), "--format", "markdown"]
    )
    lens_result = runner.invoke(
        app, ["describe", "open_tasks", "--project", str(example_project), "--format", "markdown"]
    )

    assert table_result.exit_code == 0
    assert "| column | type | primary_key | editable |" in table_result.stdout
    assert "| id | VARCHAR | yes | no |" in table_result.stdout
    assert "| project_id | VARCHAR | no | yes |" in table_result.stdout
    assert lens_result.exit_code == 0
    assert "| project_name | VARCHAR | no | no |" in lens_result.stdout


def test_list_resources_shows_flat_names_and_states(example_project: Path) -> None:
    result = CliRunner().invoke(app, ["list", "--project", str(example_project), "--format", "markdown"])

    assert result.exit_code == 0
    assert "| kind | name | path | state |" in result.stdout
    assert "| table | tasks | tasks.csv | added |" in result.stdout
    assert "| lens | open_tasks | open_tasks.sql | added |" in result.stdout
    assert "| lens | all_tasks | all_tasks.sql | untracked |" in result.stdout


def test_list_marks_shadowed_duplicates(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    from shutil import copytree

    copytree(Path("examples/basic"), project_root)
    (project_root / ".lenzdb" / "data").mkdir(parents=True, exist_ok=True)
    (project_root / ".lenzdb" / "data" / "tasks.csv").write_text(
        "id,title,status,project_id\n"
        "shadow-1,Shadow task,todo,p-1\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["list", "--project", str(project_root), "--format", "markdown"])

    assert result.exit_code == 0
    assert "| table | tasks | .lenzdb/data/tasks.csv | shadowed |" in result.stdout


def test_view_ignores_unrelated_invalid_csv_in_current_dir(
    example_project: Path, monkeypatch
) -> None:
    workdir = example_project / "work"
    workdir.mkdir()
    (workdir / "projects..csv").write_text("id,name\np-1,Broken\n", encoding="utf-8")
    monkeypatch.chdir(workdir)

    result = CliRunner().invoke(
        app, ["view", "tasks", "--project", str(example_project), "--format", "markdown"]
    )

    assert result.exit_code == 0
    assert "Ship CLI skeleton" in result.stdout
    assert "Write getting started docs" in result.stdout


def test_dotted_lens_names_are_literal(example_project: Path) -> None:
    (example_project / "report.v1.sql").write_text("select id, title from tasks order by id\n", encoding="utf-8")

    result = CliRunner().invoke(
        app, ["view", "report.v1", "--project", str(example_project), "--format", "markdown"]
    )

    assert result.exit_code == 0
    assert "| id | title |" in result.stdout
    assert "Ship CLI skeleton" in result.stdout


def test_add_registers_table_and_lens_manifests(example_project: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    (example_project / "new_table.csv").write_text("key,name\nn-1,New row\n", encoding="utf-8")
    (example_project / "new_lens.sql").write_text("select key, name from new_table\n", encoding="utf-8")

    table_result = runner.invoke(
        app, ["add", "new_table", "--primary-key", "key", "--project", str(example_project)]
    )
    lens_result = runner.invoke(app, ["add", "new_lens.sql", "--project", str(example_project)])

    assert table_result.exit_code == 0
    assert "Added table new_table" in table_result.stdout
    assert (example_project / ".lenzdb" / "schema" / "new_table.yaml").exists()
    assert lens_result.exit_code == 0
    assert "Added lens new_lens" in lens_result.stdout
    assert (example_project / ".lenzdb" / "schema" / "new_lens.yaml").exists()


def test_add_lens_rejects_primary_key_option(example_project: Path) -> None:
    (example_project / "temp_lens.sql").write_text("select id from tasks\n", encoding="utf-8")

    result = CliRunner().invoke(
        app, ["add", "temp_lens.sql", "--primary-key", "id", "--project", str(example_project)]
    )

    assert result.exit_code == 1
    assert "--primary-key is only valid for CSV tables" in result.stderr


def test_project_resource_completion(example_project: Path, monkeypatch) -> None:
    monkeypatch.setenv("LENZDB_PROJECT_ROOT", str(example_project))
    completions = complete_project_resource("ta")

    assert "tasks" in completions
    assert "main.tasks" not in completions


def test_page_size_env_zero_disables_pagination(example_project: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["view", "tasks", "--project", str(example_project), "--format", "markdown"],
        env={"LENZDB_PAGE_SIZE": "0"},
    )

    assert result.exit_code == 0
    assert "Ship CLI skeleton" in result.stdout
    assert "Close phase zero" in result.stdout


def test_selected_pager_env_precedence(monkeypatch) -> None:
    monkeypatch.setenv("PAGER", "plain-pager")
    monkeypatch.setenv("LENZDB_PAGER", "lenz-pager")

    assert selected_pager() == "lenz-pager"


def test_output_width_env_precedence(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LENZDB_COLUMNS", "42")

    assert output_width() == 42
