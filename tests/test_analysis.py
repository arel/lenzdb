from __future__ import annotations

from pathlib import Path

import pytest

from lenzdb.analysis import analyze_lens
from lenzdb.errors import LensAnalysisError
from lenzdb.project import Project


def test_analysis_classifies_columns(example_project: Path) -> None:
    project = Project.discover(example_project)
    analysis = analyze_lens(project, "open_tasks")

    kinds = {column.output_name: column.kind for column in analysis.columns}
    assert kinds["id"] == "direct_base"
    assert kinds["title"] == "direct_base"
    assert kinds["status"] == "direct_base"
    assert kinds["project_name"] == "joined_lookup"
    assert analysis.writable is True
    assert analysis.primary_key_output == "id"


def test_analysis_rejects_aggregate_lens(example_project: Path) -> None:
    lens_path = example_project / "lenses" / "task_counts.sql"
    lens_path.write_text(
        "select status, count(*) as task_count from tasks group by status",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    with pytest.raises(LensAnalysisError, match="GROUP BY"):
        analyze_lens(project, "task_counts")


def test_analysis_rejects_unsafe_join(example_project: Path) -> None:
    schema_path = example_project / "schema" / "comments.yaml"
    schema_path.write_text(
        "table: comments\n"
        "primary_key: id\n"
        "columns:\n"
        "  id:\n"
        "    type: string\n"
        "    immutable: true\n"
        "  task_id:\n"
        "    type: ref\n"
        "    table: tasks\n"
        "  body:\n"
        "    type: string\n",
        encoding="utf-8",
    )
    data_path = example_project / "data" / "comments.csv"
    data_path.write_text(
        "id,task_id,body\nc-1,t-1,First\nc-2,t-1,Second\n",
        encoding="utf-8",
    )
    lens_path = example_project / "lenses" / "task_comments.sql"
    lens_path.write_text(
        "select t.id, c.body from tasks t join comments c on c.task_id = t.id",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    analysis = analyze_lens(project, "task_comments")
    assert analysis.writable is False
    assert any("not recognized as a many-to-one lookup" in reason for reason in analysis.reasons)
