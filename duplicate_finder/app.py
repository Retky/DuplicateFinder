def run(argv: list[str] | None = None) -> int:
    if argv:
        from duplicate_finder.cli import main

        return main(argv)
    from duplicate_finder.ui.app import run as run_window

    return run_window()
