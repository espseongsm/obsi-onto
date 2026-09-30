"""macOS folder chooser for the local server; returns a path without indexing files."""

import subprocess
import sys
import threading
from pathlib import Path

_dialog_lock = threading.Lock()
_CHOOSE_FOLDER = """on run argv
    try
        activate
        set chosenFolder to choose folder with prompt "Obsidian 볼트 폴더를 선택하세요" ¬
            default location (POSIX file (item 1 of argv))
        return POSIX path of chosenFolder
    on error errorMessage number errorNumber
        if errorNumber is -128 then return ""
        error errorMessage number errorNumber
    end try
end run"""


def choose_vault_folder(initial_path=""):
    if sys.platform != "darwin":
        raise ValueError("Finder 폴더 선택은 macOS에서 지원합니다. 폴더 경로를 직접 입력하세요.")
    if not _dialog_lock.acquire(blocking=False):
        raise ValueError("이미 폴더 선택 창이 열려 있습니다. 해당 창에서 선택하거나 취소하세요.")
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
                "폴더 선택 창을 열지 못했습니다. 경로를 직접 입력하거나 다시 시도하세요."
            )
        selected = result.stdout.removesuffix("\n")
        if not selected:
            return None
        path = Path(selected)
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("선택한 폴더를 찾을 수 없습니다. 폴더 위치를 다시 확인하세요.")
        return str(path)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            "폴더 선택 시간이 만료되었습니다. Finder로 선택을 다시 눌러주세요."
        ) from exc
    except OSError as exc:
        raise ValueError("폴더 선택을 사용할 수 없습니다. 폴더 경로를 직접 입력하세요.") from exc
    finally:
        _dialog_lock.release()
