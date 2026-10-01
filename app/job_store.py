"""SQLite job state and bounded replay events; writes share the store transaction."""

import json
import uuid

from app.indexer import now

TERMINAL = {"completed", "cancelled", "failed", "interrupted", "stale"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS query_jobs(
 id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, epoch TEXT NOT NULL,
 state TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS query_events(
 job_id TEXT REFERENCES query_jobs(id) ON DELETE CASCADE,
 seq INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(job_id,seq)
);
CREATE INDEX IF NOT EXISTS job_state ON query_jobs(state);
"""


class JobStore:
    def __init__(self, store):
        self.store = store
        with store.lock:
            store.db.executescript(SCHEMA)
        if not store.get("vault_epoch"):
            store.put("vault_epoch", uuid.uuid4().hex)
        for row in store.rows(
            "SELECT id FROM query_jobs WHERE state NOT IN "
            "('awaiting_user','completed','cancelled','failed','interrupted','stale')"
        ):
            job = self.get(row["id"])
            self.save(job, "interrupted", "Interrupted by a server restart. Please ask again.")

    def get(self, jid):
        rows = self.store.rows("SELECT * FROM query_jobs WHERE id=?", (jid,))
        if not rows:
            raise KeyError(jid)
        row = rows[0]
        return {**row, "payload": json.loads(row["payload"])}

    def create(self, request_id, request, conversation=None):
        rows = self.store.rows("SELECT id FROM query_jobs WHERE request_id=?", (request_id,))
        if rows:
            job = self.get(rows[0]["id"])
            if job["payload"]["request"] != request:
                raise ValueError("A request ID cannot be reused for a different question.")
            return job, False
        if request.get("conversation_id") and self.store.rows(
            "SELECT id FROM query_jobs WHERE epoch=? AND state NOT IN "
            "('completed','cancelled','failed','interrupted','stale') AND "
            "json_extract(payload, '$.request.conversation_id')=? LIMIT 1",
            (self.store.get("vault_epoch"), request["conversation_id"]),
        ):
            raise ValueError("Finish the active question in this conversation first.")
        active = self.store.rows(
            "SELECT count(*) n FROM query_jobs WHERE state NOT IN "
            "('completed','cancelled','failed','interrupted','stale')"
        )[0]["n"]
        if active >= 8:
            raise ValueError(
                "Up to 8 questions can be active or awaiting confirmation. "
                "Finish an existing question first."
            )
        stamp, jid = now(), uuid.uuid4().hex
        payload = {
            "request": request,
            "conversation": conversation or [],
            "clarifications": [],
            "conflicts": [],
        }
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO query_jobs VALUES(?,?,?,?,?,?,?,?)",
                (
                    jid,
                    request_id,
                    self.store.get("vault_epoch"),
                    "queued",
                    0,
                    stamp,
                    stamp,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            # Keep waiting jobs; bound terminal job/event history separately from saved answers.
            db.execute(
                "DELETE FROM query_jobs WHERE state IN "
                "('completed','cancelled','failed','interrupted','stale') AND id NOT IN "
                "(SELECT id FROM query_jobs ORDER BY updated_at DESC LIMIT 100)"
            )
        job = self.get(jid)
        self.save(job, "queued", "Question received.")
        return job, True

    def save(self, job, state, message, **observed):
        seq, stamp = job["seq"] + 1, now()
        event = {
            "job_id": job["id"],
            "seq": seq,
            "state": state,
            "message": message,
            "created_at": stamp,
            **observed,
        }
        with self.store.transaction() as db:
            changed = db.execute(
                "UPDATE query_jobs SET state=?,seq=?,updated_at=?,payload=? WHERE id=? AND seq=?",
                (
                    state,
                    seq,
                    stamp,
                    json.dumps(job["payload"], ensure_ascii=False),
                    job["id"],
                    job["seq"],
                ),
            ).rowcount
            if not changed:
                raise ValueError("The task status has changed. Refresh and try again.")
            db.execute(
                "INSERT INTO query_events VALUES(?,?,?)",
                (
                    job["id"],
                    seq,
                    json.dumps(event, ensure_ascii=False),
                ),
            )
            db.execute("DELETE FROM query_events WHERE job_id=? AND seq<=?", (job["id"], seq - 64))
        job.update(state=state, seq=seq, updated_at=stamp)

    def events(self, jid, after=0):
        return [
            json.loads(row["payload"])
            for row in self.store.rows(
                "SELECT payload FROM query_events WHERE job_id=? AND seq>? ORDER BY seq",
                (jid, after),
            )
        ]

    def public(self, job):
        payload = job["payload"]
        result = None
        if payload.get("run_id"):
            rows = self.store.rows("SELECT payload FROM runs WHERE id=?", (payload["run_id"],))
            result = json.loads(rows[0]["payload"]) if rows else None
        snapshot = payload.get("snapshot", {})
        return {
            "id": job["id"],
            "state": job["state"],
            "seq": job["seq"],
            "created_at": job["created_at"],
            "question": payload["request"]["question"],
            "conversation_id": payload["request"].get("conversation_id") or job["id"],
            "events": self.events(job["id"]),
            "result": result,
            "confirmation": payload.get("confirmation")
            if job["state"] == "awaiting_user"
            else None,
            "conflicts": payload.get("conflicts", []),
            "clarifications": payload.get("clarifications", []),
            "evidence": snapshot.get("evidence", []),
            "graph": snapshot.get("graph"),
            "warnings": snapshot.get("warnings", []),
        }
