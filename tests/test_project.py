from __future__ import annotations

from pathlib import Path

import pytest

from lenzdb.errors import ProjectError
from lenzdb.project import Project


def test_project_discovery_and_validation(example_project: Path) -> None:
    project = Project.discover(example_project)
    assert sorted(project.schemas) == ["projects", "tasks"]
    assert sorted(project.lenses) == ["all_tasks", "open_tasks"]
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
    assert project.lenses["all_tasks"] == hidden_lenses / "all_tasks.sql"
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


def test_legacy_layout_is_not_discovered(tmp_path: Path) -> None:
    legacy_project = tmp_path / "legacy"
    (legacy_project / "data").mkdir(parents=True)
    (legacy_project / "schema").mkdir()
    (legacy_project / "lenses").mkdir()

    with pytest.raises(ProjectError, match=r"Missing schema directory: .*\.lenzdb/schema"):
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
