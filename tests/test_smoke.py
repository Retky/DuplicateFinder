import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import duplicate_finder
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


def test_run_prints_version(capsys):
    assert run() == 0
    assert duplicate_finder.__version__ in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [["main.py"], ["-m", "duplicate_finder"]],
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
