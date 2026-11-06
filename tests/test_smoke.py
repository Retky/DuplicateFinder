import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import duplicate_finder
import duplicate_finder.ui.app as ui_app
from duplicate_finder.app import run

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_pyproject() -> dict:
    with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)


def test_version_is_defined():
    assert isinstance(duplicate_finder.__version__, str)
    assert duplicate_finder.__version__


def test_version_matches_pyproject():
    assert load_pyproject()["project"]["version"] == duplicate_finder.__version__


def test_no_runtime_dependencies():
    assert load_pyproject()["project"]["dependencies"] == []


def test_run_without_arguments_opens_the_window(monkeypatch):
    monkeypatch.setattr(ui_app, "run", lambda: 0)
    assert run() == 0


@pytest.mark.parametrize(
    "args",
    [["main.py", "--version"], ["-m", "duplicate_finder", "--version"]],
    ids=["main.py", "python -m"],
)
def test_entry_points_exit_cleanly(args):
    result = subprocess.run(
        [sys.executable, *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert duplicate_finder.__version__ in result.stdout
