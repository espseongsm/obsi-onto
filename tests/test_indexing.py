import os

import pytest

from app.files import read_stable, scan
from app.markdown import parse_markdown
from tests.conftest import write


def test_read_only_scan_and_exact_source_spans(service, tmp_path):
    raw = (
        "---\ndate: 2026-09-24\naliases: [일지]\n---\n# 기록\n\n"
        "## 업무\n프로젝트 B 검토\n\n## 투자\n매수 검토만 했다.\n"
    )
    file = write(service, "2026-09-24.md", raw)
    write(service, "Private/비밀.md", "비공개")
    write(service, ".obsidian/설정.md", "제외")
    outside = tmp_path / "outside.md"
    outside.write_text("외부")
    (service.indexer.root / "탈출.md").symlink_to(outside)
    before = file.stat().st_mtime_ns
    service.indexer.reconcile()
    assert len(service.store.rows("SELECT * FROM notes")) == 1
    assert file.read_text() == raw and file.stat().st_mtime_ns == before
    for section in service.store.rows("SELECT * FROM sections"):
        assert section["text"] == "\n".join(raw.splitlines()[section["start"] - 1 : section["end"]])
    with pytest.raises(ValueError):
        read_stable(service.indexer.root, "../outside.md")
    with pytest.raises(ValueError):
        read_stable(service.indexer.root, "탈출.md")


def test_unchanged_scan_has_zero_content_reads_and_embedding_calls(service, monkeypatch):
    write(service, "노트.md", "# 업무\n\n계약 검토를 진행했다.")
    service.indexer.reconcile()
    assert service.embedder.calls > 0

    def forbidden(*args):
        pytest.fail("An unchanged metadata scan must not read file bodies")

    monkeypatch.setattr("app.indexer.read_stable", forbidden)
    service.indexer.reconcile()
    assert service.indexer.metrics["body_reads"] == 0
    assert service.indexer.metrics["embedding_calls"] == 0


def test_repeated_saves_reuse_vectors_and_overwrite_keeps_note_id(service):
    file = write(service, "일지.md", "# 업무\n\n결제 조회 작업")
    service.indexer.reconcile()
    original = service.store.rows("SELECT * FROM notes")[0]
    calls = service.embedder.calls
    file.write_text(file.read_text())
    service.indexer.reconcile()
    assert service.embedder.calls == calls
    file.unlink()
    file.write_text("# 업무\n\n계약 조회 작업")
    service.indexer.reconcile(paths={"일지.md"})
    current = service.store.rows("SELECT * FROM notes")[0]
    assert current["id"] == original["id"]
    assert current["revision"] == original["revision"] + 1
    assert not service.store.rows("SELECT * FROM sections WHERE text LIKE '%결제%'")


def test_event_checks_same_size_and_timestamp(service):
    file = write(service, "일지.md", "첫째 생각")
    service.indexer.reconcile()
    stat = file.stat()
    file.write_text("둘째 생각")
    os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    service.indexer.reconcile()
    assert service.indexer.metrics["body_reads"] == 0
    service.indexer.reconcile(paths={"일지.md"})
    assert service.store.rows("SELECT text FROM sections")[0]["text"] == "둘째 생각"


def test_rename_keeps_id_and_rechecks_incoming_links(service):
    file = write(service, "대상.md", "# 업무\n\n회의 결정")
    write(service, "연결.md", "[[대상]]")
    service.indexer.reconcile()
    old = service.store.rows("SELECT id FROM notes WHERE path='대상.md'")[0]["id"]
    file.rename(file.with_name("이동.md"))
    service.indexer.reconcile(paths={"대상.md", "이동.md"})
    assert service.store.rows("SELECT id FROM notes WHERE path='이동.md'")[0]["id"] == old
    assert service.store.rows("SELECT status FROM links")[0]["status"] == "unresolved"


def test_delete_removes_search_but_preserves_run_snapshot(service):
    file = write(service, "업무.md", "# 업무\n\n결제 화면 작성 완료")
    service.indexer.reconcile()
    result = service.search.ask("결제 화면", mode="lexical")
    assert result["evidence"]
    file.unlink()
    service.indexer.reconcile()
    assert not service.search.ask("결제 화면", mode="lexical")["evidence"]
    assert (
        "결제"
        in service.store.rows("SELECT payload FROM runs WHERE id=?", (result["id"],))[0]["payload"]
    )
    assert not service.store.rows("SELECT rowid FROM words")
    assert not service.store.rows("SELECT rowid FROM vectors")


