"""Unavailable generation must leave local retrieval and its evidence graph usable."""

import json

import httpx
import pytest

from app.config import Config
from app.models import Generator
from app.service import Service
from tests.conftest import FakeEmbedder, write


@pytest.mark.parametrize(
    ("url", "model", "external", "key", "reason"),
    [
        ("", "", False, "", "URL and model"),
        ("http://localhost:11434/v1", "", False, "", "URL and model"),
        ("https://example.test/v1", "test-model", False, "test-key", "transmission"),
        ("https://example.test/v1", "test-model", True, "", "API key"),
        ("http://example.test/v1", "test-model", True, "test-key", "URL"),
        ("https:///v1", "test-model", True, "test-key", "URL"),
        ("https://example.test:bad/v1", "test-model", True, "test-key", "URL"),
        ("http://[bad/v1", "test-model", False, "", "URL"),
    ],
)
def test_unavailable_llm_starts_and_searches_without_model_calls(
    tmp_path, monkeypatch, url, model, external, key, reason
):
    monkeypatch.setenv("OBSI_LLM_API_KEY", key)
    monkeypatch.setattr(
        "app.models.httpx.post", lambda *a, **kw: pytest.fail("LLM must not be called")
    )
    service = Service(
        Config(
            data_dir=tmp_path / "data",
            generation_url=url,
            generation_model=model,
            external_generation=external,
        ),
        FakeEmbedder(),
    )
    try:
        status = service.status()
        assert not status["generation_enabled"] and reason in status["generation_reason"]
        with pytest.raises(ValueError, match="Connect a vault"):
            service.jobs.submit({"question": "프로젝트"})
        root = tmp_path / "vault"
        root.mkdir()
        service.store.put("vault", str(root))
        write(service, "프로젝트.md", "프로젝트 도입 근거는 [[검토]]에 정리했다.")
        write(service, "검토.md", "프로젝트 검색 기능을 검토했다.")
        service.indexer.reconcile()
        result = service.jobs.ask({"question": "프로젝트", "generate": True})
        assert not result["generated"] and len(result["evidence"]) == 2
        assert result["graph"]["nodes"] and result["graph"]["edges"]
        assert status["generation_reason"] in result["warnings"]
        assert {sentence["text"] for sentence in result["sentences"]} == {
            item["text"] for item in result["evidence"]
        }
    finally:
        service.close()


def test_local_llm_without_api_key_is_supported(monkeypatch):
    monkeypatch.delenv("OBSI_LLM_API_KEY", raising=False)
    generator = Generator(
        Config(generation_url="http://127.0.0.1:11434/v1", generation_model="test-model")
    )

    def post(url, **options):
        assert options["headers"]["Authorization"] == "Bearer "
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"choices": [{"message": {"content": '{"sentences": []}'}}]},
        )

    monkeypatch.setattr("app.models.httpx.post", post)
    assert generator.enabled and generator.unavailable_reason is None
    assert generator.request("질문", []) == {"sentences": []}


@pytest.mark.parametrize("failure", ["authentication", "network", "invalid_response"])
@pytest.mark.parametrize("source_count", [1, 2])
def test_runtime_model_failure_preserves_search_and_stops_retries(
    service, monkeypatch, failure, source_count
):
    service.config.generation_url = "https://example.test/v1"
    service.config.generation_model = "test-model"
    service.config.external_generation = True
    monkeypatch.setenv("OBSI_LLM_API_KEY", "dummy-private-key")
    service.generator = Generator(service.config)
    calls = []

    def post(url, **options):
        calls.append(json.loads(options["json"]["messages"][1]["content"])["task"])
        if failure == "network":
            raise httpx.ConnectError("dummy-private-key https://private-address.test")
        if failure == "invalid_response":
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": []})
        return httpx.Response(
            401,
            request=httpx.Request("POST", url),
            text="dummy-private-key https://private-address.test",
        )

    monkeypatch.setattr("app.models.httpx.post", post)
    write(service, "프로젝트.md", "프로젝트 도입 근거를 검토한다. #업무")
    if source_count == 2:
        write(service, "검토.md", "프로젝트 검색 기능을 확인한다. #업무")
    service.indexer.reconcile()
    for _ in range(2):
        result = service.jobs.ask({"question": "프로젝트", "generate": True})
        assert not result["generated"] and len(result["evidence"]) == source_count
        assert result["graph"]["nodes"] and result["graph"]["edges"]
        assert {sentence["text"] for sentence in result["sentences"]} == {
            item["text"] for item in result["evidence"]
        }
    assert calls == ["conflicts" if source_count == 2 else "answer"]
    status = service.status()
    assert not status["generation_enabled"] and "restart" in status["generation_reason"]
    public = json.dumps([status, result], ensure_ascii=False)
    assert "dummy-private-key" not in public and "private-address.test" not in public
