from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from uuid import uuid4

from .models import ActivityHistoryRecord


class ActivityStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS completions (
                    record_id TEXT PRIMARY KEY,
                    employee_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_completions_employee "
                "ON completions(employee_id, completed_at)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_nonrepeat_completion "
                "ON completions(employee_id, event_id) WHERE event_id != 'EV_036'"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def list_for_employee(self, employee_id: str) -> list[ActivityHistoryRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM completions WHERE employee_id = ? ORDER BY completed_at, record_id",
                (employee_id,),
            ).fetchall()
        return [
            ActivityHistoryRecord(
                record_id=row["record_id"],
                employee_id=row["employee_id"],
                event_id=row["event_id"],
                date=date.fromisoformat(row["completed_at"]),
                due_date=None,
                status="completed",
                completion_pct=100,
                score=None,
                feedback_rating=None,
                assigned_by="self",
            )
            for row in rows
        ]

    def complete(self, employee_id: str, event_id: str, completed_at: date) -> ActivityHistoryRecord:
        record_id = f"D{uuid4().hex[:12].upper()}"
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO completions(record_id, employee_id, event_id, completed_at) "
                "VALUES (?, ?, ?, ?)",
                (record_id, employee_id, event_id, completed_at.isoformat()),
            )
        return ActivityHistoryRecord(
            record_id=record_id,
            employee_id=employee_id,
            event_id=event_id,
            date=completed_at,
            due_date=None,
            status="completed",
            completion_pct=100,
            score=None,
            feedback_rating=None,
            assigned_by="self",
        )
