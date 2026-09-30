"""A single background queue: event debounce, idle reconciliation and recovery."""

import sys
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from app.files import excluded, read_stable
from app.indexer import Indexer
from app.markdown import digest
from app.models import Embedder, Generator
from app.ontology import Ontology
from app.search import Search
from app.storage import Store
from app.suggestions import Suggestions


class Events(FileSystemEventHandler):
    def __init__(self, service):
        self.service = service

    def on_any_event(self, event):
        if event.event_type not in {"created", "modified", "deleted", "moved"}:
            return
        service = self.service
        root = service.indexer.root
        if not root:
            return
        if event.is_directory:
            if event.event_type != "modified":
                service.request_scan(delay=service.config.debounce)
            return
        paths = []
        for raw in (event.src_path, event.dest_path):
            if not raw:
                continue
            try:
                relative = str(Path(raw).relative_to(root))
            except ValueError:
                continue
            if relative.lower().endswith(".md") and not excluded(
                relative, service.store.get("excludes", [])
            ):
                paths.append(relative)
        if paths:
            service.indexer.invalidate(paths)
            with service.condition:
                for path in paths:
                    service.pending[path] = time.monotonic() + service.config.debounce
                service.condition.notify_all()


def observer_for(service):
    if sys.platform != "darwin":
        return Observer()
    from watchdog.observers.api import BaseObserver
    from watchdog.observers.fsevents import FSEventsEmitter

    class RescanEmitter(FSEventsEmitter):
        def events_callback(self, paths, inodes, flags, ids):
            # FSEvents MustScanSubDirs / UserDropped / KernelDropped bits.
            if any(flag & 0x7 for flag in flags):
                service.indexer.invalidate()
                service.request_scan(deep=True, delay=service.config.debounce)
            super().events_callback(paths, inodes, flags, ids)

    return BaseObserver(RescanEmitter)


