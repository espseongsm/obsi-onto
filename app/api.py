"""Loopback-only API and a bundled browser UI; no arbitrary SQL or tools from notes."""

import json
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import ROOT, Config
from app.folder_picker import choose_vault_folder
from app.graph_overview import graph_overview
from app.graph_view import graph_view
from app.job_api import install_job_routes
from app.query_contracts import Question
from app.service import Service


class VaultInput(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    excludes: list[str] = Field(default_factory=list, max_length=100)
    daily_filename_dates: bool = True


class FolderInput(BaseModel):
    initial_path: str = Field(default="", max_length=4096)


class ScanInput(BaseModel):
    deep: bool = False


class ReviewInput(BaseModel):
    accept: bool


class Tuning(BaseModel):
    debounce: float = Field(ge=0.5, le=30)
    reconcile_seconds: float = Field(ge=300, le=86400)


def create_app(config=None, service=None):
    config = config or Config()
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        app.state.service = service or Service(config)
        app.state.service.start()
        yield
        app.state.service.close()

    app = FastAPI(title="Obsi Onto", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin and origin != expected_origin:
            return JSONResponse(
                {"detail": "Other websites cannot access this app's local data."}, 403
            )
        private_api = request.url.path.startswith("/api/") and request.url.path != "/api/session"
        if request.url.path.startswith("/api/") and request.headers.get("sec-fetch-site") in {
            "cross-site",
            "same-site",
        }:
            return JSONResponse({"detail": "Send this request from the app."}, 403)
        if private_api or request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not secrets.compare_digest(request.headers.get("x-obsi-token", ""), token):
                return JSONResponse({"detail": "Send this request again from the app."}, 403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 65_536:
                    return JSONResponse({"detail": "Requests must be no larger than 64 KiB."}, 413)
                body.extend(chunk)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, 400)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "The task or record was deleted."}, 404)

    def svc():
        return app.state.service

    @app.get("/")
    def home():
        return FileResponse(ROOT / "web/index.html")

    @app.get("/api/session")
    def session():
        return {"token": token}

    @app.get("/api/status")
    def status():
        return svc().status()

    @app.get("/api/graph")
    def graph(q: str = Query(default="", max_length=200), overview: bool = False):
        with svc().operation:
            if overview and not q.strip():
                return graph_overview(svc().ontology, svc().indexer.root)
            return graph_view(svc().ontology, svc().indexer.root, query=q)

    @app.post("/api/vault")
    def vault(data: VaultInput):
        svc().register(
            data.path, data.excludes, r"^\d{4}-\d{2}-\d{2}$" if data.daily_filename_dates else ""
        )
        return {"queued": True}

    @app.post("/api/vault/pick-folder")
    def pick_folder(data: FolderInput):
        return {"path": choose_vault_folder(data.initial_path or svc().store.get("vault", ""))}

    @app.post("/api/scan")
    def scan(data: ScanInput):
        svc().request_scan(deep=data.deep)
        return {"queued": True}

    @app.post("/api/demo")
    def demo():
        svc().register(str(ROOT / "examples/vault"), [], r"^\d{4}-\d{2}-\d{2}$")
        return {"queued": True}

    @app.post("/api/models/prepare")
    def prepare():
        with svc().condition:
            svc().prepare_model = True
            svc().condition.notify_all()
        return {"queued": True}

    @app.post("/api/settings")
    def tune(data: Tuning):
        for key, value in data.model_dump().items():
            setattr(svc().config, key, value)
            svc().store.put(key, value)
        return {"saved": True}

    @app.post("/api/ask")
    def ask(data: Question):
        result = svc().jobs.ask(data.model_dump())
        return JSONResponse(result, status_code=202 if "state" in result else 200)

    @app.get("/api/runs")
    def runs():
        return svc().store.rows(
            "SELECT id,created_at,question FROM runs ORDER BY created_at DESC LIMIT 50"
        )

    @app.get("/api/conversations")
    def conversations():
        from app.conversations import recent

        return recent(svc().store)

    @app.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: str):
        from app.conversations import turns

        if len(conversation_id) != 32 or any(c not in "0123456789abcdef" for c in conversation_id):
            raise HTTPException(404, "Conversation not found.")
        items = turns(svc().store, conversation_id)
        if not items:
            raise HTTPException(404, "Conversation not found.")
        return items

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        rows = svc().store.rows("SELECT payload FROM runs WHERE id=?", (run_id,))
        if not rows:
            raise HTTPException(404, "Question history not found.")
        return json.loads(rows[0]["payload"])

    @app.post("/api/runs/{run_id}/extract")
    def extract(run_id: str):
        return svc().search.extract(run_id)

    @app.get("/api/candidates")
    def candidates():
        return svc().store.rows(
            "SELECT c.*,n.path,s.start,s.end FROM candidates c "
            "JOIN sections s ON s.id=c.section_id JOIN notes n ON n.id=s.note_id "
            "ORDER BY c.id DESC LIMIT 100"
        )

    @app.post("/api/candidates/{candidate_id}/review")
    def review(candidate_id: int, data: ReviewInput):
        svc().review(candidate_id, data.accept)
        return {"saved": True}

    @app.delete("/api/data")
    def clear():
        svc().clear()
        return {"deleted": True}

    install_job_routes(app, svc)
    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    return app
