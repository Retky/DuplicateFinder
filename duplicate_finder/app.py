from duplicate_finder import __version__


def run(argv: list[str] | None = None) -> int:
    if argv:
        from duplicate_finder.cli import main

        return main(argv)
    print(f"Duplicate Finder {__version__}")
    return 0
