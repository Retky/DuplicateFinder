"""Loads and saves which suffix patterns are enabled, and the last folder used.

The file lives in ``%APPDATA%\\DuplicateFinder\\config.json``. Loading a missing or
damaged file falls back to the defaults. Saving replaces the file atomically, which
is the only ``os.replace`` in the project.
"""

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from duplicate_finder.core.filename_parser import ALL_PATTERNS
from duplicate_finder.core.winpath import to_os_path

CONFIG_VERSION = 1
APP_DIR_NAME = "DuplicateFinder"
CONFIG_FILE_NAME = "config.json"


_EXTENSION_RE = re.compile(r"\.[a-z0-9]+\Z")


@dataclass(frozen=True, slots=True)
class Config:
    enabled_patterns: frozenset[str]
    last_folder: Path | None
    match_names: bool = True
    match_content: bool = True
    match_folders: bool = False
    match_subfolders: bool = False
    excluded_extensions: frozenset[str] = frozenset()
    match_nested_files: bool = False


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


def _read_flag(value: object, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _one_extension(text: str) -> str:
    ext = text.strip().casefold()
    if not ext.startswith("."):
        ext = f".{ext}"
    if _EXTENSION_RE.fullmatch(ext) is None:
        raise ValueError(f"not an extension: {text}")
    return ext


def parse_excluded_extensions(text: str) -> frozenset[str]:
    """Parse a comma- or whitespace-separated extension list. A missing dot is added."""
    return frozenset(_one_extension(part) for part in re.split(r"[\s,]+", text.strip()) if part)


def _read_extensions(value: object) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    found: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        try:
            found.append(_one_extension(item))
        except ValueError:
            continue
    return frozenset(found)


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
    match_folders = _read_flag(raw.get("match_folders"), default=False)
    return Config(
        enabled_patterns=_read_patterns(raw.get("enabled_patterns")),
        last_folder=_read_folder(raw.get("last_folder")),
        match_names=_read_flag(raw.get("match_names")),
        match_content=_read_flag(raw.get("match_content")),
        match_folders=match_folders,
        match_subfolders=_read_flag(raw.get("match_subfolders"), default=False) and match_folders,
        excluded_extensions=_read_extensions(raw.get("excluded_extensions")),
        match_nested_files=_read_flag(raw.get("match_nested_files"), default=False),
    )


def save_config(config: Config, directory: Path | None = None) -> Path:
    """Write the settings atomically. Unknown pattern IDs and relative folders are rejected."""
    unknown = config.enabled_patterns - ALL_PATTERNS
    if unknown:
        raise ValueError(f"unknown pattern ids: {sorted(unknown)}")
    if config.last_folder is not None and not config.last_folder.is_absolute():
        raise ValueError(f"last_folder must be absolute: {config.last_folder}")
    excluded = parse_excluded_extensions(" ".join(sorted(config.excluded_extensions)))
    if excluded != config.excluded_extensions:
        raise ValueError(f"not an extension: {sorted(config.excluded_extensions - excluded)}")
    match_subfolders = config.match_subfolders and config.match_folders

    path = _config_path(directory)
    os.makedirs(to_os_path(path.parent), exist_ok=True)
    temporary = path.parent / f".{CONFIG_FILE_NAME}.{uuid.uuid4().hex}.tmp"
    payload = {
        "version": CONFIG_VERSION,
        "enabled_patterns": sorted(config.enabled_patterns),
        "last_folder": None if config.last_folder is None else str(config.last_folder),
        "match_names": config.match_names,
        "match_content": config.match_content,
        "match_folders": config.match_folders,
        "match_subfolders": match_subfolders,
        "excluded_extensions": sorted(excluded),
        "match_nested_files": config.match_nested_files,
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
