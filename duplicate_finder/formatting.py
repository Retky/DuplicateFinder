"""Presentation helpers shared by the console report and the UI."""

_UNITS = ("KB", "MB", "GB", "TB")


def format_size(size: int) -> str:
    """Human-readable size using 1024-based steps, like Windows Explorer."""
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in _UNITS:
        value /= 1024
        if round(value, 1) < 1024 or unit == _UNITS[-1]:
            return f"{value:.1f} {unit}"
    raise AssertionError("unreachable")
