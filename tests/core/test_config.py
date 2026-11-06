import json
from pathlib import Path

import pytest

from duplicate_finder.core import config
from duplicate_finder.core.config import Config, default_config_dir, load_config, save_config
from duplicate_finder.core.filename_parser import ALL_PATTERNS


def write_raw(directory: Path, text: str) -> None:
    (directory / "config.json").write_text(text, encoding="utf-8")


def test_missing_file_loads_defaults(tmp_path):
    loaded = load_config(tmp_path)
    assert loaded.enabled_patterns == ALL_PATTERNS
    assert loaded.last_folder is None


def test_save_and_load_round_trip(tmp_path):
    folder = Path(r"C:\Fotos\Canción")
    saved = Config(enabled_patterns=frozenset({"paren_num", "dash_copia"}), last_folder=folder)

    path = save_config(saved, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))

    assert load_config(tmp_path) == saved
    assert raw["version"] == 1
    assert raw["enabled_patterns"] == ["dash_copia", "paren_num"]
    assert raw["last_folder"] == str(folder)
    assert "Canción" in path.read_text(encoding="utf-8")
    assert list(tmp_path.glob(".config.json.*.tmp")) == []


def test_missing_folder_is_still_remembered(tmp_path):
    write_raw(tmp_path, json.dumps({"enabled_patterns": ["copy"], "last_folder": r"C:\gone\folder"}))
    loaded = load_config(tmp_path)
    assert loaded.last_folder == Path(r"C:\gone\folder")
    assert loaded.enabled_patterns == frozenset({"copy"})


@pytest.mark.parametrize("text", ["{", "[]", '"hello"', "3", ""])
def test_damaged_file_loads_defaults(tmp_path, text):
    write_raw(tmp_path, text)
    assert load_config(tmp_path) == Config(ALL_PATTERNS, None)


def test_unknown_pattern_ids_are_dropped(tmp_path):
    write_raw(tmp_path, json.dumps({
        "enabled_patterns": ["paren_num", "nope", "paren_num", 5],
        "last_folder": r"C:\Photos",
    }))
    loaded = load_config(tmp_path)
    assert loaded.enabled_patterns == frozenset({"paren_num"})
    assert loaded.last_folder == Path(r"C:\Photos")


def test_only_unknown_ids_load_as_empty(tmp_path):
    write_raw(tmp_path, json.dumps({"enabled_patterns": ["nope", "also-nope"]}))
    assert load_config(tmp_path).enabled_patterns == frozenset()


def test_wrong_type_patterns_fall_back_but_folder_is_kept(tmp_path):
    write_raw(tmp_path, json.dumps({"enabled_patterns": "paren_num", "last_folder": r"C:\Photos"}))
    loaded = load_config(tmp_path)
    assert loaded.enabled_patterns == ALL_PATTERNS
    assert loaded.last_folder == Path(r"C:\Photos")


def test_wrong_type_folder_is_dropped_but_patterns_are_kept(tmp_path):
    write_raw(tmp_path, json.dumps({"enabled_patterns": ["copy"], "last_folder": 12}))
    loaded = load_config(tmp_path)
    assert loaded.enabled_patterns == frozenset({"copy"})
    assert loaded.last_folder is None


@pytest.mark.parametrize("folder", ["", "   ", None])
def test_blank_folder_becomes_none(tmp_path, folder):
    write_raw(tmp_path, json.dumps({"enabled_patterns": ["copy"], "last_folder": folder}))
    assert load_config(tmp_path).last_folder is None


def test_non_empty_relative_folder_is_kept(tmp_path):
    write_raw(tmp_path, json.dumps({"last_folder": "Photos"}))
    assert load_config(tmp_path).last_folder == Path("Photos")


def test_save_rejects_unknown_pattern(tmp_path):
    with pytest.raises(ValueError):
        save_config(Config(frozenset({"paren_num", "nope"}), None), tmp_path)
    assert not (tmp_path / "config.json").exists()
    assert list(tmp_path.glob(".config.json.*.tmp")) == []


def test_save_rejects_relative_folder(tmp_path):
    with pytest.raises(ValueError):
        save_config(Config(ALL_PATTERNS, Path("Photos")), tmp_path)
    assert not (tmp_path / "config.json").exists()


def test_replace_is_used_once_inside_the_directory(tmp_path, monkeypatch):
    calls = []
    real_replace = config.os.replace

    def spy(source, destination):
        calls.append((source, destination))
        return real_replace(source, destination)

    monkeypatch.setattr(config.os, "replace", spy)
    save_config(Config(frozenset({"copy"}), Path(r"C:\Photos")), tmp_path)

    assert len(calls) == 1
    source, destination = calls[0]
    assert source.endswith(".tmp")
    assert destination.endswith(str(tmp_path / "config.json"))
    assert Path(source).parent == Path(destination).parent


def test_failed_replace_removes_the_temp_file_and_keeps_the_old_one(tmp_path, monkeypatch):
    existing = tmp_path / "config.json"
    existing.write_text("previous", encoding="utf-8")

    def fail(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(config.os, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        save_config(Config(ALL_PATTERNS, None), tmp_path)

    assert existing.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.glob(".config.json.*.tmp")) == []


def test_default_dir_follows_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert default_config_dir() == tmp_path / "DuplicateFinder"


def test_missing_appdata_raises(monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    with pytest.raises(RuntimeError, match="APPDATA"):
        default_config_dir()


def test_old_config_leaves_folder_matching_off(tmp_path):
    write_raw(tmp_path, json.dumps({"enabled_patterns": ["copy"], "last_folder": r"C:\Photos"}))
    loaded = load_config(tmp_path)
    assert loaded.match_folders is False
    assert loaded.match_subfolders is False
    assert loaded.excluded_extensions == frozenset()


def test_subfolders_stay_off_unless_folder_matching_is_on(tmp_path):
    write_raw(tmp_path, json.dumps({"match_folders": False, "match_subfolders": True}))
    assert load_config(tmp_path).match_subfolders is False

    saved = Config(ALL_PATTERNS, None, match_folders=False, match_subfolders=True, excluded_extensions=frozenset({".db"}))
    save_config(saved, tmp_path)
    loaded = load_config(tmp_path)
    assert loaded.match_subfolders is False
    assert loaded.excluded_extensions == frozenset({".db"})


def test_malformed_extension_is_dropped_on_load_and_rejected_on_save(tmp_path):
    write_raw(tmp_path, json.dumps({"excluded_extensions": [".db", "not an extension", 5]}))
    assert load_config(tmp_path).excluded_extensions == frozenset({".db"})
    with pytest.raises(ValueError, match="not an extension"):
        save_config(Config(ALL_PATTERNS, None, excluded_extensions=frozenset({"bad/ext"})), tmp_path)
