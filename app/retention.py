"""Opt-in history retention; note indexes and downloaded models are preserved."""

import os
import sqlite3
import stat
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.indexer import now

RETENTION_DAYS = {0, 30, 90, 365}


def file_bytes(path):
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


class HistoryRetention:
    def __init__(self, service):
        self.service, self.store = service, service.store
        self.last_cleanup = None
        self.model_bytes = 0
        self.model_checked_at = None
        self.storage_warning = None

    def history(self):
        counts = self.store.rows(
            "SELECT count(*) runs, "
            "count(DISTINCT COALESCE(json_extract(payload, '$.conversation_id'), id)) "
            "conversations FROM runs"
        )[0]
        return {
            "retention_days": self.store.get("history_retention_days", 0),
            **counts,
            "jobs": self.store.rows("SELECT count(*) jobs FROM query_jobs")[0]["jobs"],
            "storage_warning": self.storage_warning,
        }

    def usage(self):
        current = time.monotonic()
        if self.model_checked_at is None or current - self.model_checked_at >= 300:
            size, root = 0, self.service.config.data_dir / "models"
            if not root.is_symlink():
                for folder, _, names in os.walk(root, followlinks=False):
                    for name in names:
                        try:
                            item = (Path(folder) / name).stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        if stat.S_ISREG(item.st_mode):
                            size += item.st_size
            self.model_bytes, self.model_checked_at = size, current
        database = self.service.config.data_dir / "index.sqlite3"
        with self.store.lock:
            page_size = self.store.rows("PRAGMA page_size")[0]["page_size"]
            free_pages = self.store.rows("PRAGMA freelist_count")[0]["freelist_count"]
        return {
            "database_bytes": file_bytes(database),
            "wal_bytes": file_bytes(Path(str(database) + "-wal")),
            "models_bytes": self.model_bytes,
            "reclaimable_bytes": page_size * free_pages,
        }

    def configure(self, days):
        if days not in RETENTION_DAYS:
            raise ValueError("Choose Keep all, 30, 90, or 365 days.")
        with self.service.operation:
            self.store.put("history_retention_days", days)
            self.last_cleanup = None
            deleted = self.cleanup_if_due()
        return {
            "saved": True,
            "retention_days": days,
            **(deleted or {"deleted_runs": 0, "deleted_jobs": 0, "reclaimed_bytes": 0}),
            "storage_warning": self.storage_warning,
        }

    def cleanup_if_due(self):
        with self.service.operation:
            days = self.store.get("history_retention_days", 0)
            current = time.monotonic()
            if not days or (self.last_cleanup is not None and current - self.last_cleanup < 86400):
                return
            self.last_cleanup = current
            try:
                return self.cleanup(days)
            except sqlite3.Error:
                self.storage_warning = (
                    "Automatic history cleanup could not finish. "
                    "Check available storage and try manual cleanup."
                )

    def cleanup(self, days):
        if days not in RETENTION_DAYS or not days:
            raise ValueError("Choose 30, 90, or 365 days for history cleanup.")
        cutoff = (datetime.fromisoformat(now()) - timedelta(days=days)).isoformat()
        with self.service.operation:
            before = self.usage()
            with self.store.transaction() as db:
                # A recent job can retain older turns in its copied conversation context.
                # Removing its row also makes late workers fail their publication guard.
                deleted_jobs = db.execute(
                    "DELETE FROM query_jobs WHERE created_at < ? OR "
                    "json_extract(payload, '$.run_id') IN "
                    "(SELECT id FROM runs WHERE created_at < ?) OR "
                    "(json_array_length(payload, '$.conversation') > 0 AND "
                    "json_extract(payload, '$.request.conversation_id') IN "
                    "(SELECT COALESCE(json_extract(payload, '$.conversation_id'), id) "
                    "FROM runs WHERE created_at < ?))",
                    (cutoff, cutoff, cutoff),
                ).rowcount
                deleted_runs = db.execute(
                    "DELETE FROM runs WHERE created_at < ?", (cutoff,)
                ).rowcount
            if deleted_runs or deleted_jobs or before["reclaimable_bytes"] or self.storage_warning:
                try:
                    self.store.compact()
                    self.storage_warning = None
                except sqlite3.Error:
                    self.storage_warning = (
                        "History was deleted, but storage could not be compacted. "
                        "Free disk space and try cleanup again."
                    )
            after = self.usage()
        reclaimed = (
            before["database_bytes"]
            + before["wal_bytes"]
            - after["database_bytes"]
            - after["wal_bytes"]
        )
        return {
            "deleted_runs": deleted_runs,
            "deleted_jobs": deleted_jobs,
            "reclaimed_bytes": max(0, reclaimed),
            "storage_warning": self.storage_warning,
        }
