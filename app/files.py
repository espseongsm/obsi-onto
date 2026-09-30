"""Metadata-only enumeration and stable, read-only local file access."""

import fnmatch
import os
from pathlib import Path

DEFAULT_EXCLUDES = {".obsidian", ".git", ".trash", "node_modules", ".venv"}
UF_DATALESS = 0x40000000  # Darwin: do not hydrate iCloud placeholders merely to index them.


def excluded(relative, rules):
    path = Path(relative)
    return any(part in DEFAULT_EXCLUDES for part in path.parts) or any(
        relative == rule.rstrip("/")
        or relative.startswith(rule.rstrip("/") + "/")
        or fnmatch.fnmatch(relative, rule)
        for rule in rules
    )


def signature(stat):
    return f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}"


def safe_path(root, relative):
    path = root / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(root):
        raise ValueError("볼트 밖의 경로는 읽을 수 없습니다.")
    if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise ValueError("심볼릭 링크는 색인하지 않습니다.")
    return path


def scan(root, rules):
    found, errors = {}, []
    if not root.is_dir():
        return {}, ["볼트에 접근할 수 없습니다."]

    def visit(folder):
        try:
            entries = list(os.scandir(folder))
            for entry in entries:
                relative = str(Path(entry.path).relative_to(root))
                if excluded(relative, rules) or entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    visit(Path(entry.path))
                elif entry.name.lower().endswith(".md"):
                    st = entry.stat(follow_symlinks=False)
                    found[relative] = {
                        "signature": signature(st),
                        "identity": f"{st.st_dev}:{st.st_ino}",
                        "available": not bool(getattr(st, "st_flags", 0) & UF_DATALESS),
                    }
                elif entry.name.endswith(".md.icloud"):
                    logical = Path(relative).with_name(entry.name.lstrip(".")[:-7])
                    found[str(logical)] = {
                        "signature": "icloud",
                        "identity": "",
                        "available": False,
                    }
        except OSError as exc:
            errors.append(f"{folder.relative_to(root)}: {exc.strerror}")

    visit(root)
    return found, errors


def read_stable(root, relative):
    path = safe_path(root, relative)
    before = path.stat()
    if getattr(before, "st_flags", 0) & UF_DATALESS:
        raise OSError("iCloud 다운로드 대기: 로컬에 파일이 없습니다.")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8-sig") as file:
        if signature(os.fstat(file.fileno())) != signature(before):
            raise OSError("파일 교체 중입니다. 다시 확인합니다.")
        text = file.read()
    if signature(path.stat()) != signature(before):
        raise OSError("파일 저장 중입니다. 다시 확인합니다.")
    return text, signature(before)
