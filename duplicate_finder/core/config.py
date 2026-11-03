"""Loads and saves which suffix patterns are enabled, and the last folder used.

The file lives in ``%APPDATA%\\DuplicateFinder\\config.json``. Loading a missing or
damaged file falls back to the defaults. Saving replaces the file atomically, which
is the only ``os.replace`` in the project.
"""

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from duplicate_finder.core.filename_parser import ALL_PATTERNS
from duplicate_finder.core.winpath import to_os_path

CONFIG_VERSION = 1
APP_DIR_NAME = "DuplicateFinder"
CONFIG_FILE_NAME = "config.json"


@dataclass(frozen=True, slots=True)
class Config:
    enabled_patterns: frozenset[str]
    last_folder: Path | None


def default_config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set")
    return Path(appdata) / APP_DIR_NAME


def _defaults() -> Config:
    return Config(enabled_patterns=ALL_PATTERNS, last_folder=None)


def _config_path(directory: Path | None) -> Path:
    return (directory if directory is not None else default_config_dir()) / CONFIG_FILE_NAME


def _read_patterns(value: object) -> frozenset[str]:
    if not isinstance(value, list):
        return ALL_PATTERNS
    return frozenset(item for item in value if isinstance(item, str) and item in ALL_PATTERNS)


def _read_folder(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value)


def load_config(directory: Path | None = None) -> Config:
    """Load the settings. A missing or damaged file returns the defaults and never raises."""
    path = _config_path(directory)
    try:
        with open(to_os_path(path), encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return _defaults()
    if not isinstance(raw, dict):
        return _defaults()
    return Config(
        enabled_patterns=_read_patterns(raw.get("enabled_patterns")),
        last_folder=_read_folder(raw.get("last_folder")),
    )


def save_config(config: Config, directory: Path | None = None) -> Path:
    """Write the settings atomically. Unknown pattern IDs and relative folders are rejected."""
    unknown = config.enabled_patterns - ALL_PATTERNS
    if unknown:
        raise ValueError(f"unknown pattern ids: {sorted(unknown)}")
    if config.last_folder is not None and not config.last_folder.is_absolute():
        raise ValueError(f"last_folder must be absolute: {config.last_folder}")

    path = _config_path(directory)
    os.makedirs(to_os_path(path.parent), exist_ok=True)
    temporary = path.parent / f".{CONFIG_FILE_NAME}.{uuid.uuid4().hex}.tmp"
    payload = {
        "version": CONFIG_VERSION,
        "enabled_patterns": sorted(config.enabled_patterns),
        "last_folder": None if config.last_folder is None else str(config.last_folder),
    }
    try:
        with open(to_os_path(temporary), "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(to_os_path(temporary), to_os_path(path))
    except Exception:
        try:
            os.remove(to_os_path(temporary))
        except OSError:
            pass
        raise
    return path
