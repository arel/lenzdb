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
