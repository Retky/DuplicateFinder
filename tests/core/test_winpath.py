from pathlib import Path

import pytest

from duplicate_finder.core.winpath import to_os_path


def test_local_path_gets_extended_prefix():
    assert to_os_path(Path(r"C:\Photos\a.jpg")) == r"\\?\C:\Photos\a.jpg"


def test_local_path_is_normalized():
    assert to_os_path(Path("C:/Photos/sub/../a.jpg")) == r"\\?\C:\Photos\a.jpg"


def test_unc_path_gets_unc_prefix():
    assert to_os_path(Path(r"\\server\share\Photos\a.jpg")) == r"\\?\UNC\server\share\Photos\a.jpg"


def test_already_prefixed_path_is_unchanged():
    assert to_os_path(Path(r"\\?\C:\Photos\a.jpg")) == r"\\?\C:\Photos\a.jpg"


@pytest.mark.parametrize("relative", [Path("a.jpg"), Path(r"Photos\a.jpg"), Path(r"\Photos\a.jpg")])
def test_relative_path_rejected(relative):
    with pytest.raises(ValueError):
        to_os_path(relative)
