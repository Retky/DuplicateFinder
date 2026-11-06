# Duplicate Finder

Windows desktop tool that finds duplicate files in a folder and moves the extra copies to a `dups` subfolder. It runs fully offline and never deletes files.

## Requirements

- Windows 10/11.
- [pyenv-win](https://github.com/pyenv-win/pyenv-win).
- Python 3.12 (the exact version is in `.python-version`).

## Set up the environment

From the project folder, in PowerShell:

```powershell
pyenv install 3.12.10
pyenv exec python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

`pyenv install` is only needed the first time. Because of `.python-version`, `pyenv exec python` uses 3.12 inside this folder.

## Run the application

With the virtual environment activated, this opens the desktop window:

```powershell
python main.py
```

or:

```powershell
python -m duplicate_finder
```

With no arguments the window opens. Any arguments stay on the console, including `--version` and `--dry-run`.

## Windows executable

From the project folder:

```powershell
.\build.ps1
```

The script runs the tests, then writes `dist\DuplicateFinder\DuplicateFinder.exe`. That folder is the application: it does not need Python installed and it does not use the network. The executable opens the window and has no console. Double-click it, choose a folder, analyze it, review the ticked copies, and move the ones you leave selected into `dups`. The dry run below still uses Python.

The build is not code-signed. Windows SmartScreen may warn that the publisher is unknown. Code signing is out of scope for v1.

## Dry run (console)

Analyze a folder and print what would be moved, without touching anything:

```powershell
python -m duplicate_finder --dry-run "C:\path\to\folder"
```

Options: `--show-skipped` lists ignored files and the reason, `--bytes` shows exact sizes. The console never moves files; a folder without `--dry-run` is refused.

## Run the tests

```powershell
pytest
```

## Dependencies

The application only uses the Python standard library. `requirements-dev.txt` lists the development tools (`pytest` and `pyinstaller`).