def test_read_failure_is_not_deletion_and_excludes_old_evidence(service, monkeypatch):
    file = write(service, "일지.md", "이전 근거")
    service.indexer.reconcile()
    file.write_text("변경 근거")

    def failed(*args):
        raise OSError("iCloud download pending")

    monkeypatch.setattr("app.indexer.read_stable", failed)
    service.indexer.reconcile()
    assert service.store.rows("SELECT state FROM notes")[0]["state"] == "error"
    assert not service.search.ask("이전 근거", mode="lexical")["evidence"]


def test_scan_failure_does_not_delete_records(service, monkeypatch):
    write(service, "노트.md", "중요 기록")
    service.indexer.reconcile()
    monkeypatch.setattr("app.indexer.scan", lambda *args: ({}, ["접근 불가"]))
    service.indexer.reconcile()
    assert service.store.rows("SELECT state FROM notes")[0]["state"] == "pending"


def test_source_changed_during_embedding_cannot_publish(service, monkeypatch):
    file = write(service, "노트.md", "첫 번째 원문")
    original = service.embedder.embed

    def changed(texts, query=False):
        result = original(texts, query)
        file.write_text("새로운 원문")
        return result

    monkeypatch.setattr(service.embedder, "embed", changed)
    service.indexer.reconcile()
    assert service.store.rows("SELECT state FROM notes")[0]["state"] == "error"
    assert not service.store.rows("SELECT * FROM sections")


def test_icloud_placeholder_does_not_read_or_delete(service):
    write(service, ".일지.md.icloud", "placeholder")
    found, errors = scan(service.indexer.root, [])
    assert not errors and not found["일지.md"]["available"]
    service.indexer.reconcile()
    assert service.indexer.metrics["body_reads"] == 0
    assert service.store.rows("SELECT state FROM notes")[0]["state"] == "error"


def test_frontmatter_dates_and_domains_are_not_inferred_event_dates():
    parsed = parse_markdown(
        "---\ndate: 2026-09-24\n---\n## 업무\n2026-09-20 회의\n\n## 투자\n매수 검토", "일지.md"
    )
    assert parsed["record_date"] == "2026-09-24"
    assert parsed["sections"][-1]["domains"] == ["investment"]
    assert parse_markdown("날짜 없음", "메모.md")["record_date"] is None


def test_broken_yaml_does_not_block_other_notes(service):
    write(service, "broken.md", "---\naliases: [broken\n---\n잘못된 YAML")
    write(service, "valid.md", "올바른 원문")
    service.indexer.reconcile()
    states = {n["path"]: n["state"] for n in service.store.rows("SELECT * FROM notes")}
    assert states == {"broken.md": "error", "valid.md": "ready"}


def test_unchanged_scan_does_not_rebuild_graph(service, monkeypatch):
    write(service, "노트.md", "업무 내용")
    service.indexer.reconcile()
    service.ontology.refresh()

    def forbidden(*args, **kwargs):
        pytest.fail("Unchanged scans must reuse the validated graph")

    monkeypatch.setattr("app.ontology.validate", forbidden)
    service.indexer.reconcile()
    service.ontology.refresh()


def test_model_change_rebuilds_vectors_without_mixing(service):
    write(service, "노트.md", "업무 결제 확인")
    service.indexer.reconcile()
    service.embedder.key = "test-vector-v2"
    result = service.search.ask("결제 확인", mode="semantic")
    assert all("의미" not in e["routes"] for e in result["evidence"])
    service.indexer.reconcile()
    assert (
        service.store.rows("SELECT vector_state FROM notes")[0]["vector_state"] == "test-vector-v2"
    )
    assert any("의미" in e["routes"] for e in service.search.ask("결제 확인")["evidence"])


def test_inline_and_fenced_code_do_not_create_links():
    parsed = parse_markdown("`[[가짜]]` 실제 [[진짜]]\n\n```\n[[코드]]\n```", "기록.md")
    assert [link["target"] for s in parsed["sections"] for link in s["links"]] == ["진짜"]
