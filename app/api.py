"""Loopback-only API and a bundled browser UI; no arbitrary SQL or tools from notes."""

import json
import secrets
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import ROOT, Config
from app.folder_picker import choose_vault_folder
from app.graph_view import graph_view
from app.markdown import iso_date
from app.service import Service


class VaultInput(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    excludes: list[str] = Field(default_factory=list, max_length=100)
    daily_filename_dates: bool = True


class FolderInput(BaseModel):
    initial_path: str = Field(default="", max_length=4096)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=3000)
    domain: Literal["all", "work", "investment", "personal"] = "all"
    start: str | None = None
    end: str | None = None
    mode: Literal["lexical", "semantic", "hybrid"] = "hybrid"
    generate: bool = True

    @model_validator(mode="after")
    def dates(self):
        if any(value and not iso_date(value) for value in (self.start, self.end)):
            raise ValueError("날짜는 YYYY-MM-DD로 입력하세요.")
        if self.start and self.end and self.start > self.end:
            raise ValueError("시작일이 종료일보다 늦습니다.")
        return self


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
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse(
                {"detail": "다른 사이트에서는 로컬 데이터에 접근할 수 없습니다."}, 403
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not secrets.compare_digest(request.headers.get("x-obsi-token", ""), token):
                return JSONResponse({"detail": "이 앱 화면에서 다시 요청하세요."}, 403)
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
    def graph(q: str = Query(default="", max_length=200)):
        with svc().operation:
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
        with svc().operation:
            if not svc().indexer.root:
                raise ValueError("먼저 볼트를 연결하세요.")
            svc().task = "근거 조회 중"
            try:
                return svc().search.ask(**data.model_dump())
            finally:
                svc().task = None

    @app.get("/api/runs")
    def runs():
        return svc().store.rows(
            "SELECT id,created_at,question FROM runs ORDER BY created_at DESC LIMIT 50"
        )

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        rows = svc().store.rows("SELECT payload FROM runs WHERE id=?", (run_id,))
        if not rows:
            raise HTTPException(404, "질문 기록이 없습니다.")
        return json.loads(rows[0]["payload"])

    @app.post("/api/runs/{run_id}/extract")
    def extract(run_id: str):
        with svc().operation:
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

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    return app
