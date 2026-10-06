"""Isolated fictional vault for browser tests; never load .env or call a model."""

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from app.api import create_app
from app.config import LOCAL_MODEL, Config
from app.service import Service
from tests.conftest import FakeEmbedder


class OtherWebsite(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<!doctype html><title>Other website</title><p>Untrusted origin fixture</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    with TemporaryDirectory(prefix="obsi-browser-") as directory:
        root = Path(directory).resolve()
        vault = root / "vault"
        vault.mkdir()
        (vault / "Security.md").write_text(
            "# Security fixture\n\nSecurity fixture: "
            '<img src=x onerror="window.__obsiXSS=1">\n\n[[Other]]',
            encoding="utf-8",
        )
        (vault / "Other.md").write_text("# Other\n\nSecurity fixture evidence.", encoding="utf-8")
        config = Config(
            data_dir=root / "data",
            embedding_provider="local",
            embedding_model=LOCAL_MODEL,
            embedding_dim=384,
            embedding_url="",
            external_embedding=False,
            generation_url="",
            generation_model="",
            external_generation=False,
            external_suggestions=False,
        )
        service = Service(config, FakeEmbedder())
        service.store.put("vault", str(vault))
        service.indexer.reconcile()
        service.ontology.refresh()
        old = service.search.ask("Security fixture", mode="lexical", generate=False)
        old["created_at"] = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        with service.store.transaction() as db:
            db.execute(
                "UPDATE runs SET created_at=?,payload=? WHERE id=?",
                (old["created_at"], json.dumps(old), old["id"]),
            )
        other = ThreadingHTTPServer(("127.0.0.1", 8772), OtherWebsite)
        threading.Thread(target=other.serve_forever, daemon=True).start()
        try:
            uvicorn.run(
                create_app(config, service), host="127.0.0.1", port=8771, log_level="warning"
            )
        finally:
            other.shutdown()
            other.server_close()


if __name__ == "__main__":
    main()
