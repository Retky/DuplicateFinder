import ast
from pathlib import Path

import pytest

CORE_DIR = Path(__file__).resolve().parents[2] / "duplicate_finder" / "core"
CORE_PACKAGE = "duplicate_finder.core"
FORBIDDEN = ("tkinter", "duplicate_finder.ui")


def imported_modules(source: str, package: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join(parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            modules.add(base)
            modules.update(f"{base}.{alias.name}" for alias in node.names)
    return modules


def is_forbidden(module: str) -> bool:
    return any(module == f or module.startswith(f + ".") for f in FORBIDDEN)


@pytest.mark.parametrize(
    "source",
    ["import tkinter", "from tkinter import ttk", "from duplicate_finder import ui",
     "from duplicate_finder.ui.app import run", "from ..ui import app", "from .. import ui"],
)
def test_detector_catches_forbidden_imports(source):
    assert any(is_forbidden(m) for m in imported_modules(source, CORE_PACKAGE))


def test_detector_allows_core_imports():
    source = "from .models import FileEntry\nimport hashlib\nfrom duplicate_finder import __version__"
    assert not any(is_forbidden(m) for m in imported_modules(source, CORE_PACKAGE))


@pytest.mark.parametrize("module_path", sorted(CORE_DIR.rglob("*.py")), ids=lambda p: p.name)
def test_core_does_not_import_ui(module_path):
    package = CORE_PACKAGE
    relative_parent = module_path.parent.relative_to(CORE_DIR)
    if relative_parent.parts:
        package = ".".join([CORE_PACKAGE, *relative_parent.parts])
    offending = {m for m in imported_modules(module_path.read_text(encoding="utf-8"), package)
                 if is_forbidden(m)}
    assert not offending, f"{module_path.name} imports {sorted(offending)}"
