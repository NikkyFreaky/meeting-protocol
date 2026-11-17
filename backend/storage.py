"""Persistent meetings, processing jobs and delivery receipts for one local server."""

import json
import logging
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import time


LOGGER = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Storage:
    def __init__(self, directory: Path):
        self.directory = directory
        self.media_directory = directory / "media"
        self.database = directory / "meetings.sqlite3"

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection

    def initialize(self):
        self.media_directory.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS meetings (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    media_file TEXT,
                    media_type TEXT,
                    speaker_count INTEGER,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT,
                    error TEXT,
                    duration REAL,
                    demo INTEGER NOT NULL DEFAULT 0,
                    speakers TEXT,
                    segments TEXT,
                    protocol TEXT
                );
                CREATE INDEX IF NOT EXISTS meeting_queue ON meetings (status, created_at);
                CREATE TABLE IF NOT EXISTS task_deliveries (
                    meeting_id TEXT NOT NULL REFERENCES meetings(id),
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    external_id TEXT,
                    url TEXT,
                    PRIMARY KEY (meeting_id, task_id)
                );
                CREATE TABLE IF NOT EXISTS processing_events (
                    id INTEGER PRIMARY KEY,
                    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS processing_events_meeting ON processing_events (meeting_id, id);
            """)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(meetings)")}
            if "processing_started_at" not in columns:
                connection.execute("ALTER TABLE meetings ADD COLUMN processing_started_at REAL")
            if "processing_seconds" not in columns:
                connection.execute("ALTER TABLE meetings ADD COLUMN processing_seconds REAL")
            connection.execute("""
                UPDATE meetings SET status = 'queued', stage = 'queued',
                    processing_started_at = NULL, duration = NULL
                WHERE status = 'processing'
            """)
            # An interrupted HTTP request may already have created a remote task.
            connection.execute("UPDATE task_deliveries SET status = 'uncertain' WHERE status = 'sending'")
            connection.execute("UPDATE meetings SET status = 'purging' WHERE status = 'deleting'")
        self.purge_deleted()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        meeting = dict(row)
        meeting["demo"] = bool(meeting["demo"])
        for field, fallback in (
            ("speakers", {}), ("segments", []),
            ("protocol", {"summary": "", "topics": [], "decisions": [], "tasks": []}),
        ):
            if field in meeting:
                meeting[field] = json.loads(meeting[field]) if meeting[field] else fallback
        return meeting

    def add(self, *, meeting_id, title, filename, media_file, media_type, speaker_count=None, result=None):
        with self.connection() as connection:
            if result is not None:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute("""
                    SELECT * FROM meetings WHERE demo = 1 AND status = 'completed'
                    ORDER BY created_at DESC, rowid DESC LIMIT 1
                """).fetchone()
                if existing is not None:
                    return self.decode(existing)
            connection.execute("""
                INSERT INTO meetings (
                    id, title, filename, media_file, media_type, speaker_count,
                    created_at, status, stage, duration, demo, speakers, segments, protocol
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                meeting_id, title, filename, media_file, media_type, speaker_count,
                utc_now(), "queued" if result is None else "completed",
                "queued" if result is None else "ready",
                result["duration"] if result else None, int(result is not None),
                json.dumps(result["speakers"], ensure_ascii=False) if result else None,
                json.dumps(result["segments"], ensure_ascii=False) if result else None,
                json.dumps(result["protocol"], ensure_ascii=False) if result else None,
            ))
            return self.decode(connection.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone())

    def list(self):
        with self.connection() as connection:
            rows = connection.execute("""
                SELECT id, title, filename, media_file, created_at, status, stage, error, duration, demo,
                    processing_started_at
                FROM meetings
                WHERE status NOT IN ('deleting', 'purging') AND (demo = 0 OR id = (
                    SELECT id FROM meetings WHERE demo = 1 AND status = 'completed'
                    ORDER BY created_at DESC, rowid DESC LIMIT 1
                ))
                ORDER BY created_at DESC, rowid DESC
            """).fetchall()
        return [self.decode(row) for row in rows]

    def get(self, meeting_id):
        with self.connection() as connection:
            return self.decode(connection.execute("""
                SELECT * FROM meetings WHERE id = ? AND status NOT IN ('deleting', 'purging')
            """, (meeting_id,)).fetchone())

    def mark_for_deletion(self, meeting_id):
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status, demo FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if row is None or row["status"] in {"deleting", "purging"}:
                return "accepted"
            if connection.execute("""
                SELECT 1 FROM task_deliveries WHERE meeting_id = ? AND status = 'sending' LIMIT 1
            """, (meeting_id,)).fetchone():
                return "sending"
            if row["demo"]:
                # Retiring the visible example must not reveal older copies.
                connection.execute("UPDATE meetings SET status = 'purging' WHERE demo = 1")
            else:
                status = "deleting" if row["status"] == "processing" else "purging"
                connection.execute("UPDATE meetings SET status = ? WHERE id = ?", (status, meeting_id))
        return "accepted"

    def release_deleted(self, meeting_id):
        with self.connection() as connection:
            result = connection.execute("""
                UPDATE meetings SET status = 'purging' WHERE id = ? AND status = 'deleting'
            """, (meeting_id,))
        if result.rowcount:
            self.purge_deleted()

    def purge_deleted(self):
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for row in connection.execute("SELECT id, media_file FROM meetings WHERE status = 'purging'").fetchall():
                if row["media_file"]:
                    try:
                        (self.media_directory / row["media_file"]).unlink(missing_ok=True)
                    except OSError:
                        LOGGER.warning("Файл удалённой записи %s пока недоступен для удаления", row["id"])
                        continue
                connection.execute("DELETE FROM task_deliveries WHERE meeting_id = ?", (row["id"],))
                connection.execute("DELETE FROM meetings WHERE id = ?", (row["id"],))

    def claim(self):
        with self.connection() as connection:
            row = connection.execute("""
                UPDATE meetings SET status = 'processing', stage = 'decode', error = NULL,
                    processing_started_at = ?
                WHERE id = (SELECT id FROM meetings WHERE status = 'queued' ORDER BY created_at, rowid LIMIT 1)
                RETURNING *
            """, (time(),)).fetchone()
            if row is not None:
                connection.execute("DELETE FROM processing_events WHERE meeting_id = ?", (row["id"],))
            return self.decode(row)

    @staticmethod
    def _append_processing_event(connection, meeting_id, kind, message):
        connection.execute("""
            INSERT INTO processing_events (meeting_id, stage, kind, message, created_at)
            SELECT id, stage, ?, ?, ? FROM meetings WHERE id = ? AND status = 'processing'
        """, (kind, message, utc_now(), meeting_id))

    def log_processing(self, meeting_id, kind, message):
        with self.connection() as connection:
            self._append_processing_event(connection, meeting_id, kind, message)

    def processing_events(self, meeting_id):
        with self.connection() as connection:
            rows = connection.execute("""
                SELECT id, stage, kind, message, created_at FROM processing_events
                WHERE meeting_id = ? ORDER BY id
            """, (meeting_id,)).fetchall()
        return [dict(row) for row in rows]

    def progress(self, meeting_id, stage):
        with self.connection() as connection:
            result = connection.execute("UPDATE meetings SET stage = ? WHERE id = ? AND status = 'processing'", (stage, meeting_id))
            if result.rowcount:
                self._append_processing_event(connection, meeting_id, "start", "Этап начат.")
            return bool(result.rowcount)

    def set_duration(self, meeting_id, duration):
        with self.connection() as connection:
            connection.execute("UPDATE meetings SET duration = ? WHERE id = ? AND status = 'processing'", (duration, meeting_id))

    def processing_rate(self):
        with self.connection() as connection:
            samples = connection.execute("""
                SELECT processing_seconds / duration FROM meetings
                WHERE status = 'completed' AND demo = 0 AND duration >= 30 AND processing_seconds > 0
                ORDER BY processing_started_at DESC LIMIT 10
            """).fetchall()
        return median(row[0] for row in samples) if samples else None

    def complete(self, meeting_id, result):
        with self.connection() as connection:
            connection.execute("""
                UPDATE meetings SET status = 'completed', stage = 'ready',
                    duration = ?, speakers = ?, segments = ?, protocol = ?,
                    processing_seconds = MAX(0.001, ? - processing_started_at)
                WHERE id = ? AND status = 'processing'
            """, (
                result["duration"], json.dumps(result["speakers"], ensure_ascii=False),
                json.dumps(result["segments"], ensure_ascii=False),
                json.dumps(result["protocol"], ensure_ascii=False), time(), meeting_id,
            ))

    def fail(self, meeting_id, error):
        with self.connection() as connection:
            self._append_processing_event(connection, meeting_id, "error", error)
            connection.execute("""
                UPDATE meetings SET status = 'failed', stage = 'failed', error = ?
                WHERE id = ? AND status = 'processing'
            """, (error, meeting_id))

    def edit(self, meeting_id, speakers, protocol):
        with self.connection() as connection:
            row = connection.execute("""
                UPDATE meetings SET speakers = json_patch(speakers, COALESCE(?, '{}')),
                    protocol = COALESCE(?, protocol)
                WHERE id = ? AND status = 'completed' RETURNING *
            """, (
                json.dumps(speakers, ensure_ascii=False) if speakers is not None else None,
                json.dumps(protocol, ensure_ascii=False) if protocol is not None else None,
                meeting_id,
            )).fetchone()
            return self.decode(row)

    def deliveries(self, meeting_id):
        with self.connection() as connection:
            rows = connection.execute("""
                SELECT task_id, external_id, url FROM task_deliveries
                WHERE meeting_id = ? AND status = 'sent' ORDER BY rowid
            """, (meeting_id,)).fetchall()
        return [dict(row) for row in rows]

    def reserve_task(self, meeting_id, task_id):
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("""
                SELECT 1 FROM meetings WHERE id = ? AND status = 'completed'
            """, (meeting_id,)).fetchone():
                return {"status": "unavailable"}
            inserted = connection.execute("""
                INSERT INTO task_deliveries (meeting_id, task_id, status) VALUES (?, ?, 'sending')
                ON CONFLICT (meeting_id, task_id) DO NOTHING
            """, (meeting_id, task_id))
            if inserted.rowcount:
                return None
            previous = connection.execute("""
                SELECT status, external_id, url FROM task_deliveries WHERE meeting_id = ? AND task_id = ?
            """, (meeting_id, task_id)).fetchone()
            return dict(previous)

    def complete_task(self, meeting_id, task_id, receipt):
        with self.connection() as connection:
            connection.execute("""
                UPDATE task_deliveries SET status = 'sent', external_id = ?, url = ?
                WHERE meeting_id = ? AND task_id = ?
            """, (receipt["external_id"], receipt["url"], meeting_id, task_id))

    def fail_task(self, meeting_id, task_id, *, uncertain=False):
        with self.connection() as connection:
            if uncertain:
                connection.execute("""
                    UPDATE task_deliveries SET status = 'uncertain' WHERE meeting_id = ? AND task_id = ?
                """, (meeting_id, task_id))
            else:
                connection.execute("""
                    DELETE FROM task_deliveries WHERE meeting_id = ? AND task_id = ?
                """, (meeting_id, task_id))
