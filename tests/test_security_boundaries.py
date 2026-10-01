import os
import stat

import pytest

from app import files
from app.conflicts import rule_candidates, validate_candidates
from app.markdown import links_in, parse_markdown
from tests.conftest import write


def test_fifo_rejected_without_blocking_and_other_notes_index(service, monkeypatch):
    fifo = service.indexer.root / "pipe.md"
    os.mkfifo(fifo)
    write(service, "valid.md", "정상 노트")
    real_open = files.os.open

    def checked_open(path, flags, *args, **kwargs):
        if str(path).endswith("pipe.md"):
            assert flags & os.O_NONBLOCK
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(files.os, "open", checked_open)
    with pytest.raises(ValueError, match="regular Markdown"):
        files.read_stable(service.indexer.root, "pipe.md")
    service.indexer.reconcile()
    assert [n["path"] for n in service.store.rows("SELECT path FROM notes")] == ["valid.md"]
    service.indexer.reconcile(paths={"pipe.md"})


def test_ancestor_swap_after_path_validation_cannot_read_outside(service, tmp_path, monkeypatch):
    write(service, "folder/note.md", "inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "note.md").write_text("outside secret fixture")
    original = files.safe_path

    def swap(root, relative):
        result = original(root, relative)
        (root / "folder").rename(root / "moved")
        (root / "folder").symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(files, "safe_path", swap)
    with pytest.raises(OSError):
        files.read_stable(service.indexer.root, "folder/note.md")


def test_descriptor_nonregular_race_is_rejected(service, monkeypatch):
    path = write(service, "note.md", "normal")
    real_open = files.os.open

    def swap(pathname, flags, *args, **kwargs):
        if pathname == "note.md":
            path.unlink()
            os.mkfifo(path)
            assert flags & os.O_NONBLOCK
        return real_open(pathname, flags, *args, **kwargs)

    monkeypatch.setattr(files.os, "open", swap)
    with pytest.raises(OSError, match="replaced"):
        files.read_stable(service.indexer.root, "note.md")
    assert stat.S_ISFIFO(path.stat().st_mode)


def test_note_size_and_metadata_complexity_are_bounded(service, monkeypatch):
    write(service, "large.md", "가" * 100)
    monkeypatch.setattr(files, "MAX_NOTE_BYTES", 100)
    with pytest.raises(ValueError):
        files.read_stable(service.indexer.root, "large.md")
    alias = "---\na: &a [x, x]\nb: &b [*a, *a]\n---\n본문"
    with pytest.raises(ValueError, match="aliases"):
        parse_markdown(alias, "alias.md")
    with pytest.raises(ValueError, match="complex"):
        parse_markdown("---\na: " + "[" * 30 + "x" + "]" * 30 + "\n---", "deep.md")
    with pytest.raises(ValueError, match="Each line"):
        parse_markdown("[" * 20_000, "long.md")
    assert links_in("[" * 16_000, 1) == []
    assert [i["target"] for i in links_in("[[정상]] [자료](https://example.test)", 1)] == [
        "정상",
        "https://example.test",
    ]
    parsed = parse_markdown("---\ndate: 2026-09-28\naliases: [별칭]\n---\n본문", "ok.md")
    assert parsed["record_date"] == "2026-09-28" and parsed["aliases"] == ["별칭"]


def test_temporal_changes_and_missing_quotes_not_promoted_to_conflicts():
    evidence = [
        {"citation": "S1", "record_date": "2026-09-01", "text": "프로젝트 B 출시일: 2026-10-01"},
        {"citation": "S2", "record_date": "2026-09-28", "text": "프로젝트 B 출시일: 2026-10-15"},
    ]
    assert rule_candidates(evidence) == []
    candidate = {
        "subject": "프로젝트 B",
        "question": "어느 일정?",
        "reason": "확인 필요",
        "classification": "needs_context",
        "a": {"citation": "S1", "quote": "없는 문장"},
        "b": {"citation": "S2", "quote": evidence[1]["text"]},
    }
    assert validate_candidates({"conflicts": [candidate]}, evidence) == []
    candidate["a"]["citation"] = "S999"
    assert validate_candidates({"conflicts": [candidate]}, evidence) == []
