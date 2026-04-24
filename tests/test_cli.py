from __future__ import annotations

from pathlib import Path

from lenzdb.cli import app, complete_project_resource


def test_view_markdown(runner, example_project: Path) -> None:
    result = runner.invoke(
        app, ["view", "open_tasks", "--project", str(example_project), "--format", "markdown"]
    )
    assert result.exit_code == 0
    assert "| id | title | status | project_name |" in result.stdout
    assert "Ship CLI skeleton" in result.stdout


def test_view_table_markdown(runner, example_project: Path) -> None:
    result = runner.invoke(
        app, ["view", "tasks", "--project", str(example_project), "--format", "markdown"]
    )
    assert result.exit_code == 0
    assert "| id | title | status | project_id |" in result.stdout
    assert "p-1" in result.stdout


def test_view_table_output_is_not_duplicated(runner, example_project: Path) -> None:
    result = runner.invoke(app, ["view", "open_tasks", "--project", str(example_project)])
    assert result.exit_code == 0
    assert result.stdout.count("Ship CLI skeleton") == 1


def test_project_root_env_var(runner, example_project: Path, monkeypatch) -> None:
    monkeypatch.setenv("LENZDB_PROJECT_ROOT", str(example_project))
    result = runner.invoke(app, ["view", "open_tasks", "--format", "markdown"])
    assert result.exit_code == 0
    assert "Ship CLI skeleton" in result.stdout


def test_project_resource_completion(example_project: Path, monkeypatch) -> None:
    monkeypatch.setenv("LENZDB_PROJECT_ROOT", str(example_project))
    completions = complete_project_resource("ta")
    assert "tasks" in completions
    assert "main.tasks" not in completions
    assert complete_project_resource("main.o") == ["main.open_tasks"]


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


def test_view_allows_readonly_cte_lens(runner, example_project: Path) -> None:
    (example_project / "cte_tasks.sql").write_text(
        "with todo_tasks as (select id, title from tasks where status = 'todo') "
        "select * from todo_tasks\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["view", "cte_tasks", "--project", str(example_project), "--format", "markdown"]
    )

    assert result.exit_code == 0
    assert "Ship CLI skeleton" in result.stdout


def test_view_registered_namespaced_lens_and_unqualified_table(
    runner, example_project: Path
) -> None:
    imports = example_project / "imports"
    reports = example_project / "reports"
    imports.mkdir()
    reports.mkdir()
    (imports / "notes.csv").write_text("id,text\nn-1,Useful\n", encoding="utf-8")
    (reports / "notes_view.sql").write_text("select id, text from notes\n", encoding="utf-8")
    (example_project / ".lenzdb" / "schema" / "client_notes.yaml").write_text(
        "table: client.notes\n"
        "primary_key: id\n"
        "columns:\n"
        "  id:\n"
        "    type: string\n"
        "    immutable: true\n"
        "  text:\n"
        "    type: string\n",
        encoding="utf-8",
    )
    (example_project / ".lenzdb" / "project.yaml").write_text(
        "tables:\n"
        "  - path: imports/*.csv\n"
        "    namespace: client\n"
        "lenses:\n"
        "  - path: reports/*.sql\n"
        "    namespace: client\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["view", "client.notes_view", "--project", str(example_project), "--format", "markdown"]
    )

    assert result.exit_code == 0
    assert "| id | text |" in result.stdout
    assert "Useful" in result.stdout


def test_list_resources(runner, example_project: Path) -> None:
    result = runner.invoke(app, ["list", "--project", str(example_project), "--format", "markdown"])
    assert result.exit_code == 0
    assert "| kind | namespace | name | path |" in result.stdout
    assert "| table | main | tasks | tasks.csv |" in result.stdout
    assert "| lens | main | open_tasks | open_tasks.sql |" in result.stdout
    assert "status" not in result.stdout


def test_list_resources_with_status(runner, example_project: Path) -> None:
    result = runner.invoke(
        app, ["list", "--project", str(example_project), "--with-status", "--format", "markdown"]
    )
    assert result.exit_code == 0
    assert "| kind | namespace | name | path | status |" in result.stdout
    assert "| table | main | tasks | tasks.csv | ok |" in result.stdout
    assert "| lens | main | open_tasks | open_tasks.sql | ok |" in result.stdout


def test_explain_table_resource(runner, example_project: Path) -> None:
    result = runner.invoke(app, ["explain", "tasks", "--project", str(example_project)])
    assert result.exit_code == 0
    assert "Primary table: main.tasks" in result.stdout
    assert "identity lens for CSV table" in result.stdout


def test_missing_project_error_is_helpful(runner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["view", "all_tasks", "--project", str(tmp_path)])
    assert result.exit_code == 1
    assert "No LenzDB project found" in result.stderr
    assert "Run from a project root or pass --project." in result.stderr


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
        "t-2,Write GETTING started docs,doing,Docs Refresh\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["diff", "open_tasks", str(edited), "--project", str(example_project)]
    )
    assert result.exit_code == 0
    assert "UPDATED t-1" in result.stdout
    assert "Ship CLI skeleton" in result.stdout