class Service:
    def __init__(self, config, embedder=None):
        self.config = config
        config.validate_storage()
        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(config.data_dir / "index.sqlite3", config.embedding_dim)
        if self.store.get("index_format") != 1:
            with self.store.transaction() as db:
                db.execute("UPDATE notes SET hash=NULL,state='pending'")
            self.store.put("index_format", 1)
        self.embedder = embedder or Embedder(config)
        self.generator = Generator(config)
        self.indexer = Indexer(self.store, self.embedder, config)
        self.ontology = Ontology(self.store)
        self.search = Search(self.store, self.embedder, self.ontology, self.generator, self.indexer)
        self.suggestions = Suggestions(self.store, self.search, self.generator)
        self.condition = threading.Condition()
        self.operation = threading.RLock()
        self.pending, self.scan_due, self.deep = {}, None, False
        self.prepare_model = False
        self.stopping = False
        self.observer = None
        self.thread = None
        self.error = None
        self.task = None
        self.last_reconcile = time.monotonic()
        self.config.debounce = self.store.get("debounce", config.debounce)
        self.config.reconcile_seconds = self.store.get(
            "reconcile_seconds", config.reconcile_seconds
        )

    def start(self):
        if self.indexer.root:
            cached = self.store.get("suggestions", {})
            if cached.get("state") in {None, "generating"} or (
                cached.get("model_enabled") is False and self.suggestions.model_enabled
            ):
                self.suggestions.reset()
            self.watch()
            self.request_scan()
        self.thread = threading.Thread(target=self.run, daemon=True, name="obsi-indexer")
        self.thread.start()

    def watch(self):
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=3)
            self.observer = None
        if self.indexer.root and self.indexer.root.is_dir():
            self.observer = observer_for(self)
            self.observer.schedule(Events(self), str(self.indexer.root), recursive=True)
            self.observer.start()

    def register(self, path, excludes, daily_pattern):
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("존재하는 볼트 폴더 경로를 입력하세요.")
        self.config.validate_storage(root)
        if self.indexer.root and self.indexer.root != root:
            raise ValueError("다른 볼트를 연결하려면 기존 로컬 색인을 먼저 삭제하세요.")
        if any(Path(rule).is_absolute() or ".." in Path(rule).parts for rule in excludes):
            raise ValueError("제외 경로는 볼트 안의 상대 경로 또는 패턴이어야 합니다.")
        with self.operation:
            previous_pattern = self.store.get("daily_pattern", r"^\d{4}-\d{2}-\d{2}$")
            changed = (
                self.store.get("vault") != str(root)
                or set(self.store.get("excludes", [])) != set(excludes)
                or previous_pattern != daily_pattern
            )
            if previous_pattern != daily_pattern:
                with self.store.transaction() as db:
                    db.execute("UPDATE notes SET hash=NULL,state='pending'")
            self.store.put("vault", str(root))
            self.store.put("excludes", excludes)
            self.store.put("daily_pattern", daily_pattern)
            if changed or not self.store.get("suggestions"):
                self.suggestions.reset()
            self.indexer.invalidate()
            self.watch()
            self.request_scan()

    def request_scan(self, deep=False, delay=0):
        with self.condition:
            self.scan_due = time.monotonic() + delay
            self.deep = self.deep or deep
            self.condition.notify_all()

    def run(self):
        last_wall = time.time()
        while True:
            with self.condition:
                if self.stopping:
                    return
                current = time.monotonic()
                wall = time.time()
                if wall - last_wall > 60:  # Sleep/long suspension: check metadata after resuming.
                    self.scan_due = current
                last_wall = wall
                if self.observer and not all(e.is_alive() for e in self.observer.emitters):
                    self.error = "파일 변경 감시가 중단되었습니다. 메타데이터를 다시 확인합니다."
                    self.scan_due = current
                    self.watch()
                periodic = current - self.last_reconcile >= self.config.reconcile_seconds
                due = {p for p, when in self.pending.items() if when <= current}
                full = periodic or (self.scan_due is not None and self.scan_due <= current)
                prepare = self.prepare_model
                if not (due or full or prepare):
                    deadlines = [
                        30,
                        max(0.01, self.config.reconcile_seconds - (current - self.last_reconcile)),
                    ]
                    if self.pending:
                        deadlines.append(max(0.01, min(self.pending.values()) - current))
                    if self.scan_due is not None:
                        deadlines.append(max(0.01, self.scan_due - current))
                    self.condition.wait(min(deadlines))
                    continue
            # Queries and indexing share one work slot; periodic scans wait while a query runs.
            with self.operation:
                with self.condition:
                    if self.stopping:
                        return
                    deep = self.deep if full else False
                    if full:
                        self.scan_due, self.deep = None, False
                    for path in due:
                        self.pending.pop(path, None)
                    self.prepare_model = False
                try:
                    self.error = None
                    if prepare:
                        self.task = "로컬 모델 준비 중"
                        self.embedder.prepare()
                        full = True
                    self.task = "정밀 검사 중" if deep else "색인 갱신 중"
                    self.indexer.reconcile(paths=None if full else due, deep=deep)
                    self.ontology.refresh()
                    if full and self.store.get("suggestions", {}).get("state") == "pending":
                        self.task = "예상 질문 생성 중"
                        self.suggestions.refresh()
                    if full:
                        self.last_reconcile = time.monotonic()
                    retry = self.store.rows("SELECT path FROM notes WHERE state!='ready'")
                    if retry:
                        with self.condition:
                            for row in retry:
                                self.pending.setdefault(row["path"], time.monotonic() + 30)
                except Exception as exc:
                    self.error = f"{type(exc).__name__}: {exc}"
                finally:
                    self.task = None

    def status(self):
        notes = self.store.rows(
            "SELECT path,state,error,revision,indexed_at,vector_state FROM notes ORDER BY path"
        )
        quality = self.store.rows(
            "SELECT l.raw,l.status,l.line,n.path FROM links l JOIN sections s ON s.id=l.section_id "
            "JOIN notes n ON n.id=s.note_id "
            "WHERE l.status NOT IN ('resolved','external_unverified') LIMIT 100"
        )
        return {
            "vault": str(self.indexer.root) if self.indexer.root else None,
            "excludes": self.store.get("excludes", []),
            "daily_filename_dates": bool(self.store.get("daily_pattern", r"^\d{4}-\d{2}-\d{2}$")),
            "suggestions": self.store.get("suggestions", {"state": "pending", "items": []}),
            "notes": notes,
            "quality": quality,
            "counts": {
                "notes": len(notes),
                "ready": sum(n["state"] == "ready" for n in notes),
                "sections": self.store.rows("SELECT count(*) n FROM sections")[0]["n"],
                "links": self.store.rows("SELECT count(*) n FROM links WHERE status='resolved'")[0][
                    "n"
                ],
            },
            "task": self.task,
            "error": self.error,
            "scan_errors": self.indexer.errors,
            "last_scan": self.store.get("last_scan"),
            "metrics": self.indexer.metrics,
            "debounce": self.config.debounce,
            "reconcile_seconds": self.config.reconcile_seconds,
            "embedding_ready": self.embedder.ready,
            "embedding_model": self.config.embedding_model,
            "embedding_external": self.config.embedding_provider == "external",
            "generation_enabled": self.generator.enabled,
            "generation_model": self.config.generation_model,
            "generation_external": self.config.external_generation,
            "suggestions_external": self.config.external_suggestions,
            "watching": bool(
                self.observer
                and self.observer.is_alive()
                and all(e.is_alive() for e in self.observer.emitters)
            ),
            "data_dir": str(self.config.data_dir),
            "validation": self.ontology.validation,
        }

    def review(self, cid, accept):
        with self.operation:
            rows = self.store.rows(
                "SELECT c.*,s.note_id,n.state,n.revision current_revision,n.path,n.hash note_hash "
                "FROM candidates c JOIN sections s ON s.id=c.section_id "
                "JOIN notes n ON n.id=s.note_id WHERE c.id=?",
                (cid,),
            )
            if (
                not rows
                or rows[0]["state"] != "ready"
                or rows[0]["revision"] != rows[0]["current_revision"]
            ):
                raise ValueError("근거가 변경되었습니다. 후보를 다시 생성하세요.")
            raw, _ = read_stable(self.indexer.root, rows[0]["path"])
            if digest(raw) != rows[0]["note_hash"]:
                self.indexer.invalidate([rows[0]["path"]])
                raise ValueError("원문이 변경되었습니다. 색인이 갱신된 뒤 다시 확인하세요.")
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE candidates SET status=? WHERE id=?",
                    ("accepted" if accept else "rejected", cid),
                )
                self.store.put("generation", self.store.get("generation", 0) + 1)
            self.ontology.refresh()

    def clear(self):
        with self.operation:
            if self.observer:
                self.observer.stop()
                self.observer.join(timeout=3)
                self.observer = None
            with self.condition:
                self.pending.clear()
                self.scan_due = None
                self.deep = False
            self.store.erase()
            self.ontology.version = -1
            self.ontology.refresh()
            self.indexer.errors = []

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=3)
        if self.thread:
            self.thread.join()
        self.store.close()
