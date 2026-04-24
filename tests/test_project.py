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


def test_invalid_reference_is_rejected(example_project: Path) -> None:
    data_path = example_project / "data" / "tasks.csv"
    data_path.write_text(
        "id,title,status,project_id\n"
        "t-1,Ship CLI skeleton,todo,p-1\n"
        "t-2,Write getting started docs,doing,missing\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    with pytest.raises(ProjectError, match="Invalid reference"):
        project.validate()
