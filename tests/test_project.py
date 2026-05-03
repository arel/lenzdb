from __future__ import annotations

from pathlib import Path

import pytest

from lenzdb.errors import ProjectError
from lenzdb.project import Project


def test_project_discovers_flat_tracked_and_untracked_resources(example_project: Path) -> None:
    project = Project.discover(example_project)

    assert sorted(project.schemas) == ["projects", "tasks"]
    assert sorted(project.lenses) == ["open_tasks"]
    assert sorted(project.untracked_lens_paths) == ["all_tasks"]
    assert project.table_path("tasks") == example_project / "tasks.csv"
    assert project.lens_sql("open_tasks").startswith("select\n")
    project.validate()


def test_project_treats_dotted_filenames_literally(example_project: Path) -> None:
    (example_project / "projects..csv").write_text("id,name\np-1,Extra\n", encoding="utf-8")
    (example_project / "team.alpha.sql").write_text("select id from tasks\n", encoding="utf-8")

    project = Project.discover(example_project)

    assert "projects." in project.untracked_table_paths
    assert project.untracked_table_paths["projects."][0] == example_project / "projects..csv"
    assert project.untracked_lens_paths["team.alpha"][0] == example_project / "team.alpha.sql"


def test_tracked_resources_win_over_untracked_duplicates(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    schema_dir = project_root / ".lenzdb" / "schema"
    schema_dir.mkdir(parents=True)
    (project_root / "tasks.csv").write_text("id,title\nt-1,Tracked\n", encoding="utf-8")
    (project_root / "dup.csv").write_text("id,title\nt-2,Shadow\n", encoding="utf-8")
    (schema_dir / "tasks.yaml").write_text(
        "kind: table\n"
        "name: tasks\n"
        "path: tasks.csv\n"
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

    project = Project.discover(project_root)

    assert project.resolve_table_name("tasks") == "tasks"
    assert project.table_path("tasks") == project_root / "tasks.csv"
    assert project.untracked_table_paths["dup"][0] == project_root / "dup.csv"


def test_composite_primary_key_validates_unique_tuples(example_project: Path) -> None:
    (example_project / "memberships.csv").write_text(
        "org_id,user_id,role\n"
        "o-1,u-1,admin\n"
        "o-1,u-2,member\n",
        encoding="utf-8",
    )
    (example_project / ".lenzdb" / "schema" / "memberships.yaml").write_text(
        "kind: table\n"
        "name: memberships\n"
        "path: memberships.csv\n"
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

    project = Project.discover(example_project)
    assert project.primary_key_columns("memberships") == ["org_id", "user_id"]
    project.validate()


def test_duplicate_tracked_names_are_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    schema_dir = project_root / ".lenzdb" / "schema"
    schema_dir.mkdir(parents=True)
    (project_root / "tasks.csv").write_text("id,title\nt-1,Tracked\n", encoding="utf-8")
    (schema_dir / "tasks.yaml").write_text(
        "kind: table\n"
        "name: tasks\n"
        "path: tasks.csv\n"
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
    (schema_dir / "tasks_lens.yaml").write_text(
        "kind: lens\n"
        "name: tasks\n"
        "path: tasks.sql\n",
        encoding="utf-8",
    )

    with pytest.raises(ProjectError, match="Table and lens names must be distinct"):
        Project.discover(project_root)

