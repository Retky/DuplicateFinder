"""Builds a scenario folder from a name-to-content description. Writes stay under that folder."""

import os
from pathlib import Path

from duplicate_finder.core.winpath import to_os_path


def build_folder(folder: Path, files: dict[str, bytes]) -> None:
    """Create ``folder`` and write each file. Names are a single path component."""
    os.makedirs(to_os_path(folder), exist_ok=True)
    for name, data in files.items():
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError(f"file name must be a single path component: {name!r}")
        with open(to_os_path(folder / name), "wb") as handle:
            handle.write(data)
