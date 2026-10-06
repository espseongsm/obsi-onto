"""Model response budgets apply while receiving, including compressed bodies."""

import gzip
import json
import struct
import zlib
from contextlib import contextmanager

import httpx
import pytest

from app.config import Config
from app.model_http import post_json
from app.models import Embedder, Generator


class CountedStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.read, self.closed = chunks, 0, False

    def __iter__(self):
        for chunk in self.chunks:
            self.read += 1
            yield chunk

    def close(self):
        self.closed = True


def mock_stream(monkeypatch, chunks, headers=None, status=200):
    body = CountedStream(chunks)
    calls = []

    @contextmanager
    def stream(method, url, **options):
        calls.append((method, url, options))
        response = httpx.Response(
            status, request=httpx.Request(method, url), headers=headers, stream=body
        )
        try:
            yield response
        finally:
            response.close()

    monkeypatch.setattr("app.model_http.httpx.stream", stream)
    return body, calls


@pytest.mark.parametrize("headers", [{}, {"content-length": "2"}])
def test_body_without_trustworthy_length_stops_at_budget(monkeypatch, headers):
    body, _ = mock_stream(monkeypatch, [b"123456", b"123456", b"never read"], headers)
    with pytest.raises(ValueError, match="too large"):
        post_json("https://example.test/v1", max_bytes=10, headers={})
    assert body.read == 2 and body.closed


def test_oversized_declared_length_rejects_before_receiving(monkeypatch):
    body, _ = mock_stream(monkeypatch, [b"never read"], {"content-length": "11"})
    with pytest.raises(ValueError, match="too large"):
        post_json("https://example.test/v1", max_bytes=10, headers={})
    assert body.read == 0 and body.closed


def test_http_error_is_checked_before_reading_body(monkeypatch):
    body, _ = mock_stream(monkeypatch, [b"private response details"], status=401)
    with pytest.raises(httpx.HTTPStatusError):
        post_json("https://example.test/v1", max_bytes=10, headers={})
    assert body.read == 0 and body.closed


@pytest.mark.parametrize("encoding", ["identity", "gzip", "deflate", "raw-deflate"])
def test_valid_response_at_decoded_limit(monkeypatch, encoding):
    payload = json.dumps({"text": "근거"}, ensure_ascii=False).encode()
    encoded = payload
    if encoding == "gzip":
        encoded = gzip.compress(payload)
    elif encoding == "deflate":
        encoded = zlib.compress(payload)
    elif encoding == "raw-deflate":
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        encoded = compressor.compress(payload) + compressor.flush()
    headers = {"content-encoding": "deflate" if encoding == "raw-deflate" else encoding}
    body, calls = mock_stream(monkeypatch, [encoded[:1], encoded[1:]], headers)
    result = post_json(
        "https://example.test/v1",
        max_bytes=len(payload),
        headers={"Authorization": "Bearer dummy-test-key"},
        json={"input": "query"},
        timeout=60,
        follow_redirects=False,
    )
    assert result == {"text": "근거"} and body.closed
    method, _, options = calls[0]
    assert method == "POST" and options["timeout"] == 60
    assert not options["follow_redirects"]
    assert options["headers"]["Authorization"] == "Bearer dummy-test-key"
    assert options["headers"]["Accept-Encoding"] == "gzip, deflate"


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_compressed_bomb_stops_at_decoded_budget(monkeypatch, encoding):
    compress = gzip.compress if encoding == "gzip" else zlib.compress
    encoded = compress(b"x" * 1_000_000)
    body, _ = mock_stream(monkeypatch, [encoded, b"never read"], {"content-encoding": encoding})
    with pytest.raises(ValueError, match="too large"):
        post_json("https://example.test/v1", max_bytes=128, headers={})
    assert body.read == 1 and body.closed


def test_compressed_wire_bytes_are_also_bounded(monkeypatch):
    monkeypatch.setattr("app.model_http.WIRE_OVERHEAD_BYTES", 0)
    body, _ = mock_stream(monkeypatch, [b"x" * 11], {"content-encoding": "gzip"})
    with pytest.raises(ValueError, match="too large"):
        post_json("https://example.test/v1", max_bytes=10, headers={})
    assert body.closed


@pytest.mark.parametrize(
    ("encoding", "payload", "error"),
    [
        ("br", b"never read", "Unsupported"),
        ("gzip", b"invalid", "Invalid"),
        ("gzip", gzip.compress(b"{}")[:-1], "Incomplete"),
        ("gzip", gzip.compress(b"{}") + b"trailing", "Invalid"),
    ],
)
def test_unexpected_or_malformed_encoding_is_closed(monkeypatch, encoding, payload, error):
    body, _ = mock_stream(monkeypatch, [payload], {"content-encoding": encoding})
    with pytest.raises(ValueError, match=error):
        post_json("https://example.test/v1", max_bytes=1_000, headers={})
    assert body.closed
    if encoding == "br":
        assert body.read == 0


def test_generation_oversized_response_preserves_safe_fallback(monkeypatch):
    monkeypatch.setattr("app.models.GENERATION_RESPONSE_BYTES", 64)
    body, _ = mock_stream(monkeypatch, [b"x" * 65, b"never read"])
    generator = Generator(
        Config(generation_url="http://127.0.0.1:11434/v1", generation_model="test-model")
    )
    with pytest.raises(ValueError, match="response format is unsupported"):
        generator.request("question", [])
    assert not generator.enabled and body.read == 1 and body.closed


def test_generation_valid_streamed_response(monkeypatch):
    payload = json.dumps({"choices": [{"message": {"content": '{"sentences": []}'}}]})
    body, calls = mock_stream(monkeypatch, [payload[:10].encode(), payload[10:].encode()])
    generator = Generator(
        Config(generation_url="http://127.0.0.1:11434/v1", generation_model="test-model")
    )
    assert generator.request("question", []) == {"sentences": []}
    assert body.closed and generator.enabled
    assert calls[0][1].endswith("/chat/completions")


def test_external_embedding_budget_and_valid_vector_order(monkeypatch, tmp_path):
    config = Config(
        data_dir=tmp_path,
        embedding_provider="external",
        embedding_dim=2,
        embedding_url="https://example.test/v1",
        external_embedding=True,
    )
    embedder = Embedder(config)
    payload = json.dumps(
        {
            "data": [
                {"index": 1, "embedding": [3, 4]},
                {"index": 0, "embedding": [1, 2]},
            ]
        }
    ).encode()
    body, calls = mock_stream(monkeypatch, [payload])
    monkeypatch.setenv("OBSI_EMBED_API_KEY", "dummy-test-key")
    vectors = embedder.embed(["a", "b"])
    assert [struct.unpack("=ff", vector) for vector in vectors] == [(1, 2), (3, 4)]
    assert body.closed and calls[0][1].endswith("/embeddings")
    assert calls[0][2]["headers"]["Authorization"] == "Bearer dummy-test-key"
    monkeypatch.setattr("app.models.EMBEDDING_RESPONSE_BYTES", 64)
    body, _ = mock_stream(monkeypatch, [b"x" * 65, b"never read"])
    with pytest.raises(ValueError, match="too large"):
        embedder.embed(["a"])
    assert body.read == 1 and body.closed
