import json
import os

import httpx

import main
from app.config import Config
from app.models import Generator
from app.search import Search


def test_main_loads_project_env_and_preserves_shell_values(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for key in (
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "OBSI_LLM_URL",
        "OBSI_LLM_API_KEY",
        "OBSI_LLM_MODEL",
        "OBSI_ALLOW_EXTERNAL_GENERATION",
    ):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "OPENAI_BASE_URL=https://example.test/openai/v1/\n"
        "OPENAI_API_KEY=dummy-file-key\n"
        "OBSI_LLM_URL=${OPENAI_BASE_URL}\n"
        "OBSI_LLM_API_KEY=${OPENAI_API_KEY}\n"
        "OBSI_LLM_MODEL=gpt-5.6-luna\n"
        "OBSI_ALLOW_EXTERNAL_GENERATION=1\n"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-shell-key")
    monkeypatch.setenv("OBSI_LLM_MODEL", "shell-model")
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main, "create_app", Config)
    monkeypatch.setattr("sys.argv", ["main.py", "--port", "8877"])
    started = {}

    def run(server):
        started.update(
            config=server.config.app,
            host=server.config.host,
            port=server.config.port,
        )

    monkeypatch.setattr(main.LocalAppServer, "run", run)
    main.main()
    config = started["config"]
    assert config.generation_url == "https://example.test/openai/v1/"
    assert config.generation_model == "shell-model"
    assert config.external_generation
    assert os.getenv("OBSI_LLM_API_KEY") == "dummy-shell-key"
    assert started["host"] == "127.0.0.1" and started["port"] == 8877


def test_luna_request_omits_unsupported_temperature_and_keeps_citations(monkeypatch):
    config = Config(
        generation_url="https://example.test/openai/v1/",
        generation_model="gpt-5.6-luna",
        external_generation=True,
    )
    evidence = [{"citation": "S1", "text": "가상 시연일은 2026-10-01이다."}]
    monkeypatch.setenv("OBSI_LLM_API_KEY", "dummy-test-key")

    def post(url, **options):
        payload = options["json"]
        assert url == "https://example.test/openai/v1/chat/completions"
        assert payload["model"] == "gpt-5.6-luna" and "temperature" not in payload
        assert payload["response_format"] == {"type": "json_object"}
        assert json.loads(payload["messages"][1]["content"])["evidence"] == evidence
        assert options["headers"]["Authorization"] == "Bearer dummy-test-key"
        assert not options["follow_redirects"]
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "sentences": [
                                        {"text": "시연일은 2026-10-01입니다.", "citations": ["S1"]}
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr("app.models.httpx.post", post)
    answer = Generator(config).request("시연일은?", evidence)
    assert Search.validate_sentences(answer, evidence)[0]["citations"] == ["S1"]


def test_follow_up_context_is_separate_from_current_evidence(monkeypatch):
    monkeypatch.setenv("OBSI_LLM_API_KEY", "dummy-test-key")
    config = Config(
        generation_url="https://example.test/openai/v1/",
        generation_model="gpt-5.6-luna",
        external_generation=True,
    )
    evidence = [{"citation": "S1", "text": "현재 검증된 문단"}]
    previous = [{"question": "지난 질문", "answer": "지난 답변"}]

    def post(url, **options):
        message = json.loads(options["json"]["messages"][1]["content"])
        assert message["evidence"] == evidence
        assert message["previous_conversation"] == previous
        assert "user_clarification" not in message
        assert "이전 답변은 사실 근거가 아니다" in options["json"]["messages"][0]["content"]
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"sentences": [{"text": "현재 답변", "citations": ["S1"]}]}
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr("app.models.httpx.post", post)
    result = Generator(config).request(
        "이어지는 질문", evidence, "answer", {"conversation": previous}
    )
    assert result["sentences"][0]["citations"] == ["S1"]
