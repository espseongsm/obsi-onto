"""macOS folder chooser for the local server; returns a path without indexing files."""

import subprocess
import sys
import threading
from pathlib import Path

_dialog_lock = threading.Lock()
_CHOOSE_FOLDER = """on run argv
    try
        activate
        set chosenFolder to choose folder with prompt "Choose your Obsidian vault folder" ¬
            default location (POSIX file (item 1 of argv))
        return POSIX path of chosenFolder
    on error errorMessage number errorNumber
        if errorNumber is -128 then return ""
        error errorMessage number errorNumber
    end try
end run"""


def choose_vault_folder(initial_path=""):
    if sys.platform != "darwin":
        raise ValueError(
            "Finder folder selection is available on macOS. Enter the folder path manually."
        )
    if not _dialog_lock.acquire(blocking=False):
        raise ValueError(
            "A folder selection window is already open. Choose a folder or cancel in that window."
        )
    try:
        start = Path(initial_path).expanduser() if initial_path else Path.home()
        if not start.is_absolute() or not start.is_dir():
            start = Path.home()
        # Paths are argv data, never interpolated into AppleScript or a shell command.
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", _CHOOSE_FOLDER, str(start)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode:
            raise ValueError(
                "Could not open the folder selection window. Enter the path manually or try again."
            )
        selected = result.stdout.removesuffix("\n")
        if not selected:
            return None
        path = Path(selected)
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("The selected folder was not found. Check its location.")
        return str(path)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Folder selection timed out. Choose the Finder option again.") from exc
    except OSError as exc:
        raise ValueError(
            "Folder selection is unavailable. Enter the folder path manually."
        ) from exc
    finally:
        _dialog_lock.release()
