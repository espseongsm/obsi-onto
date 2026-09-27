import json

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.suggestions import Suggestions
from tests.conftest import write
from tests.test_api_watcher import wait_until


def questions(evidence):
    return {
        "questions": [
            {
                "title": row["title"],
                "question": f"{row['title']}에 적힌 검토 내용을 알려줘",
                "topic": row["title"],
                "citations": [row["citation"]],
            }
            for row in evidence[:6]
        ]
    }


def test_vault_changes_regenerate_after_indexing_but_tuning_and_same_settings_do_not(
    service, monkeypatch
):
    write(service, "프로젝트.md", "## 업무\n시연 일정과 계약 검토")
    write(service, "투자.md", "## 투자\n현금흐름을 검토한 기록")
    monkeypatch.setattr(service, "watch", lambda: None)
    service.generator.enabled = True
    calls = []

    def request(question, evidence, task):
        assert task == "suggestions"
        assert not service.store.rows("SELECT id FROM notes WHERE state!='ready'")
        calls.append(evidence)
        return questions(evidence)

    monkeypatch.setattr(service.generator, "request", request)
    with TestClient(create_app(service.config, service)) as client:
        headers = {"x-obsi-token": client.get("/api/session").json()["token"]}
        wait_until(lambda: service.store.get("suggestions", {}).get("state") == "ready")
        original = client.get("/api/status").json()["suggestions"]
        assert len(original["items"]) == 2 and len(calls) == 1
        settings = {"path": str(service.indexer.root), "excludes": ["Private"]}
        previous_scan = service.store.get("last_scan")
        assert client.post("/api/vault", headers=headers, json=settings).status_code == 200
        wait_until(lambda: service.store.get("last_scan") != previous_scan and not service.task)
        assert service.store.get("suggestions") == original and len(calls) == 1
        client.post(
            "/api/settings", headers=headers, json={"debounce": 2, "reconcile_seconds": 600}
        )
        service.request_scan()
        wait_until(lambda: service.scan_due is None and not service.task)
        assert len(calls) == 1
        settings["excludes"].append("투자.md")
        assert client.post("/api/vault", headers=headers, json=settings).status_code == 200
        wait_until(lambda: len(calls) == 2 and service.store.get("suggestions")["state"] == "ready")
        updated = client.get("/api/status").json()["suggestions"]
        assert [item["title"] for item in updated["items"]] == ["프로젝트"]
        assert all(row["title"] != "투자" for row in calls[-1])
        settings["daily_filename_dates"] = False
        assert client.post("/api/vault", headers=headers, json=settings).status_code == 200
        wait_until(lambda: len(calls) == 3 and service.store.get("suggestions")["state"] == "ready")
        assert not client.get("/api/status").json()["daily_filename_dates"]
        assert client.delete("/api/data", headers=headers).status_code == 200
        assert not client.get("/api/status").json()["suggestions"]["items"]


def test_setting_change_hides_previous_cards_and_new_vault_has_no_old_sources(
    service, monkeypatch, tmp_path
):
    monkeypatch.setattr(service, "watch", lambda: None)
    write(service, "기존.md", "기존 내용")
    service.indexer.reconcile()
    service.suggestions.reset()
    service.suggestions.refresh()
    assert service.store.get("suggestions")["items"]
    service.register(str(service.indexer.root), ["기존.md"], "")
    assert service.store.get("suggestions")["state"] == "pending"
    assert not service.status()["suggestions"]["items"]
    service.indexer.reconcile()
    service.suggestions.refresh()
    assert service.store.get("suggestions")["state"] == "empty"
    service.clear()
    other = tmp_path / "새 볼트"
    other.mkdir()
    (other / "새 프로젝트.md").write_text("새 프로젝트의 일정 검토")
    service.register(str(other), [], "")
    service.indexer.reconcile()
    service.suggestions.refresh()
    assert service.store.get("suggestions")["items"][0]["sources"][0]["path"] == "새 프로젝트.md"


