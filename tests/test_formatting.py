import pytest

from duplicate_finder.formatting import format_size

KB = 1024
MB = KB * 1024
GB = MB * 1024
TB = GB * 1024


@pytest.mark.parametrize(
    "size, expected",
    [
        (0, "0 B"),
        (1, "1 B"),
        (1023, "1023 B"),
        (KB, "1.0 KB"),
        (1536, "1.5 KB"),
        (MB - 1, "1.0 MB"),
        (MB - 60, "1023.9 KB"),
        (MB, "1.0 MB"),
        (int(2.4 * MB), "2.4 MB"),
        (GB, "1.0 GB"),
        (int(1.25 * GB), "1.2 GB"),
        (TB, "1.0 TB"),
        (2048 * TB, "2048.0 TB"),
    ],
)
def test_format_size(size, expected):
    assert format_size(size) == expected
