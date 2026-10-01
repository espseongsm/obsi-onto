"""One SQLite publication boundary for text, vectors, provenance and run snapshots."""

import fcntl
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager

import sqlite_vec

from app.markdown import searchable_body

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes(
 id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, signature TEXT, identity TEXT,
 hash TEXT, revision INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'pending',
 error TEXT, title TEXT, aliases TEXT NOT NULL DEFAULT '[]', metadata TEXT NOT NULL DEFAULT '{}',
 record_date TEXT, date_source TEXT, indexed_at TEXT, vector_state TEXT DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS sections(
 id INTEGER PRIMARY KEY AUTOINCREMENT, note_id TEXT REFERENCES notes(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL, heading TEXT, start INTEGER, end INTEGER, text TEXT, hash TEXT,
 tags TEXT, domains TEXT, embedding_key TEXT
);
CREATE INDEX IF NOT EXISTS section_note ON sections(note_id);
CREATE VIRTUAL TABLE IF NOT EXISTS words USING fts5(tokens);
CREATE TABLE IF NOT EXISTS embeddings(key TEXT PRIMARY KEY, model TEXT, vector BLOB);
CREATE TABLE IF NOT EXISTS links(
 id INTEGER PRIMARY KEY, section_id INTEGER REFERENCES sections(id) ON DELETE CASCADE,
 raw TEXT, target TEXT, fragment TEXT, kind TEXT, line INTEGER,
 status TEXT, target_note TEXT, target_section INTEGER
);
CREATE TABLE IF NOT EXISTS candidates(
 id INTEGER PRIMARY KEY, section_id INTEGER REFERENCES sections(id) ON DELETE CASCADE,
 revision INTEGER, kind TEXT, topic TEXT, quote TEXT, event_date TEXT, activity_state TEXT,
 relation TEXT DEFAULT 'about', status TEXT DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY, created_at TEXT, question TEXT, payload TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path, dimensions):
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
        lock_fd = os.open(str(path) + ".lock", flags, 0o600)
        os.fchmod(lock_fd, 0o600)
        self.instance_lock = os.fdopen(lock_fd, "a")
        try:
            fcntl.flock(self.instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.instance_lock.close()
            raise ValueError("Another app instance is already using this index folder.") from exc
        self.dimensions = dimensions
        self.lock = threading.RLock()
        descriptor = os.open(path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.create_function("searchable_body", 1, searchable_body, deterministic=True)
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self.db.executescript("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;" + SCHEMA)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(notes)")}
        for name, definition in (
            ("vector_attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("vector_retry_at", "REAL NOT NULL DEFAULT 0"),
        ):
            if name not in columns:
                self.db.execute(f"ALTER TABLE notes ADD COLUMN {name} {definition}")
        previous_dim = self.get("vector_dimensions")
        if previous_dim is not None and previous_dim != dimensions:
            self.db.execute("DROP TABLE IF EXISTS vectors")
            self.db.execute("UPDATE notes SET vector_state='pending'")
        self.db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vectors USING vec0("
            f"embedding float[{int(dimensions)}] distance_metric=cosine)"
        )
        self.db.commit()
        self.put("vector_dimensions", dimensions)
        self.put("schema_version", 2)

    @contextmanager
    def transaction(self):
        with self.lock:
            nested = self.db.in_transaction
            name = "nested_" + uuid.uuid4().hex
            self.db.execute(f"SAVEPOINT {name}" if nested else "BEGIN")
            try:
                yield self.db
            except BaseException:
                if nested:
                    self.db.execute(f"ROLLBACK TO {name}")
                    self.db.execute(f"RELEASE {name}")
                else:
                    self.db.rollback()
                raise
            else:
                if nested:
                    self.db.execute(f"RELEASE {name}")
                else:
                    self.db.commit()

    def rows(self, sql, params=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, params)]

    def get(self, key, default=None):
        rows = self.rows("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def put(self, key, value):
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES (?,?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def remove_sections(self, db, note_id):
        for row in db.execute("SELECT id FROM sections WHERE note_id=?", (note_id,)).fetchall():
            db.execute("DELETE FROM words WHERE rowid=?", (row[0],))
            db.execute("DELETE FROM vectors WHERE rowid=?", (row[0],))
        db.execute("DELETE FROM sections WHERE note_id=?", (note_id,))

    def erase(self):
        with self.transaction() as db:
            for table in (
                "query_events",
                "query_jobs",
                "candidates",
                "links",
                "words",
                "vectors",
                "sections",
                "notes",
                "embeddings",
                "runs",
                "settings",
            ):
                db.execute(f"DELETE FROM {table}")
        with self.lock:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.db.execute("VACUUM")
        self.put("vector_dimensions", self.dimensions)
        self.put("schema_version", 2)

    def close(self):
        with self.lock:
            self.db.close()
            self.instance_lock.close()
