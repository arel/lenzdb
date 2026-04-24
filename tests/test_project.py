from __future__ import annotations

from pathlib import Path

import pytest

from lenzdb.errors import ProjectError
from lenzdb.project import Project


def test_project_discovery_and_validation(example_project: Path) -> None:
    project = Project.discover(example_project)
    assert sorted(project.schemas) == ["main.projects", "main.tasks"]
    assert sorted(project.lenses) == ["main.all_tasks", "main.open_tasks"]
    project.validate()


def test_hidden_data_and_lenses_are_discovered(example_project: Path) -> None:
    hidden_data = example_project / ".lenzdb" / "data"
    hidden_lenses = example_project / ".lenzdb" / "lenses"
    hidden_data.mkdir()
    hidden_lenses.mkdir()
    (example_project / "projects.csv").rename(hidden_data / "projects.csv")
    (example_project / "all_tasks.sql").rename(hidden_lenses / "all_tasks.sql")

    project = Project.discover(example_project)

    assert project.table_path("projects") == hidden_data / "projects.csv"
    assert project.lenses["main.all_tasks"] == hidden_lenses / "all_tasks.sql"
    project.validate()


def test_main_namespace_resolves_tables_and_lenses(example_project: Path) -> None:
    project = Project.discover(example_project)

    assert project.schema_for("main.tasks") == project.schema_for("tasks")
    assert project.table_path("main.projects") == example_project / "projects.csv"
    assert project.lens_sql("main.open_tasks") == project.lens_sql("open_tasks")


def test_unknown_namespace_is_rejected(example_project: Path) -> None:
    project = Project.discover(example_project)

    with pytest.raises(ProjectError, match="Unknown table namespace 'archive'"):
        project.schema_for("archive.tasks")

    with pytest.raises(ProjectError, match="Unknown lens namespace 'archive'"):
        project.lens_sql("archive.open_tasks")


def test_subfolder_files_are_ignored_without_registration(example_project: Path) -> None:
    imports = example_project / "imports"
    imports.mkdir()
    (imports / "notes.csv").write_text("id,text\nn-1,Hidden\n", encoding="utf-8")

    project = Project.discover(example_project)

    with pytest.raises(ProjectError, match="Unknown table 'notes'"):
        project.table_path("notes")


def test_registered_single_files_default_to_main_namespace(example_project: Path) -> None:
    imports = example_project / "imports"
    reports = example_project / "reports"
    imports.mkdir()
    reports.mkdir()
    (imports / "notes.csv").write_text("id,text\nn-1,Useful\n", encoding="utf-8")
    (reports / "notes_view.sql").write_text("select id, text from notes\n", encoding="utf-8")
    (example_project / ".lenzdb" / "schema" / "notes.yaml").write_text(
        "table: notes\n"
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
        "  - path: imports/notes.csv\n"
        "lenses:\n"
        "  - path: reports/notes_view.sql\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)

    assert project.table_path("notes") == imports / "notes.csv"
    assert project.lens_sql("notes_view") == "select id, text from notes\n"


def test_registered_folders_and_globs_require_namespace(example_project: Path) -> None:
    imports = example_project / "imports"
    imports.mkdir()
    (imports / "notes.csv").write_text("id,text\nn-1,Hidden\n", encoding="utf-8")
    (example_project / ".lenzdb" / "project.yaml").write_text(
        "tables:\n"
        "  - path: imports/*.csv\n",
        encoding="utf-8",
    )

    with pytest.raises(ProjectError, match="folders and globs must specify a namespace"):
        Project.discover(example_project)


def test_registered_namespace_makes_unqualified_duplicates_ambiguous(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    (project_root / ".lenzdb" / "schema").mkdir(parents=True)
    imports = project_root / "imports"
    imports.mkdir()
    (project_root / "tasks.csv").write_text("id,title\nt-1,Main task\n", encoding="utf-8")
    (imports / "tasks.csv").write_text("id,title\nt-2,Client task\n", encoding="utf-8")
    (project_root / "tasks_view.sql").write_text("select id, title from main.tasks\n", encoding="utf-8")
    (project_root / ".lenzdb" / "schema" / "tasks.yaml").write_text(
        "table: tasks\n"
        "primary_key: id\n"
        "columns:\n"
        "  id:\n"
        "    type: string\n"
        "    immutable: true\n"
        "  title:\n"
        "    type: string\n",
        encoding="utf-8",
    )
    (project_root / ".lenzdb" / "schema" / "client_tasks.yaml").write_text(
        "table: client.tasks\n"
        "primary_key: id\n"
        "columns:\n"
        "  id:\n"
        "    type: string\n"
        "    immutable: true\n"
        "  title:\n"
        "    type: string\n",
        encoding="utf-8",
    )
    (project_root / ".lenzdb" / "project.yaml").write_text(
        "tables:\n"
        "  - path: imports/*.csv\n"
        "    namespace: client\n",
        encoding="utf-8",
    )

    project = Project.discover(project_root)

    assert project.table_path("client.tasks") == imports / "tasks.csv"
    with pytest.raises(ProjectError, match="Ambiguous table 'tasks'"):
        project.table_path("tasks")


def test_table_and_lens_names_must_be_distinct(example_project: Path) -> None:
    (example_project / "open_tasks.csv").write_text(
        "id,title\nx-1,Collision\n",
        encoding="utf-8",
    )
    (example_project / ".lenzdb" / "schema" / "open_tasks.yaml").write_text(
        "table: open_tasks\n"
        "primary_key: id\n"
        "columns:\n"
        "  id:\n"
        "    type: string\n"
        "    immutable: true\n"
        "  title:\n"
        "    type: string\n",
        encoding="utf-8",
    )

    with pytest.raises(ProjectError, match="Table and lens names must be distinct"):
        Project.discover(example_project)


def test_legacy_layout_is_not_discovered(tmp_path: Path) -> None:
    legacy_project = tmp_path / "legacy"
    (legacy_project / "data").mkdir(parents=True)
    (legacy_project / "schema").mkdir()
    (legacy_project / "lenses").mkdir()

    with pytest.raises(ProjectError, match="No LenzDB project found"):
        Project.discover(legacy_project)


def test_invalid_reference_is_rejected(example_project: Path) -> None:
    data_path = example_project / "tasks.csv"
    data_path.write_text(
        "id,title,status,project_id\n"
        "t-1,Ship CLI skeleton,todo,p-1\n"
        "t-2,Write getting started docs,doing,missing\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    with pytest.raises(ProjectError, match="Invalid reference"):
        project.validate()
