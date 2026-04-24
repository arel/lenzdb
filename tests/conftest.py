from __future__ import annotations

from pathlib import Path
from shutil import copytree

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def example_project(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "examples" / "basic"
    destination = tmp_path / "project"
    copytree(source, destination)
    return destination
