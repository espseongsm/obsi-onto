"""Metadata-only enumeration and stable, read-only local file access."""

import fnmatch
import os
import stat
from contextlib import contextmanager
from pathlib import Path

DEFAULT_EXCLUDES = {".obsidian", ".git", ".trash", "node_modules", ".venv"}
UF_DATALESS = 0x40000000  # Darwin: do not hydrate iCloud placeholders merely to index them.
MAX_NOTE_BYTES = 8 * 1024 * 1024


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
        raise ValueError("Paths outside the vault cannot be read.")
    if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise ValueError("Symbolic links are not indexed.")
    return path


def scan(root, rules):
    found, errors = {}, []
    if not root.is_dir():
        return {}, ["The vault could not be accessed."]

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
                    if not stat.S_ISREG(st.st_mode):
                        continue
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


@contextmanager
def source_parent(root, relative):
    """Open every relative ancestor without following links, anchored to the vault descriptor."""
    safe_path(root, relative)
    parts = Path(relative).parts
    if not parts or any(part in {"..", "."} for part in parts):
        raise ValueError("Enter a file path inside the vault.")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(root, flags)
    try:
        for part in parts[:-1]:
            child = os.open(part, flags, dir_fd=parent)
            os.close(parent)
            parent = child
        yield parent, parts[-1]
    finally:
        os.close(parent)


def read_stable(root, relative):
    with source_parent(root, relative) as (parent, name):
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Only regular Markdown files can be read.")
        if before.st_size > MAX_NOTE_BYTES:
            raise ValueError("Each note must be no larger than 8 MiB.")
        if getattr(before, "st_flags", 0) & UF_DATALESS:
            raise OSError("Waiting for iCloud download: the file is not available locally.")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as file:
            opened = os.fstat(file.fileno())
            if not stat.S_ISREG(opened.st_mode) or signature(opened) != signature(before):
                raise OSError("The file is being replaced. It will be checked again.")
            raw = file.read(MAX_NOTE_BYTES + 1)
            if len(raw) > MAX_NOTE_BYTES:
                raise ValueError("Each note must be no larger than 8 MiB.")
            after = os.fstat(file.fileno())
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if signature(after) != signature(before) or signature(current) != signature(before):
            raise OSError("The file is being saved. It will be checked again.")
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        return text, signature(before)
