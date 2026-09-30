"""Incremental indexing: expensive reads only for changed candidates."""

import json
import resource
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.files import excluded, read_stable, safe_path, scan, signature
from app.markdown import digest, parse_markdown, search_tokens
from app.ontology import resolve_links


def now():
    return datetime.now(timezone.utc).isoformat()


class Indexer:
    def __init__(self, store, embedder, config):
        self.store, self.embedder, self.config = store, embedder, config
        self.metrics = {"body_reads": 0, "files_checked": 0, "seconds": 0, "cpu_seconds": 0}
        self.errors = []
        self.busy = False

    @property
    def root(self):
        value = self.store.get("vault")
        return Path(value) if value else None

    def invalidate(self, paths=None):
        with self.store.transaction() as db:
            if paths is None:
                db.execute("UPDATE notes SET state='pending' WHERE state='ready'")
            else:
                db.executemany(
                    "UPDATE notes SET state='pending' WHERE path=?", [(p,) for p in paths]
                )
            self.store.put("generation", self.store.get("generation", 0) + 1)

    def reconcile(self, paths=None, deep=False):
        root = self.root
        if not root:
            return
        start = time.perf_counter()
        cpu = time.process_time()
        usage = resource.getrusage(resource.RUSAGE_SELF)
        self.metrics = {"body_reads": 0, "embedding_calls": 0, "files_checked": 0}
        calls = self.embedder.calls
        self.busy = True
        try:
            found, errors = self._metadata(root, paths)
            self.errors = errors
            if errors:
                # Incomplete directory enumeration cannot prove that an old file was deleted.
                self.invalidate()
            self.metrics["files_checked"] = len(found)
            rows = self.store.rows("SELECT * FROM notes")
            by_path = {row["path"]: row for row in rows}
            identities = {}
            for row in rows:
                identities.setdefault(row["identity"], []).append(row)
            consumed = set()
            publication_changed = False
            for path, meta in found.items():
                old = by_path.get(path)
                if old is None:
                    matches = [
                        n
                        for n in identities.get(meta["identity"], [])
                        if n["path"] not in found
                        and n["id"] not in consumed
                        and not (root / n["path"]).exists()
                    ]
                    if meta["identity"] and len(matches) == 1:
                        old = matches[0]
                note_id = old["id"] if old else uuid.uuid4().hex
                consumed.add(note_id)
                moved = old is not None and old["path"] != path
                changed = (
                    not old
                    or old["signature"] != meta["signature"]
                    or moved
                    or deep
                    or old["state"] != "ready"
                    or paths is not None
                    or (old["vector_state"] != self.embedder.key and self.embedder.ready)
                )
                if not changed and meta["available"]:
                    continue
                publication_changed = True
                with self.store.transaction() as db:
                    db.execute(
                        "INSERT INTO notes(id,path,identity) VALUES(?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET path=excluded.path,"
                        "identity=excluded.identity,state='pending'",
                        (note_id, path, meta["identity"]),
                    )
                if not meta["available"]:
                    self._error(note_id, "다운로드 대기: iCloud 파일을 다운로드 유지로 설정하세요.")
                    continue
                try:
                    self._index(root, path, note_id, old, moved)
                except (OSError, ValueError, UnicodeError) as exc:
                    self._error(note_id, str(exc))
            for old in rows:
                if errors:
                    continue
                if old["id"] in consumed or old["path"] in found:
                    continue
                if paths is not None and old["path"] not in paths:
                    continue
                # A second existence check after the debounce avoids atomic-save deletion races.
                if (root / old["path"]).exists() and not excluded(
                    old["path"], self.store.get("excludes", [])
                ):
                    continue
                with self.store.transaction() as db:
                    self.store.remove_sections(db, old["id"])
                    db.execute("DELETE FROM notes WHERE id=?", (old["id"],))
                publication_changed = True
            with self.store.transaction():
                if publication_changed:
                    resolve_links(self.store)
                    self.store.put("generation", self.store.get("generation", 0) + 1)
                self.store.put("last_scan", now())
        finally:
            self.busy = False
            after = resource.getrusage(resource.RUSAGE_SELF)
            self.metrics.update(
                seconds=round(time.perf_counter() - start, 4),
                cpu_seconds=round(time.process_time() - cpu, 4),
                embedding_calls=self.embedder.calls - calls,
                disk_input_blocks=after.ru_inblock - usage.ru_inblock,
            )

    def _metadata(self, root, paths):
        rules = self.store.get("excludes", [])
        if paths is None:
            return scan(root, rules)
        if not root.is_dir():
            return {}, ["볼트에 접근할 수 없습니다."]
        found, errors = {}, []
        for path in paths:
            if excluded(path, rules):
                continue
            try:
                file = safe_path(root, path)
                st = file.stat()
                found[path] = {
                    "signature": signature(st),
                    "identity": f"{st.st_dev}:{st.st_ino}",
                    "available": not bool(getattr(st, "st_flags", 0) & 0x40000000),
                }
            except FileNotFoundError:
                placeholder = root / Path(path).parent / ("." + Path(path).name + ".icloud")
                if placeholder.exists():
                    found[path] = {"signature": "icloud", "identity": "", "available": False}
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
        return found, errors

    def _error(self, note_id, message):
        with self.store.transaction() as db:
            db.execute("UPDATE notes SET state='error',error=? WHERE id=?", (message, note_id))

    def _index(self, root, path, note_id, old, moved):
        text, sig = read_stable(root, path)
        self.metrics["body_reads"] += 1
        source_hash = digest(text)
        if (
            old
            and old["hash"] == source_hash
            and not moved
            and (not self.embedder.ready or old["vector_state"] == self.embedder.key)
        ):
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE notes SET signature=?,state='ready',error=NULL WHERE id=?",
                    (sig, note_id),
                )
            return
        parsed = parse_markdown(text, path, self.store.get("daily_pattern", r"^\d{4}-\d{2}-\d{2}$"))
        revision = (old["revision"] if old else 0) + 1
        vectors, vector_error = {}, None
        if self.embedder.ready:
            try:
                vectors = self._vectors(parsed)
            except Exception as exc:
                vector_error = f"의미 색인 대기: {type(exc).__name__}"
        # Re-read only this changed source, after any slow embedding work, before publishing.
        latest, latest_sig = read_stable(root, path)
        self.metrics["body_reads"] += 1
        if digest(latest) != source_hash:
            raise OSError("처리 중 노트가 다시 바뀌었습니다. 최신 버전을 다시 색인합니다.")
        preserved = self._unchanged_reviews(note_id, parsed, old)
        with self.store.transaction() as db:
            self.store.remove_sections(db, note_id)
            for section in parsed["sections"]:
                key = section.get("embedding_key")
                sid = db.execute(
                    "INSERT INTO sections(note_id,revision,heading,start,end,text,hash,"
                    "tags,domains,"
                    "embedding_key) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        note_id,
                        revision,
                        section["heading"],
                        section["start"],
                        section["end"],
                        section["text"],
                        section["hash"],
                        json.dumps(section["tags"]),
                        json.dumps(section["domains"]),
                        key,
                    ),
                ).lastrowid
                for candidate in preserved.get((section["heading"], section["hash"]), []):
                    db.execute(
                        "INSERT INTO candidates(id,section_id,revision,kind,topic,quote,event_date,"
                        "activity_state,relation,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            candidate["id"],
                            sid,
                            revision,
                            candidate["kind"],
                            candidate["topic"],
                            candidate["quote"],
                            candidate["event_date"],
                            candidate["activity_state"],
                            candidate["relation"],
                            candidate["status"],
                        ),
                    )
                searchable = " ".join(
                    [
                        parsed["title"],
                        *parsed["aliases"],
                        section["heading"],
                        *section["tags"],
                        section["text"],
                    ]
                )
                db.execute(
                    "INSERT INTO words(rowid,tokens) VALUES(?,?)", (sid, search_tokens(searchable))
                )
                if key in vectors:
                    db.execute(
                        "INSERT INTO vectors(rowid,embedding) VALUES(?,?)", (sid, vectors[key])
                    )
                for link in section["links"]:
                    db.execute(
                        "INSERT INTO links(section_id,raw,target,fragment,kind,line) "
                        "VALUES(?,?,?,?,?,?)",
                        (
                            sid,
                            link["raw"],
                            link["target"],
                            link["fragment"],
                            link["kind"],
                            link["line"],
                        ),
                    )
            db.execute(
                "UPDATE notes SET signature=?,hash=?,revision=?,state='ready',error=?,"
                "title=?,aliases=?,metadata=?,record_date=?,date_source=?,indexed_at=?,"
                "vector_state=? WHERE id=?",
                (
                    latest_sig,
                    source_hash,
                    revision,
                    vector_error,
                    parsed["title"],
                    json.dumps(parsed["aliases"]),
                    json.dumps(parsed["metadata"], default=str),
                    parsed["record_date"],
                    parsed["date_source"],
                    now(),
                    self.embedder.key if self.embedder.ready and not vector_error else "pending",
                    note_id,
                ),
            )

    def _unchanged_reviews(self, note_id, parsed, old):
        if (
            not old
            or old["title"] != parsed["title"]
            or old["record_date"] != parsed["record_date"]
        ):
            return {}
        existing = self.store.rows("SELECT * FROM sections WHERE note_id=?", (note_id,))
        result = {}
        for section in parsed["sections"]:
            key = (section["heading"], section["hash"])
            matches = [s for s in existing if (s["heading"], s["hash"]) == key]
            new_matches = [s for s in parsed["sections"] if (s["heading"], s["hash"]) == key]
            if len(matches) == len(new_matches) == 1:
                result[key] = self.store.rows(
                    "SELECT * FROM candidates WHERE section_id=?", (matches[0]["id"],)
                )
        return result

    def _vectors(self, parsed):
        result, missing = {}, {}
        for section in parsed["sections"]:
            text = f"{parsed['title']}\n{section['heading']}\n{section['text']}"
            key = digest(self.embedder.key + "\n" + text)
            section["embedding_key"] = key
            cached = self.store.rows("SELECT vector FROM embeddings WHERE key=?", (key,))
            if cached:
                result[key] = cached[0]["vector"]
            else:
                missing[key] = text
        if missing:
            embeddings = self.embedder.embed(list(missing.values()))
            with self.store.transaction() as db:
                for key, vector in zip(missing, embeddings, strict=True):
                    db.execute(
                        "INSERT OR REPLACE INTO embeddings VALUES(?,?,?)",
                        (key, self.embedder.key, vector),
                    )
                    result[key] = vector
        return result
