from __future__ import annotations

from pathlib import Path

from lenzdb.cli import app, complete_project_resource, output_width, selected_pager


def test_version_flag(runner) -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "lnz 0.1.4"


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


def test_view_table_without_any_lenses(runner, tmp_path: Path) -> None:
    project_root = tmp_path / "demo"
    schema_dir = project_root / ".lenzdb" / "schema"
    data_dir = project_root / ".lenzdb" / "data"
    schema_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    (schema_dir / "projects.yaml").write_text(
        "table: projects\n"
        "primary_key: id\n"
        "columns:\n"
        "  id:\n"
        "    type: string\n"
        "  name:\n"
        "    type: string\n",
        encoding="utf-8",
    )
    (data_dir / "projects.csv").write_text("id,name\np-1,Alpha\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["view", "projects", "--project", str(project_root), "--format", "markdown"],
    )

    assert result.exit_code == 0
    assert "| id | name |" in result.stdout
    assert "p-1" in result.stdout


def test_view_untracked_table_without_lenzdb_uses_temporary_schema(
    runner, example_project: Path
) -> None:
    lenz_dir = example_project / ".lenzdb"
    for path in sorted(lenz_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()
    lenz_dir.rmdir()

    result = runner.invoke(
        app, ["view", "tasks", "--project", str(example_project), "--format", "markdown"]
    )

    assert result.exit_code == 0
    assert "| id | title | status | project_id |" in result.stdout
    assert "Ship CLI skeleton" in result.stdout
    assert "Info: using temporary schema for untracked table main.tasks" in result.stderr


def test_describe_table_shape(runner, example_project: Path) -> None:
    result = runner.invoke(
        app,
        ["describe", "tasks", "--project", str(example_project), "--format", "markdown"],
    )

    assert result.exit_code == 0
    assert "| column | type | primary_key | editable |" in result.stdout
    assert "| id | VARCHAR | yes | no |" in result.stdout
    assert "| status | VARCHAR | no | yes |" in result.stdout


def test_describe_untracked_table_without_lenzdb_uses_temporary_schema(
    runner, example_project: Path
) -> None:
    lenz_dir = example_project / ".lenzdb"
    for path in sorted(lenz_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()
    lenz_dir.rmdir()

    result = runner.invoke(
        app, ["describe", "tasks", "--project", str(example_project), "--format", "markdown"]
    )

    assert result.exit_code == 0
    assert "| column | type | primary_key | editable |" in result.stdout
    assert "| id | VARCHAR | yes | no |" in result.stdout
    assert "Info: using temporary schema for untracked table main.tasks" in result.stderr


def test_describe_lens_selected_columns(runner, example_project: Path) -> None:
    result = runner.invoke(
        app,
        [
            "describe",
            "open_tasks",
            "--project",
            str(example_project),
            "--format",
            "markdown",
            "--columns",
            "id,project_name",
        ],
    )

    assert result.exit_code == 0
    assert "| id | VARCHAR | yes | no |" in result.stdout
    assert "| project_name | VARCHAR | no | yes |" in result.stdout
    assert "title" not in result.stdout


def test_describe_lens_with_untracked_dependencies_keeps_notices_on_stderr(
    runner, example_project: Path
) -> None:
    (example_project / ".lenzdb" / "schema" / "tasks.yaml").unlink()
    (example_project / ".lenzdb" / "schema" / "projects.yaml").unlink()

    result = runner.invoke(
        app,
        ["describe", "open_tasks", "--project", str(example_project), "--format", "markdown"],
    )

    assert result.exit_code == 0
    assert "| column | type | primary_key | editable |" in result.stdout
    assert "Info: using temporary schema for untracked table main.tasks" in result.stderr
    assert "Info: using temporary schema for untracked table main.projects" in result.stderr


def test_describe_count_and_sql_shapes(runner, example_project: Path) -> None:
    count_result = runner.invoke(
        app,
        [
            "describe",
            "tasks",
            "--project",
            str(example_project),
            "--format",
            "markdown",
            "--count",
        ],
    )
    sql_result = runner.invoke(
        app,
        [
            "describe",
            "tasks",
            "--project",
            str(example_project),
            "--format",
            "markdown",
            "--sql",
            "select title from resource;",
        ],
    )

    assert count_result.exit_code == 0
    assert "| count | BIGINT | no | no |" in count_result.stdout
    assert sql_result.exit_code == 0
    assert "| title | VARCHAR | no | yes |" in sql_result.stdout
    assert "project_id" not in sql_result.stdout


def test_view_rejects_describe_flag(runner, example_project: Path) -> None:
    result = runner.invoke(app, ["view", "tasks", "--project", str(example_project), "--describe"])

    assert result.exit_code != 0
    assert "No such option: --describe" in result.stderr


def test_view_ignores_unrelated_untracked_csv(runner, example_project: Path) -> None:
    (example_project / "bar.snoo.csv").write_text("id,name\ns-1,Snoo\n", encoding="utf-8")

    result = runner.invoke(
        app, ["view", "tasks", "--project", str(example_project), "--format", "markdown"]
    )

    assert result.exit_code == 0
    assert "p-1" in result.stdout


def test_view_lens_with_untracked_dependencies_uses_temporary_schemas(
    runner, example_project: Path
) -> None:
    (example_project / ".lenzdb" / "schema" / "tasks.yaml").unlink()
    (example_project / ".lenzdb" / "schema" / "projects.yaml").unlink()

    result = runner.invoke(
        app, ["view", "open_tasks", "--project", str(example_project), "--format", "markdown"]
    )

    assert result.exit_code == 0
    assert "| id | title | status | project_name |" in result.stdout
    assert "Ship CLI skeleton" in result.stdout
    assert "Info: using temporary schema for untracked table main.tasks" in result.stderr
    assert "Info: using temporary schema for untracked table main.projects" in result.stderr


def test_view_table_output_is_not_duplicated(runner, example_project: Path) -> None:
    result = runner.invoke(app, ["view", "open_tasks", "--project", str(example_project)])
    assert result.exit_code == 0
    assert result.stdout.count("Ship CLI skeleton") == 1


def test_view_columns_filter_and_order(runner, example_project: Path) -> None:
    result = runner.invoke(
        app,
        [
            "view",
            "tasks",
            "--project",
            str(example_project),
            "--format",
            "markdown",
            "--columns",
            "title,status",
            "--filter",
            "status in ('todo', 'doing')",
            "--order",
            "-status,title",
        ],
    )

    assert result.exit_code == 0
    assert "| title | status |" in result.stdout
    assert "project_id" not in result.stdout
    assert result.stdout.index("Ship CLI skeleton") < result.stdout.index("Write getting started docs")


def test_view_tsv_and_yaml_formats(runner, example_project: Path) -> None:
    tsv_result = runner.invoke(
        app,
        [
            "view",
            "tasks",
            "--project",
            str(example_project),
            "--format",
            "tsv",
            "--columns",
            "id,title",
            "--limit",
            "1",
        ],
    )
    yaml_result = runner.invoke(
        app,
        [
            "view",
            "tasks",
            "--project",
            str(example_project),
            "--format",
            "yaml",
            "--columns",
            "id,title",
            "--limit",
            "1",
        ],
    )

    assert tsv_result.exit_code == 0
    assert "id\ttitle\n" in tsv_result.stdout
    assert "t-1\tShip CLI skeleton\n" in tsv_result.stdout
    assert yaml_result.exit_code == 0
    assert "- id: t-1\n  title: Ship CLI skeleton\n" in yaml_result.stdout


def test_view_list_format(runner, example_project: Path) -> None:
    result = runner.invoke(
        app,
        [
            "view",
            "tasks",
            "--project",
            str(example_project),
            "--format",
            "list",
            "--columns",
            "id,title",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert "- [t-1 | Ship CLI skeleton]" in result.stdout


def test_view_help_lists_output_formats(runner) -> None:
    result = runner.invoke(app, ["view", "--help"])

    assert result.exit_code == 0
    assert "Output format:" in result.stdout
    for output_format in ["table", "list", "markdown", "csv", "tsv", "json", "ndjson", "yaml", "html"]:
        assert output_format in result.stdout


def test_list_help_lists_output_formats(runner) -> None:
    result = runner.invoke(app, ["list", "--help"])

    assert result.exit_code == 0
    assert "Output format:" in result.stdout
    for output_format in ["table", "list", "markdown", "csv", "tsv", "json", "ndjson", "yaml", "html"]:
        assert output_format in result.stdout


def test_output_width_env_precedence(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LENZDB_COLUMNS", "42")

    assert output_width() == 42

    monkeypatch.delenv("LENZDB_COLUMNS")

    assert output_width() == 80


def test_selected_pager_env_precedence(monkeypatch) -> None:
    monkeypatch.setenv("PAGER", "plain-pager")
    monkeypatch.setenv("LENZDB_PAGER", "lenz-pager")

    assert selected_pager() == "lenz-pager"

    monkeypatch.delenv("LENZDB_PAGER")

    assert selected_pager() == "plain-pager"


def test_selected_pager_is_unset_without_env(monkeypatch) -> None:
    monkeypatch.delenv("LENZDB_PAGER", raising=False)
    monkeypatch.delenv("PAGER", raising=False)

    assert selected_pager() is None


def test_view_paginates_with_project_page_size(runner, example_project: Path) -> None:
    (example_project / ".lenzdb" / "project.yaml").write_text(
        "view:\n"
        "  page_size: 1\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "view",
            "tasks",
            "--project",
            str(example_project),
            "--format",
            "markdown",
            "--order",
            "id",
            "--page",
            "2",
        ],
    )

    assert result.exit_code == 0
    assert "Write getting started docs" in result.stdout
    assert "Ship CLI skeleton" not in result.stdout


def test_view_page_size_env_overrides_project_page_size(runner, example_project: Path) -> None:
    result = runner.invoke(
        app,
        [
            "view",
            "tasks",
            "--project",
            str(example_project),
            "--format",
            "markdown",
            "--order",
            "id",
            "--page",
            "2",
        ],
        env={"LENZDB_PAGE_SIZE": "1"},
    )

    assert result.exit_code == 0
    assert "Write getting started docs" in result.stdout
    assert "Ship CLI skeleton" not in result.stdout


def test_view_page_size_env_minus_one_requires_explicit_size(
    runner, example_project: Path
) -> None:
    missing_size = runner.invoke(
        app,
        [
            "view",
            "tasks",
            "--project",
            str(example_project),
            "--format",
            "markdown",
            "--order",
            "id",
            "--page",
            "2",
        ],
        env={"LENZDB_PAGE_SIZE": "-1"},
    )
    explicit_size = runner.invoke(
        app,
        [
            "view",
            "tasks",
            "--project",
            str(example_project),
            "--format",
            "markdown",
            "--order",
            "id",
            "--page",
            "2",
            "--page-size",
            "1",
        ],
        env={"LENZDB_PAGE_SIZE": "-1"},
    )

    assert missing_size.exit_code == 1
    assert "--page requires --page-size when $LENZDB_PAGE_SIZE=-1" in missing_size.stderr
    assert explicit_size.exit_code == 0
    assert "Write getting started docs" in explicit_size.stdout


def test_view_count_allows_filter(runner, example_project: Path) -> None:
    result = runner.invoke(
        app,
        [
            "view",
            "tasks",
            "--project",
            str(example_project),
            "--format",
            "markdown",
            "--filter",
            "status = 'todo'",
            "--count",
        ],
    )

    assert result.exit_code == 0
    assert "| count |" in result.stdout
    assert "| 1 |" in result.stdout


def test_view_distinct_single_column(runner, example_project: Path) -> None:
    result = runner.invoke(
        app,
        [
            "view",
            "tasks",
            "--project",
            str(example_project),
            "--format",
            "markdown",
            "--distinct",
            "project_id",
            "--order",
            "project_id",
        ],
    )

    assert result.exit_code == 0
    assert "| project_id |" in result.stdout
    assert "| p-1 |" in result.stdout
    assert "| p-2 |" in result.stdout
    assert result.stdout.count("| p-1 |") == 1


def test_view_distinct_multiple_columns(runner, example_project: Path) -> None:
    result = runner.invoke(
        app,
        [
            "view",
            "tasks",
            "--project",
            str(example_project),
            "--format",
            "markdown",
            "--distinct",
            "status,project_id",
            "--order",
            "status,project_id",
        ],
    )

    assert result.exit_code == 0
    assert "| status | project_id |" in result.stdout
    assert "| doing | p-2 |" in result.stdout
    assert "| done | p-1 |" in result.stdout
    assert "| todo | p-1 |" in result.stdout


def test_view_distinct_rejects_columns(runner, example_project: Path) -> None:
    result = runner.invoke(
        app,
        [
            "view",
            "tasks",
            "--project",
            str(example_project),
            "--distinct",
            "status",
            "--columns",
            "id,status",
        ],
    )

    assert result.exit_code == 1
    assert "--distinct cannot be combined with --columns" in result.stderr


def test_view_sql_uses_resource_alias(runner, example_project: Path) -> None:
    result = runner.invoke(
        app,
        [
            "view",
            "open_tasks",
            "--project",
            str(example_project),
            "--format",
            "markdown",
            "--sql",
            "select title from resource where status = 'doing'",
        ],
    )

    assert result.exit_code == 0
    assert "| title |" in result.stdout
    assert "Write getting started docs" in result.stdout
    assert "Ship CLI skeleton" not in result.stdout


def test_view_rejects_incompatible_sql_and_convenience_flags(
    runner, example_project: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "view",
            "tasks",
            "--project",
            str(example_project),
            "--sql",
            "select * from resource",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 1
    assert "--sql cannot be combined with view convenience options" in result.stderr


def test_view_rejects_empty_sql(runner, example_project: Path) -> None:
    result = runner.invoke(
        app,
        ["view", "tasks", "--project", str(example_project), "--sql", ""],
    )

    assert result.exit_code == 1
    assert "--sql must not be empty" in result.stderr


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
    assert "| kind | namespace | name | path | state |" in result.stdout
    assert "| table | main | tasks | tasks.csv | added |" in result.stdout
    assert "| lens | main | open_tasks | open_tasks.sql | added |" in result.stdout
    assert "check" not in result.stdout


def test_list_resources_with_dotted_lens_filename(runner, example_project: Path) -> None:
    (example_project / "bar.foo.sql").write_text("select id, title from tasks\n", encoding="utf-8")
    (example_project / "other.thing.bar.foo.sql").write_text(
        "select id, title from tasks\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["list", "--project", str(example_project), "--format", "markdown"])

    assert result.exit_code == 0
    assert "| lens | bar | foo | bar.foo.sql | added |" in result.stdout
    assert "| lens | other.thing.bar | foo | other.thing.bar.foo.sql | added |" in result.stdout


def test_list_resources_with_check(runner, example_project: Path) -> None:
    result = runner.invoke(
        app, ["list", "--project", str(example_project), "--check", "--format", "markdown"]
    )
    assert result.exit_code == 0
    assert "| kind | namespace | name | path | state | check |" in result.stdout
    assert "| table | main | tasks | tasks.csv | added | ok |" in result.stdout
    assert "| lens | main | open_tasks | open_tasks.sql | added | ok |" in result.stdout


def test_list_resources_with_missing_and_untracked_csvs(runner, example_project: Path) -> None:
    (example_project / "tasks.csv").unlink()
    (example_project / "bar.snoo.csv").write_text("id,name\ns-1,Snoo\n", encoding="utf-8")

    result = runner.invoke(app, ["list", "--project", str(example_project), "--format", "markdown"])

    assert result.exit_code == 0
    assert "| table | main | tasks |  | missing |" in result.stdout
    assert "| table | bar | snoo | bar.snoo.csv | untracked |" in result.stdout


def test_list_resources_with_missing_registered_csv(runner, example_project: Path) -> None:
    (example_project / ".lenzdb" / "schema" / "main.pear.yaml").write_text(
        "table: pear\n"
        "primary_key: id\n"
        "columns:\n"
        "  id:\n"
        "    type: string\n"
        "    immutable: true\n"
        "  name:\n"
        "    type: string\n",
        encoding="utf-8",
    )
    (example_project / ".lenzdb" / "project.yaml").write_text(
        "tables:\n"
        "  - path: somedir/pear.csv\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["list", "--project", str(example_project), "--format", "markdown"])

    assert result.exit_code == 0
    assert "| table | main | pear | somedir/pear.csv | missing |" in result.stdout


def test_list_resources_without_lenzdb_shows_untracked_csvs(runner, example_project: Path) -> None:
    lenz_dir = example_project / ".lenzdb"
    for path in sorted(lenz_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()
    lenz_dir.rmdir()

    result = runner.invoke(app, ["list", "--project", str(example_project), "--format", "markdown"])

    assert result.exit_code == 0
    assert "| table | main | projects | projects.csv | untracked |" in result.stdout
    assert "| table | main | tasks | tasks.csv | untracked |" in result.stdout


def test_list_resources_in_current_subdir_shows_untracked_csv(
    runner, example_project: Path, monkeypatch
) -> None:
    somedir = example_project / "somedir"
    somedir.mkdir()
    (somedir / "pear.csv").write_text("id,name\np-1,Pear\n", encoding="utf-8")
    monkeypatch.chdir(somedir)

    result = runner.invoke(app, ["list", "--format", "markdown"])

    assert result.exit_code == 0
    assert "| table | main | pear | somedir/pear.csv | untracked |" in result.stdout


def test_list_resources_marks_header_mismatch_as_state_error(
    runner, example_project: Path
) -> None:
    (example_project / "tasks.csv").write_text(
        "id,title,status\n"
        "t-1,Ship CLI skeleton,todo\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["list", "--project", str(example_project), "--check", "--format", "markdown"]
    )

    assert result.exit_code == 0
    assert "| table | main | tasks | tasks.csv | error | header_mismatch:" in result.stdout


def test_add_untracked_root_csv_by_table_name(runner, example_project: Path) -> None:
    (example_project / "new_table.csv").write_text(
        "key,name\n"
        "n-1,New row\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["add", "new_table", "--primary-key", "key", "--project", str(example_project)]
    )

    assert result.exit_code == 0
    assert "Added table main.new_table" in result.stdout
    schema = example_project / ".lenzdb" / "schema" / "main.new_table.yaml"
    assert schema.exists()
    assert "primary_key: key" in schema.read_text(encoding="utf-8")

    view_result = runner.invoke(
        app, ["view", "new_table", "--project", str(example_project), "--format", "markdown"]
    )
    assert view_result.exit_code == 0
    assert "| n-1 | New row |" in view_result.stdout


def test_add_csv_in_subfolder_registers_project_path(runner, example_project: Path) -> None:
    imports = example_project / "imports"
    imports.mkdir()
    (imports / "new_notes.csv").write_text(
        "code,text\n"
        "note-1,Useful\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "add",
            "imports/new_notes.csv",
            "--primary-key",
            "code",
            "--project",
            str(example_project),
        ],
    )

    assert result.exit_code == 0
    assert "Updated: .lenzdb/project.yaml" in result.stdout
    project_config = (example_project / ".lenzdb" / "project.yaml").read_text(encoding="utf-8")
    assert "path: imports/new_notes.csv" in project_config

    view_result = runner.invoke(
        app, ["view", "new_notes", "--project", str(example_project), "--format", "markdown"]
    )
    assert view_result.exit_code == 0
    assert "| note-1 | Useful |" in view_result.stdout


def test_add_csv_path_is_resolved_from_current_dir(
    runner, example_project: Path, monkeypatch
) -> None:
    somedir = example_project / "somedir"
    somedir.mkdir()
    (somedir / "pear.csv").write_text(
        "id,name\n"
        "p-1,Pear\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(somedir)

    result = runner.invoke(app, ["add", "./pear.csv", "--primary-key", "id"])

    assert result.exit_code == 0
    assert "Added table main.pear" in result.stdout
    project_config = (example_project / ".lenzdb" / "project.yaml").read_text(encoding="utf-8")
    assert "path: somedir/pear.csv" in project_config
    assert (example_project / ".lenzdb" / "schema" / "main.pear.yaml").exists()


def test_add_csv_with_composite_primary_key(runner, example_project: Path) -> None:
    (example_project / "memberships.csv").write_text(
        "org_id,user_id,role\n"
        "o-1,u-1,admin\n"
        "o-1,u-2,member\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "add",
            "memberships",
            "--primary-key",
            "org_id,user_id",
            "--project",
            str(example_project),
        ],
    )

    assert result.exit_code == 0
    schema = (example_project / ".lenzdb" / "schema" / "main.memberships.yaml").read_text(
        encoding="utf-8"
    )
    assert "primary_key:\n- org_id\n- user_id\n" in schema
    assert "org_id:\n    type: string\n    immutable: true\n" in schema
    assert "user_id:\n    type: string\n    immutable: true\n" in schema


def test_add_csv_rejects_duplicate_composite_primary_key(
    runner, example_project: Path
) -> None:
    (example_project / "memberships.csv").write_text(
        "org_id,user_id,role\n"
        "o-1,u-1,admin\n"
        "o-1,u-1,member\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "add",
            "memberships",
            "--primary-key",
            "org_id,user_id",
            "--project",
            str(example_project),
        ],
    )

    assert result.exit_code == 1
    assert "duplicate value 'o-1 | u-1'" in result.stderr


def test_explain_table_resource(runner, example_project: Path) -> None:
    result = runner.invoke(app, ["explain", "tasks", "--project", str(example_project)])
    assert result.exit_code == 0
    assert "Primary table: main.tasks" in result.stdout
    assert "identity lens for CSV table" in result.stdout


def test_missing_project_error_is_helpful(runner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["view", "all_tasks", "--project", str(tmp_path)])
    assert result.exit_code == 1
    assert "Unknown resource 'all_tasks'" in result.stderr


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


def test_explain_shows_inferred_defaults(runner, example_project: Path) -> None:
    (example_project / "doing_tasks.sql").write_text(
        "select id, title from tasks where status = 'doing' order by id\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["explain", "doing_tasks", "--project", str(example_project)])
    assert result.exit_code == 0
    assert "Inferred defaults:" in result.stdout
    assert "status = 'doing'" in result.stdout


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
