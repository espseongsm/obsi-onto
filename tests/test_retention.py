import json
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import retention
from app.api import create_app
from app.conversations import turns
from tests.conftest import write

REFERENCE = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_retention_time(monkeypatch):
    monkeypatch.setattr(retention, "now", lambda: REFERENCE.isoformat())


def run(service, age, conversation_id=None, text="saved private evidence"):
    rid = uuid.uuid4().hex
    payload = {
        "id": rid,
        "question": "project question",
        "conversation_id": conversation_id or rid,
        "vault_epoch": service.store.get("vault_epoch"),
        "sentences": [{"text": text}],
        "evidence": [{"text": text}],
    }
    with service.store.transaction() as db:
        db.execute(
            "INSERT INTO runs VALUES(?,?,?,?)",
            (rid, (REFERENCE - age).isoformat(), payload["question"], json.dumps(payload)),
        )
    return rid


def job(service, age, *, run_id=None, conversation_id=None, context=None):
    request = {"question": "project question", "conversation_id": conversation_id}
    item, _ = service.jobs.repo.create(uuid.uuid4().hex, request, context)
    if run_id:
        item["payload"]["run_id"] = run_id
    item["payload"]["snapshot"] = {"evidence": [{"text": "saved private evidence"}]}
    service.jobs.repo.save(item, "completed" if run_id else "awaiting_user", "Saved")
    with service.store.transaction() as db:
        db.execute(
            "UPDATE query_jobs SET created_at=? WHERE id=?",
            ((REFERENCE - age).isoformat(), item["id"]),
        )
    return item["id"]


def test_default_keeps_history_and_reports_storage(service):
    rid = run(service, timedelta(days=400))
    model = service.config.data_dir / "models" / "weights.bin"
    model.parent.mkdir()
    model.write_bytes(b"model fixture")
    assert service.retention.cleanup_if_due() is None
    status = service.status()
    assert status["history"] == {
        "retention_days": 0,
        "runs": 1,
        "conversations": 1,
        "jobs": 0,
        "storage_warning": None,
    }
    assert status["storage_usage"]["models_bytes"] == model.stat().st_size
    assert status["storage_usage"]["database_bytes"] > 0
    assert turns(service.store, rid)


def test_exact_boundary_jobs_context_and_recent_turns(service):
    cid = uuid.uuid4().hex
    expired = run(service, timedelta(days=30, microseconds=1), cid)
    boundary = run(service, timedelta(days=30), cid)
    recent = run(service, timedelta(days=1), cid)
    expired_job = job(service, timedelta(days=31))
    associated_job = job(service, timedelta(days=1), run_id=expired)
    context_job = job(
        service,
        timedelta(days=1),
        conversation_id=cid,
        context=[{"question": "project question", "answer": "saved private evidence"}],
    )
    independent_job = job(service, timedelta(days=1), run_id=recent)
    deleted = service.retention.cleanup(30)
    assert deleted["deleted_runs"] == 1 and deleted["deleted_jobs"] == 3
    assert deleted["reclaimed_bytes"] >= 0
    assert {r["id"] for r in service.store.rows("SELECT id FROM runs")} == {boundary, recent}
    assert [j["id"] for j in service.store.rows("SELECT id FROM query_jobs")] == [independent_job]
    for jid in (expired_job, associated_job, context_job):
        assert not service.store.rows("SELECT * FROM query_events WHERE job_id=?", (jid,))
    assert len(turns(service.store, cid)) == 2


def test_opt_in_persists_and_cleanup_reclaims_pages_preserving_index_and_models(service):
    note = write(service, "keep.md", "project evidence remains")
    service.indexer.reconcile()
    root, epoch = service.indexer.root, service.store.get("vault_epoch")
    run(service, timedelta(days=100), text="private evidence " * 100_000)
    run(service, timedelta(days=1))
    model = service.config.data_dir / "models" / "weights.bin"
    model.parent.mkdir()
    model.write_bytes(b"unchanged model")
    before = service.retention.usage()
    saved = service.retention.configure(90)
    after = service.retention.usage()
    assert saved["deleted_runs"] == 1 and saved["retention_days"] == 90
    assert service.store.get("history_retention_days") == 90
    assert service.retention.cleanup_if_due() is None
    assert after["database_bytes"] + after["wal_bytes"] < (
        before["database_bytes"] + before["wal_bytes"]
    )
    assert saved["reclaimed_bytes"] > 0
    assert service.indexer.root == root and service.store.get("vault_epoch") == epoch
    assert service.store.rows("SELECT id FROM notes")
    assert service.store.rows("SELECT id FROM sections")
    assert service.store.rows("SELECT rowid FROM vectors")
    assert note.read_text() == "project evidence remains"
    assert model.read_bytes() == b"unchanged model"
    assert service.retention.configure(0)["deleted_runs"] == 0