def test_model_sample_is_bounded_diverse_excludes_private_and_cache_is_reused(service, monkeypatch):
    for index in range(26):
        write(service, f"업무/{index:02}.md", "---\ndomain: work\n---\n" + "업무 본문 " * 160)
    write(service, "투자.md", "## 투자\n분석 근거")
    write(service, "개인.md", "## 개인\n생각 기록")
    write(service, "Private/비밀.md", "비밀 자료")
    service.indexer.reconcile()
    service.generator.enabled = True
    calls = []

    def request(question, evidence, task):
        calls.append(evidence)
        assert len(evidence) == 24
        assert all(len(row["text"]) <= 600 for row in evidence)
        assert {"투자", "개인"} <= {row["title"] for row in evidence[:4]}
        assert "비밀" not in json.dumps(evidence, ensure_ascii=False)
        return questions(evidence)

    monkeypatch.setattr(service.generator, "request", request)
    service.suggestions.reset()
    service.suggestions.refresh()
    cached = service.store.get("suggestions")
    assert cached["state"] == "ready" and len(cached["items"]) == 6
    assert "text" not in cached["items"][0]["sources"][0]
    Suggestions(service.store, service.search, service.generator).refresh()
    assert len(calls) == 1 and service.store.get("suggestions") == cached


@pytest.mark.parametrize("failure", ["timeout", "citation", "topic", "format"])
def test_generation_failure_uses_real_note_titles_without_repeated_calls(
    service, monkeypatch, failure
):
    write(service, "실제 노트.md", "실제 판단 근거")
    service.indexer.reconcile()
    service.generator.enabled = True
    calls = []

    def request(question, evidence, task):
        calls.append(1)
        if failure == "timeout":
            raise TimeoutError("secret provider response must not reach UI")
        result = questions(evidence)
        if failure == "citation":
            result["questions"][0]["citations"] = ["S999999"]
        elif failure == "topic":
            result["questions"][0].update(topic="가상기업", question="가상기업은 어떤가?")
        else:
            result = {"questions": "invalid"}
        return result

    monkeypatch.setattr(service.generator, "request", request)
    service.suggestions.reset()
    service.suggestions.refresh()
    result = service.store.get("suggestions")
    assert result["state"] == "fallback" and "실제 노트" in result["items"][0]["question"]
    assert "secret" not in json.dumps(result)
    service.suggestions.refresh()
    assert len(calls) == 1


def test_source_changed_during_generation_is_not_published(service, monkeypatch):
    file = write(service, "변경.md", "이전 판단 근거")
    service.indexer.reconcile()
    service.generator.enabled = True

    def request(question, evidence, task):
        file.write_text("다른 판단으로 변경")
        return questions(evidence)

    monkeypatch.setattr(service.generator, "request", request)
    service.suggestions.reset()
    service.suggestions.refresh()
    assert service.store.get("suggestions")["state"] == "empty"
    assert not service.store.get("suggestions")["items"]


def test_existing_vault_recovers_interrupted_generation_on_start(service, monkeypatch):
    write(service, "기록.md", "다시 읽을 본문")
    monkeypatch.setattr(service, "watch", lambda: None)
    service.store.put("suggestions", {"state": "generating", "items": []})
    service.start()
    wait_until(lambda: service.store.get("suggestions")["state"] == "fallback")
    assert service.store.get("suggestions")["items"][0]["title"] == "기록"


def test_external_suggestions_require_separate_opt_in_and_upgrade_local_cache(service, monkeypatch):
    write(service, "개인 기록.md", "외부에 보내지 않을 본문")
    service.indexer.reconcile()
    service.config.external_generation = True
    service.config.external_suggestions = False
    service.generator.enabled = True
    calls = []

    def request(question, evidence, task):
        calls.append(evidence)
        return questions(evidence)

    monkeypatch.setattr(service.generator, "request", request)
    service.suggestions.reset()
    service.suggestions.refresh()
    assert not calls and service.generator.enabled
    assert service.store.get("suggestions")["state"] == "fallback"
    service.config.external_suggestions = True
    monkeypatch.setattr(service, "watch", lambda: None)
    service.start()
    wait_until(lambda: service.store.get("suggestions")["state"] == "ready")
    assert len(calls) == 1
