"""Creates the main window."""

import tkinter as tk
from tkinter import messagebox

from duplicate_finder import __version__
from duplicate_finder.core.config import load_config
from duplicate_finder.ui.main_window import MainWindow


def run() -> int:
    root = tk.Tk()
    root.title(f"Duplicate Finder {__version__}")
    root.minsize(900, 640)
    try:
        config = load_config()
    except RuntimeError as exc:
        messagebox.showerror("Duplicate Finder", str(exc), parent=root)
        root.destroy()
        return 1
    MainWindow(root, config)
    root.mainloop()
    return 0
