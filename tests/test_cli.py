from __future__ import annotations

from pathlib import Path

from lenzdb.cli import app


def test_view_markdown(runner, example_project: Path) -> None:
    result = runner.invoke(
        app, ["view", "open_tasks", "--project", str(example_project), "--format", "markdown"]
    )
    assert result.exit_code == 0
    assert "| id | title | status | project_name |" in result.stdout
    assert "Ship CLI skeleton" in result.stdout


def test_view_table_output_is_not_duplicated(runner, example_project: Path) -> None:
    result = runner.invoke(app, ["view", "open_tasks", "--project", str(example_project)])
    assert result.exit_code == 0
    assert result.stdout.count("Ship CLI skeleton") == 1


def test_project_root_env_var(runner, example_project: Path, monkeypatch) -> None:
    monkeypatch.setenv("LENZDB_PROJECT_ROOT", str(example_project))
    result = runner.invoke(app, ["view", "open_tasks", "--format", "markdown"])
    assert result.exit_code == 0
    assert "Ship CLI skeleton" in result.stdout


def test_view_qualified_lens_and_sql_tables(runner, example_project: Path) -> None:
    (example_project / "qualified_tasks.sql").write_text(
        "select t.id, t.title, p.name as project_name "
        "from main.tasks as t "
        "join main.projects as p on p.id = t.project_id "
        "order by t.id\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["view", "main.qualified_tasks", "--project", str(example_project), "--format", "markdown"]
    )

    assert result.exit_code == 0
    assert "| id | title | project_name |" in result.stdout
    assert "Ship CLI skeleton" in result.stdout


def test_view_rejects_unknown_sql_table_namespace(runner, example_project: Path) -> None:
    (example_project / "bad_namespace.sql").write_text(
        "select * from archive.tasks\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["view", "bad_namespace", "--project", str(example_project), "--format", "markdown"]
    )

    assert result.exit_code == 1
    assert "Unknown table namespace 'archive'" in result.stderr


def test_check_and_explain(runner, example_project: Path) -> None:
    check_result = runner.invoke(app, ["check", "--project", str(example_project)])
    assert check_result.exit_code == 0
    assert "Project check passed." in check_result.stdout

    explain_result = runner.invoke(
        app, ["explain", "open_tasks", "--project", str(example_project)]
    )
    assert explain_result.exit_code == 0
    assert "Writable: yes" in explain_result.stdout
    assert "project_name" in explain_result.stdout


def test_diff_command(runner, example_project: Path, tmp_path: Path) -> None:
    edited = tmp_path / "edited.csv"
    edited.write_text(
        "id,title,status,project_name\n"
        "t-1,Ship real CLI,todo,Core Platform\n"
        "t-2,Write getting started docs,doing,Docs Refresh\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["diff", "open_tasks", str(edited), "--project", str(example_project)]
    )
    assert result.exit_code == 0
    assert "UPDATED t-1" in result.stdout
    assert "Ship CLI skeleton" in result.stdout
