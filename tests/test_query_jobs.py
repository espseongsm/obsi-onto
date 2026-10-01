import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.conflicts import overlay
from app.job_store import JobStore
from app.service import Service
from tests.conftest import FakeEmbedder, write


def finish(service, jid, expected="completed"):
    future = service.jobs.futures.get(jid)
    if future:
        future.result(timeout=5)
    job = service.jobs.get(jid)
    assert job["state"] == expected, job
    return job


def submit(service, **changes):
    return service.jobs.submit(
        {"question": "프로젝트 B 출시일", "mode": "lexical", "generate": False, **changes}
    )["id"]


def conflict_notes(service):
    a = write(service, "회의.md", "---\ndate: 2026-09-28\n---\n프로젝트 B 출시일: 2026-10-01")
    write(service, "계획.md", "---\ndate: 2026-09-28\n---\n프로젝트 B 출시일: 2026-10-15")
    service.indexer.reconcile()
    return a


def reply(job, choice="b"):
    return {
        "request_id": uuid.uuid4().hex,
        "question_id": job["confirmation"]["id"],
        "version": job["confirmation"]["version"],
        "choice": choice,
        "explanation": "",
    }


def test_progress_and_idempotent_creation(service):
    write(service, "업무.md", "프로젝트 B 출시일을 검토했다.")
    service.indexer.reconcile()
    request = {"question": "프로젝트 B", "mode": "lexical", "generate": False}
    rid = uuid.uuid4().hex
    first = service.jobs.submit(request, rid)
    second = service.jobs.submit(request, rid)
    assert first["id"] == second["id"]
    job = finish(service, first["id"])
    states = [e["state"] for e in job["events"]]
    assert states[0] == "queued" and states[-1] == "completed"
    assert all(
        stage in states
        for stage in ("retrieving", "verifying", "checking_conflicts", "generating", "finalizing")
    )
    assert [e["seq"] for e in job["events"]] == list(range(1, job["seq"] + 1))
    assert job["result"]["evidence"]
    assert len(service.store.rows("SELECT * FROM runs")) == 1
    with pytest.raises(ValueError):
        service.jobs.submit({**request, "question": "다른 질문"}, rid)


def test_conflict_wait_reply_and_duplicate_resume(service):
    conflict_notes(service)
    jid = submit(service)
    job = finish(service, jid, "awaiting_user")
    assert not service.store.rows("SELECT * FROM runs")
    assert len([n for n in job["graph"]["nodes"] if n.get("conflict")]) == 2
    assert any(e["kind"] == "conflict_candidate" for e in job["graph"]["edges"])
    assert overlay(job["graph"], [job["confirmation"]]) == job["graph"]
    response = reply(job)
    service.jobs.clarify(jid, response)
    result = finish(service, jid)["result"]
    duplicate = service.jobs.clarify(jid, response)
    assert duplicate["result"]["id"] == result["id"]
    assert len(service.store.rows("SELECT * FROM runs")) == 1
    assert result["clarifications"][0]["source"] == "user_clarification"
    assert result["clarifications"][0]["scope"] == "this_answer"
    assert not service.store.rows("SELECT * FROM candidates")
    assert "conflict_candidate" not in service.ontology.graph.serialize(format="turtle")
    with pytest.raises(ValueError):
        service.jobs.clarify(jid, {**response, "choice": "a"})


def test_defer_preserves_unresolved_sources_without_model_generation(service, monkeypatch):
    conflict_notes(service)
    service.generator.enabled = True
    monkeypatch.setattr(
        service.generator, "request", lambda *a: pytest.fail("defer must not choose")
    )
    jid = submit(service, generate=True)
    job = finish(service, jid, "awaiting_user")
    service.jobs.clarify(jid, reply(job, "defer"))
    result = finish(service, jid)["result"]
    assert not result["generated"]
    assert len(result["evidence"]) == 2
    assert result["conflicts"][0]["status"] == "deferred"


def test_waiting_source_change_invalidates_choice(service):
    file = conflict_notes(service)
    jid = submit(service)
    job = finish(service, jid, "awaiting_user")
    with pytest.raises(ValueError):
        service.jobs.clarify(jid, {**reply(job), "version": 99})
    file.write_text("프로젝트 B 출시일이 변경됐다.")
    service.jobs.clarify(jid, reply(job))
    changed = finish(service, jid, "stale")
    assert not changed["clarifications"] and not changed["confirmation"]
    assert not service.store.rows("SELECT * FROM runs")


