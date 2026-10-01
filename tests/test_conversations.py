import json
import uuid

from fastapi.testclient import TestClient

from app.api import create_app
from app.conversations import context, recent, retrieval_question, turns
from tests.conftest import write


def ask(service, conversation_id, question, generate=False):
    return service.jobs.ask(
        {
            "question": question,
            "conversation_id": conversation_id,
            "mode": "lexical",
            "generate": generate,
        }
    )


def test_follow_up_uses_previous_subject_but_current_sources(service, monkeypatch):
    write(service, "알파.md", "프로젝트 알파의 결론은 고객 온보딩 개선이다.")
    write(service, "시장.md", "시장 전망은 성장 둔화다.")
    service.indexer.reconcile()
    cid = uuid.uuid4().hex
    first = ask(service, cid, "프로젝트 알파의 결론은?")
    assert first["evidence"] and first["conversation_id"] == cid
    captured = []
    service.generator.enabled = True

    def generate(question, evidence, *args):
        captured.append((question, evidence, args))
        return {
            "sentences": [
                {"text": "고객 온보딩 개선입니다.", "citations": [evidence[0]["citation"]]}
            ]
        }

    monkeypatch.setattr(service.generator, "request", generate)
    second = ask(service, cid, "그때 결론은?", generate=True)
    assert second["generated"]
    assert [item["path"] for item in second["evidence"]] == ["알파.md"]
    assert captured[0][2][1]["conversation"][0]["question"] == first["question"]
    assert [run["id"] for run in turns(service.store, cid)] == [first["id"], second["id"]]
    assert recent(service.store)[0]["turns"] == 2
    third = ask(service, cid, "시장 전망은?")
    assert [item["path"] for item in third["evidence"]] == ["시장.md"]
    prior = context(service.store, cid, service.store.get("vault_epoch"))
    assert retrieval_question("시장 전망은?", prior) == "시장 전망은?"


def test_conversation_context_stops_at_vault_settings_epoch(service):
    write(service, "노트.md", "프로젝트 알파의 결론은 개선이다.")
    service.indexer.reconcile()
    cid = uuid.uuid4().hex
    ask(service, cid, "프로젝트 알파의 결론은?")
    assert context(service.store, cid, service.store.get("vault_epoch"))
    service.jobs.invalidate()
    assert context(service.store, cid, service.store.get("vault_epoch")) == []


def test_follow_up_context_keeps_latest_twenty_turns(service):
    cid = uuid.uuid4().hex
    epoch = service.store.get("vault_epoch")
    with service.store.transaction() as db:
        for number in range(22):
            payload = {
                "conversation_id": cid,
                "vault_epoch": epoch,
                "question": f"질문 {number}",
                "sentences": [{"text": f"답변 {number}"}],
            }
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?)",
                (
                    f"{number:032x}",
                    f"2026-09-29T00:00:{number:02d}",
                    payload["question"],
                    json.dumps(payload),
                ),
            )
    previous = context(service.store, cid, epoch)
    assert [turn["question"] for turn in previous] == [f"질문 {number}" for number in range(2, 22)]
    assert previous[-1]["answer"] == "답변 21"


def test_conversation_api_requires_session_token(service):
    write(service, "노트.md", "프로젝트 알파")
    service.indexer.reconcile()
    cid = uuid.uuid4().hex
    ask(service, cid, "프로젝트 알파")
    with TestClient(create_app(service=service)) as client:
        assert client.get("/api/conversations").status_code == 403
        token = client.get("/api/session").json()["token"]
        headers = {"X-Obsi-Token": token}
        assert client.get("/api/conversations", headers=headers).json()[0]["id"] == cid
        assert len(client.get(f"/api/conversations/{cid}", headers=headers).json()) == 1
        assert client.get("/api/conversations/invalid", headers=headers).status_code == 404
