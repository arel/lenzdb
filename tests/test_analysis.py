from __future__ import annotations

from pathlib import Path

import pytest

from lenzdb.analysis import analyze_lens
from lenzdb.errors import LensAnalysisError
from lenzdb.project import Project


def test_analysis_classifies_flat_lens_columns(example_project: Path) -> None:
    project = Project.discover(example_project)
    analysis = analyze_lens(project, "open_tasks")

    kinds = {column.output_name: column.kind for column in analysis.columns}
    assert kinds["id"] == "direct_base"
    assert kinds["title"] == "direct_base"
    assert kinds["status"] == "direct_base"
    assert kinds["project_name"] == "joined_lookup"
    assert analysis.writable is True
    assert analysis.primary_key_output == "id"
    assert analysis.column_map()["project_name"].writable is False


def test_analysis_infers_defaults_from_where_clause(example_project: Path) -> None:
    (example_project / "doing_tasks.sql").write_text(
        "select id, title from tasks where status = 'doing' order by id\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    analysis = analyze_lens(project, "doing_tasks")

    assert analysis.inferred_defaults == {"status": "doing"}
    assert analysis.inferred_default_sources == {"status": "status = 'doing'"}


def test_analysis_requires_all_composite_primary_key_outputs(example_project: Path) -> None:
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
        "  user_id:\n"
        "    type: string\n"
        "  role:\n"
        "    type: string\n",
        encoding="utf-8",
    )
    (example_project / "membership_roles.sql").write_text(
        "select org_id, user_id, role from memberships\n",
        encoding="utf-8",
    )
    (example_project / "membership_roles_missing_key.sql").write_text(
        "select org_id, role from memberships\n",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    analysis = analyze_lens(project, "membership_roles")
    missing_key_analysis = analyze_lens(project, "membership_roles_missing_key")

    assert analysis.writable is True
    assert analysis.primary_key_outputs == ["org_id", "user_id"]
    assert analysis.column_map()["org_id"].writable is False
    assert analysis.column_map()["user_id"].writable is False
    assert missing_key_analysis.writable is False
    assert any("user_id" in reason for reason in missing_key_analysis.reasons)


def test_analysis_rejects_aggregate_lens(example_project: Path) -> None:
    lens_path = example_project / "task_counts.sql"
    lens_path.write_text(
        "select status, count(*) as task_count from tasks group by status",
        encoding="utf-8",
    )

    project = Project.discover(example_project)
    with pytest.raises(LensAnalysisError, match="GROUP BY"):
        analyze_lens(project, "task_counts")