@pytest.mark.parametrize("action", ["cancel", "clear", "exclude", "edit", "other_note"])
def test_model_wait_allows_indexing_and_discards_invalid_results(service, monkeypatch, action):
    file = write(service, "업무.md", "프로젝트 B 출시일 확인")
    service.indexer.reconcile()
    entered, release = threading.Event(), threading.Event()
    service.generator.enabled = True

    def slow(question, evidence, *args):
        entered.set()
        assert release.wait(5)
        return {"sentences": [{"text": "모델 답변", "citations": [evidence[0]["citation"]]}]}

    monkeypatch.setattr(service.generator, "request", slow)
    monkeypatch.setattr(service, "watch", lambda: None)
    jid = submit(service, generate=True)
    assert entered.wait(3)
    future = service.jobs.futures[jid]
    try:
        assert service.operation.acquire(timeout=1), "LLM wait must not hold operation"
        try:
            if action == "cancel":
                service.jobs.cancel(jid)
            elif action == "clear":
                service.clear()
            elif action == "exclude":
                service.register(str(service.indexer.root), ["업무.md"], r"^\d{4}-\d{2}-\d{2}$")
            elif action == "edit":
                file.write_text("다른 근거로 변경")
                service.indexer.reconcile(paths={"업무.md"})
            else:
                write(service, "새 기록.md", "무관한 기록 생성")
                service.indexer.reconcile(paths={"새 기록.md"})
                assert service.store.rows("SELECT * FROM notes WHERE path='새 기록.md'")
        finally:
            service.operation.release()
    finally:
        release.set()
        future.result(timeout=5)
    if action in {"clear", "cancel", "exclude"}:
        assert not service.store.rows("SELECT * FROM runs")
    elif action == "edit":
        result = finish(service, jid)["result"]
        assert not result["generated"] and not result["evidence"]
    else:
        assert finish(service, jid)["result"]["generated"]


def test_awaiting_user_survives_restart_without_automatic_approval(service):
    conflict_notes(service)
    jid = submit(service)
    original = finish(service, jid, "awaiting_user")
    service.close()
    recovered = Service(service.config, FakeEmbedder())
    try:
        job = recovered.jobs.get(jid)
        assert job["state"] == "awaiting_user" and job["confirmation"] == original["confirmation"]
        assert not recovered.store.rows("SELECT * FROM runs")
        recovered.jobs.clarify(jid, reply(job, "both"))
        finish(recovered, jid)
    finally:
        recovered.close()


def test_running_job_marked_interrupted_and_events_bounded(service):
    with service.operation:
        job, _ = service.jobs.repo.create(uuid.uuid4().hex, {"question": "업무"})
        for _ in range(70):
            service.jobs.repo.save(job, "retrieving", "실행 중")
        assert len(service.jobs.repo.events(job["id"])) == 64
        recovered = JobStore(service.store)
        assert recovered.get(job["id"])["state"] == "interrupted"


def test_atomic_nested_transaction_rolls_back_run_and_state(service):
    with pytest.raises(RuntimeError), service.store.transaction():
        service.store.put("outer-test", True)
        with service.store.transaction() as db:
            db.execute("INSERT INTO runs VALUES('rollback','','','{}')")
        raise RuntimeError("rollback")
    assert service.store.get("outer-test") is None
    assert not service.store.rows("SELECT * FROM runs")


def test_private_job_api_token_origin_limits_and_sse_replay(service, monkeypatch):
    write(service, "업무.md", "프로젝트 B 출시일 확인")
    service.indexer.reconcile()
    monkeypatch.setattr(service, "start", lambda: None)
    with TestClient(create_app(service.config, service)) as client:
        assert client.get("/api/status").status_code == 403
        assert (
            client.get("/api/session", headers={"sec-fetch-site": "cross-site"}).status_code == 403
        )
        client.headers["x-obsi-token"] = client.get("/api/session").json()["token"]
        assert (
            client.get("/api/status", headers={"origin": "https://testserver"}).status_code == 403
        )
        assert client.post("/api/query-jobs", content=b"x" * 65_537).status_code == 413
        response = client.post(
            "/api/query-jobs",
            json={"question": "프로젝트 B", "generate": False, "request_id": uuid.uuid4().hex},
        )
        assert response.status_code == 202
        jid = response.json()["id"]
        job = finish(service, jid)
        stream = client.get(f"/api/query-jobs/{jid}/events", headers={"Last-Event-ID": "2"})
        assert stream.status_code == 200 and "text/event-stream" in stream.headers["content-type"]
        assert "id: 1\n" not in stream.text and "id: 2\n" not in stream.text
        assert f"id: {job['seq']}\n" in stream.text
        assert "prompt" not in stream.text and "Authorization" not in stream.text
        assert client.get("/api/query-jobs/missing").status_code == 404


def test_vector_failure_recovers_without_changing_source_identity(service, monkeypatch):
    write(service, "업무.md", "프로젝트 B 출시일 확인")
    original = service.embedder.embed
    monkeypatch.setattr(
        service.embedder, "embed", lambda *a, **kw: (_ for _ in ()).throw(ValueError())
    )
    service.indexer.reconcile()
    note = service.store.rows("SELECT * FROM notes")[0]
    sections = service.store.rows("SELECT id,revision FROM sections")
    assert note["state"] == "ready" and note["vector_state"] == "pending"
    assert note["vector_retry_at"] > time.time()
    monkeypatch.setattr(service.embedder, "embed", original)
    service.indexer.reconcile(paths={"업무.md"})
    recovered = service.store.rows("SELECT * FROM notes")[0]
    assert recovered["vector_state"] == service.embedder.key
    assert recovered["vector_attempts"] == 0 and recovered["vector_retry_at"] == 0
    assert service.store.rows("SELECT id,revision FROM sections") == sections
    assert service.store.rows("SELECT rowid FROM vectors")