@pytest.mark.parametrize("context_history", [False, True])
def test_cleanup_prevents_late_model_publication(service, monkeypatch, context_history):
    write(service, "keep.md", "project evidence remains")
    service.indexer.reconcile()
    cid = uuid.uuid4().hex
    if context_history:
        run(service, timedelta(days=31), cid)
    entered, release = threading.Event(), threading.Event()
    service.generator.enabled = True

    def slow(question, evidence, *args):
        entered.set()
        assert release.wait(5)
        return {
            "sentences": [{"text": "late private answer", "citations": [evidence[0]["citation"]]}]
        }

    monkeypatch.setattr(service.generator, "request", slow)
    request = {
        "question": "project",
        "mode": "lexical",
        "generate": True,
        "conversation_id": cid if context_history else None,
    }
    jid = service.jobs.submit(request)["id"]
    assert entered.wait(3)
    future = service.jobs.futures[jid]
    try:
        if not context_history:
            with service.store.transaction() as db:
                db.execute(
                    "UPDATE query_jobs SET created_at=? WHERE id=?",
                    ((REFERENCE - timedelta(days=31)).isoformat(), jid),
                )
        assert service.retention.cleanup(30)["deleted_jobs"] == 1
    finally:
        release.set()
        future.result(timeout=5)
    assert not service.store.rows("SELECT * FROM runs")
    assert not service.store.rows("SELECT * FROM query_jobs WHERE id=?", (jid,))
    assert not service.store.rows("SELECT * FROM query_events WHERE job_id=?", (jid,))


def test_retention_api_requires_token_and_valid_period(service, monkeypatch):
    monkeypatch.setattr(service, "start", lambda: None)
    with TestClient(create_app(service.config, service)) as client:
        assert client.post("/api/history/cleanup", json={"days": 30}).status_code == 403
        client.headers["x-obsi-token"] = client.get("/api/session").json()["token"]
        assert client.post("/api/history/cleanup", json={"days": 0}).status_code == 422
        assert client.post("/api/history/retention", json={"days": 45}).status_code == 422
        rid = run(service, timedelta(days=31))
        response = client.post("/api/history/cleanup", json={"days": 30})
        assert response.status_code == 200 and response.json()["deleted_runs"] == 1
        assert not service.store.rows("SELECT id FROM runs WHERE id=?", (rid,))
        assert client.post("/api/history/retention", json={"days": 0}).json()["saved"]


def test_automatic_cleanup_waits_for_daily_interval_and_respects_keep_all(service, monkeypatch):
    tick = [100.0]
    monkeypatch.setattr(retention.time, "monotonic", lambda: tick[0])
    service.retention.configure(30)
    rid = run(service, timedelta(days=31))
    assert service.retention.cleanup_if_due() is None
    assert service.store.rows("SELECT id FROM runs WHERE id=?", (rid,))
    tick[0] += 86400
    assert service.retention.cleanup_if_due()["deleted_runs"] == 1
    service.retention.configure(0)
    rid = run(service, timedelta(days=400))
    tick[0] += 86400
    assert service.retention.cleanup_if_due() is None
    assert service.store.rows("SELECT id FROM runs WHERE id=?", (rid,))


def test_cleanup_discards_late_relationship_extraction(service, monkeypatch):
    write(service, "keep.md", "project evidence remains")
    service.indexer.reconcile()
    result = service.search.ask("project", mode="lexical", generate=False)
    with service.store.transaction() as db:
        db.execute(
            "UPDATE runs SET created_at=? WHERE id=?",
            ((REFERENCE - timedelta(days=31)).isoformat(), result["id"]),
        )
    entered, release = threading.Event(), threading.Event()

    def slow(question, evidence, *args):
        entered.set()
        assert release.wait(5)
        return {
            "candidates": [
                {
                    "citation": evidence[0]["citation"],
                    "kind": "Claim",
                    "topic": "project",
                    "quote": evidence[0]["text"],
                }
            ]
        }

    monkeypatch.setattr(service.generator, "request", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.search.extract, result["id"])
        try:
            assert entered.wait(3)
            assert service.retention.cleanup(30)["deleted_runs"] == 1
        finally:
            release.set()
        with pytest.raises(ValueError, match="deleted"):
            future.result(timeout=5)
    assert not service.store.rows("SELECT * FROM candidates")


def test_compaction_failure_keeps_deletion_success_and_allows_retry(service, monkeypatch):
    run(service, timedelta(days=31), text="private evidence " * 10_000)
    original = service.store.compact

    def fail():
        raise sqlite3.OperationalError("private fixture path and raw SQL")

    monkeypatch.setattr(service.store, "compact", fail)
    deleted = service.retention.cleanup(30)
    assert deleted["deleted_runs"] == 1 and deleted["storage_warning"]
    assert "private fixture" not in deleted["storage_warning"]
    assert not service.store.rows("SELECT * FROM runs")
    assert service.status()["history"]["storage_warning"] == deleted["storage_warning"]
    monkeypatch.setattr(service.store, "compact", original)
    retried = service.retention.cleanup(30)
    assert retried["deleted_runs"] == 0 and retried["storage_warning"] is None


def test_automatic_cleanup_database_error_does_not_stop_service(service, monkeypatch):
    service.store.put("history_retention_days", 30)

    def fail(days):
        raise sqlite3.OperationalError("private fixture path and raw SQL")

    monkeypatch.setattr(service.retention, "cleanup", fail)
    assert service.retention.cleanup_if_due() is None
    assert service.status()["history"]["storage_warning"]
    assert service.retention.last_cleanup is not None
