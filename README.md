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

With the virtual environment activated:

```powershell
python main.py
```

or:

```powershell
python -m duplicate_finder
```

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
