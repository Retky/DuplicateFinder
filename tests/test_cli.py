import os
import subprocess
import sys
from pathlib import Path

import pytest

from duplicate_finder import __version__
from duplicate_finder.cli import main

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def folder(tmp_path) -> Path:
    files = {
        "Photo.jpg": b"photo-content",
        "Photo (1).jpg": b"photo-content",
        "Photo (3).jpg": b"photo-content-bigger",
        "Img.bmp": b"BM" + b"\x00" * 30,
        "Img (1).bmp": b"BM" + b"\xff" * 30,
        "Lonely (1).png": b"no original",
        "empty.txt": b"",
    }
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "Photo (2).jpg").write_bytes(b"photo-content")
    return tmp_path


def snapshot(folder: Path) -> dict[str, int]:
    return {str(p.relative_to(folder)): p.stat().st_mtime_ns for p in folder.rglob("*")}


def test_dry_run_report(folder, capsys):
    assert main(["--dry-run", str(folder)]) == 0
    out = capsys.readouterr().out

    assert f"Folder: {folder}" in out
    assert "Analyzed: 6   Skipped: 2   Errors: 0" in out
    assert "] Photo.jpg (13 B)" in out
    assert "] Lonely.png (no original)" in out
    for expected in ("[move] Photo (1).jpg", "[keep] Photo (3).jpg", "[keep] Img (1).bmp",
                     "[keep] Lonely (1).png", "CONFIRMED", "DIFFERENT_SIZE", "DIFFERENT_CONTENT", "NO_ORIGINAL"):
        assert expected in out
    assert "Summary: 1 group(s) with confirmed duplicates, 1 file(s), 13 B to move." in out
    assert out.rstrip().endswith("No files were moved (dry run).")


def test_dry_run_never_modifies_folder(folder, capsys):
    before = snapshot(folder)

    main(["--dry-run", "--show-skipped", str(folder)])

    assert snapshot(folder) == before
    assert not (folder / "dups").exists()


def test_show_skipped(folder, capsys):
    main(["--dry-run", str(folder)])
    assert "Skipped files:" not in capsys.readouterr().out

    main(["--dry-run", "--show-skipped", str(folder)])
    out = capsys.readouterr().out
    assert "Skipped files:" in out
    assert "empty.txt" in out and "empty" in out
    assert "sub" in out and "directory" in out


def test_bytes_option(folder, capsys):
    main(["--dry-run", "--bytes", str(folder)])
    out = capsys.readouterr().out
    assert "Photo.jpg (13 bytes)" in out
    assert "13 bytes to move" in out


def test_read_error_message_is_shown(folder, capsys, monkeypatch):
    from duplicate_finder.core import hasher

    real = hasher.sha256_file

    def fail_on_copy(path, *args, **kwargs):
        if path.name == "Photo (1).jpg":
            raise PermissionError(13, "Access is denied")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(hasher, "sha256_file", fail_on_copy)
    main(["--dry-run", str(folder)])
    assert "READ_ERROR: Access is denied" in capsys.readouterr().out


def test_empty_folder(tmp_path, capsys):
    assert main(["--dry-run", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "No duplicate candidates found." in out
    assert "0 file(s)" in out


def test_invalid_folder_exit_code(tmp_path, capsys):
    assert main(["--dry-run", str(tmp_path / "missing")]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_folder_without_dry_run_is_refused(folder, capsys):
    before = snapshot(folder)
    assert main([str(folder)]) == 2
    assert "only --dry-run is supported" in capsys.readouterr().err
    assert snapshot(folder) == before


def test_dry_run_without_folder_is_refused(capsys):
    assert main(["--dry-run"]) == 2
    assert "FOLDER is required" in capsys.readouterr().err


def test_version(capsys):
    assert main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_subprocess_with_unicode_names(tmp_path):
    (tmp_path / "photo 📷.jpg").write_bytes(b"same")
    (tmp_path / "photo 📷 (1).jpg").write_bytes(b"same")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHONIOENCODING")}

    result = subprocess.run(
        [sys.executable, "-m", "duplicate_finder", "--dry-run", str(tmp_path)],
        cwd=PROJECT_ROOT, capture_output=True, timeout=60, env=env,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    out = result.stdout.decode("utf-8")
    assert "[move] photo 📷 (1).jpg" in out
    assert not (tmp_path / "dups").exists()
