"""Windows extended-length path helper, so OS calls work past 260 characters."""

import os.path
from pathlib import Path

_EXTENDED_PREFIX = "\\\\?\\"
_UNC_PREFIX = "\\\\"


def to_os_path(path: Path) -> str:
    """Return the string to pass to OS calls for an absolute path.

    The ``\\\\?\\`` prefix disables Windows path normalization, so the path is
    normalized here first.
    """
    text = str(path)
    if text.startswith(_EXTENDED_PREFIX):
        return text
    if not Path(text).is_absolute():
        raise ValueError(f"path must be absolute: {text}")
    text = os.path.normpath(text)
    if text.startswith(_UNC_PREFIX):
        return _EXTENDED_PREFIX + "UNC\\" + text[len(_UNC_PREFIX):]
    return _EXTENDED_PREFIX + text
