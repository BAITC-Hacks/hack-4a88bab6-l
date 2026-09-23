"""Transactional seed and preview/commit import for Career Quest data."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import (
    DatasetError,
    HISTORY_COLUMNS,
    MAX_FILE_BYTES,
    load_dataset,
    parse_json_wrapper,
)


GRADES = {"Junior", "Middle", "Senior", "Lead"}
HISTORY_STATUSES = {"completed", "in_progress", "dropped", "no_show", "declined", "overdue"}
ASSIGNERS = {"self", "manager", "hr"}
ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")


class ImportConflict(ValueError):
    """Preview is invalid, stale, or requires explicit update confirmation."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _error(errors: list[dict[str, Any]], source: str, row: int | None, field: str, message: str) -> None:
    errors.append({"source": source, "row": row, "field": field, "message": message})


def _iso_date(value: Any, errors: list[dict[str, Any]], source: str, row: int, field: str) -> str | None:
    if not isinstance(value, str):
        _error(errors, source, row, field, "Expected ISO date YYYY-MM-DD")
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _error(errors, source, row, field, "Invalid calendar date")
        return None
    if parsed.isoformat() != value:
        _error(errors, source, row, field, "Expected ISO date YYYY-MM-DD")
        return None
    return value


def _int_range(value: Any, low: int, high: int, errors: list[dict[str, Any]], source: str, row: int, field: str) -> bool:
    if type(value) is not int or not low <= value <= high:
        _error(errors, source, row, field, f"Expected integer from {low} to {high}")
        return False
    return True


def _text(value: Any, errors: list[dict[str, Any]], source: str, row: int, field: str) -> bool:
    if not isinstance(value, str) or not value.strip():
        _error(errors, source, row, field, "Expected non-empty text")
        return False
    return True


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    found = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return found[0] if found else None


def _catalog(conn: sqlite3.Connection) -> tuple[set[str], set[tuple[str, str]], dict[str, dict[str, Any]]]:
    skills = {row[0] for row in conn.execute("SELECT skill_id FROM skills")}
    profiles = {(row[0], row[1]) for row in conn.execute("SELECT role,grade FROM role_profiles")}
    events = {row[0]: json.loads(row[1]) for row in conn.execute("SELECT event_id,data FROM events")}
    return skills, profiles, events


def _fingerprint(conn: sqlite3.Connection) -> str:
    """Detect intervening imports, assessment updates, or app completions."""
    digest = hashlib.sha256()
    for table, ordering in (
        ("meta", "key"), ("skills", "skill_id"), ("role_profiles", "role,grade"),
        ("events", "event_id"), ("employees", "employee_id"),
        ("employee_versions", "employee_id,version"),
        ("history", "record_id"), ("app_participations", "id"),
    ):
        digest.update(table.encode())
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY {ordering}"):
            digest.update(_json(tuple(row)).encode("utf-8"))
    return digest.hexdigest()


def _parse_uploaded_history(content: bytes, errors: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    source = "activity_history.csv"
    if len(content) > MAX_FILE_BYTES:
        _error(errors, source, None, "file", "File exceeds 16 MB")
        return []
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeError:
        _error(errors, source, None, "file", "Expected UTF-8 CSV")
        return []
    reader = csv.DictReader(io.StringIO(decoded, newline=""))
    if tuple(reader.fieldnames or ()) != HISTORY_COLUMNS:
        _error(errors, source, 1, "columns", "Expected the original ten CSV columns in order")
        return []
    result: list[tuple[int, dict[str, Any]]] = []
    for raw in reader:
        line = reader.line_num
        if None in raw or any(value is None for value in raw.values()):
            _error(errors, source, line, "columns", "Wrong number of values")
            continue
        row: dict[str, Any] = dict(raw)
        for key in ("due_date", "score", "feedback_rating"):
            row[key] = row[key] or None
        for key in ("completion_pct", "score", "feedback_rating"):
            if row[key] is None:
                continue
            try:
                # Reject 1.2 or 03 instead of silently accepting a changed format.
                if not re.fullmatch(r"\d+", row[key]):
                    raise ValueError
                row[key] = int(row[key])
            except ValueError:
                _error(errors, source, line, key, "Expected integer")
                row[key] = None
        result.append((line, row))
    return result


def _validate_catalog(dataset: dict[str, Any]) -> None:
    errors: list[dict[str, Any]] = []
    as_of = _iso_date(dataset["meta"].get("as_of_date"), errors, "meta", 1, "as_of_date")
    skills: set[str] = set()
    profiles: set[tuple[str, str]] = set()
    events: set[str] = set()
    for i, skill in enumerate(dataset["skills"], 1):
        if not isinstance(skill, dict):
            _error(errors, "skills.json", i, "skills", "Expected object")
            continue
        skill_id = skill.get("skill_id")
        if not isinstance(skill_id, str) or not ID_PATTERN.fullmatch(skill_id) or skill_id in skills:
            _error(errors, "skills.json", i, "skill_id", "Invalid or duplicate skill ID")
        else:
            skills.add(skill_id)
        for field in ("name", "category", "description"):
            _text(skill.get(field), errors, "skills.json", i, field)
        if skill.get("type") not in {"hard", "soft"}:
            _error(errors, "skills.json", i, "type", "Expected hard or soft")
    for i, profile in enumerate(dataset["role_profiles"], 1):
        if not isinstance(profile, dict):
            _error(errors, "skills.json", i, "role_profiles", "Expected object")
            continue
        role, grade = profile.get("role"), profile.get("grade")
        if not isinstance(role, str) or not role or grade not in GRADES or (role, grade) in profiles:
            _error(errors, "skills.json", i, "role/grade", "Invalid or duplicate role profile")
        else:
            profiles.add((role, grade))
        requirements = profile.get("required_skills")
        if not isinstance(requirements, dict):
            _error(errors, "skills.json", i, "required_skills", "Expected skill-to-level object")
        else:
            for skill_id, level in requirements.items():
                if skill_id not in skills:
                    _error(errors, "skills.json", i, f"required_skills.{skill_id}", "Unknown skill")
                _int_range(level, 0, 5, errors, "skills.json", i, f"required_skills.{skill_id}")
        critical = profile.get("critical_skills")
        if not isinstance(critical, list) or any(skill_id not in skills for skill_id in critical):
            _error(errors, "skills.json", i, "critical_skills", "Expected known skill IDs")
    for i, event in enumerate(dataset["events"], 1):
        if not isinstance(event, dict):
            _error(errors, "events.json", i, "events", "Expected object")
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not ID_PATTERN.fullmatch(event_id) or event_id in events:
            _error(errors, "events.json", i, "event_id", "Invalid or duplicate event ID")
        else:
            events.add(event_id)
        for field in ("title", "description", "type"):
            _text(event.get(field), errors, "events.json", i, field)
        if event.get("format") not in {"online", "offline", "self_paced"}:
            _error(errors, "events.json", i, "format", "Unknown format")
        if type(event.get("mandatory")) is not bool:
            _error(errors, "events.json", i, "mandatory", "Expected boolean")
        hours = event.get("duration_hours")
        if type(hours) not in {int, float} or hours <= 0:
            _error(errors, "events.json", i, "duration_hours", "Expected positive number")
        for field in ("target_roles", "target_grades"):
            if not isinstance(event.get(field), list) or not all(isinstance(v, str) for v in event[field]):
                _error(errors, "events.json", i, field, "Expected string array")
        if isinstance(event.get("target_grades"), list) and any(v not in GRADES for v in event["target_grades"]):
            _error(errors, "events.json", i, "target_grades", "Unknown grade")
        develops = event.get("develops_skills")
        if not isinstance(develops, list):
            _error(errors, "events.json", i, "develops_skills", "Expected array")
        else:
            for gain in develops:
                if not isinstance(gain, dict) or gain.get("skill_id") not in skills:
                    _error(errors, "events.json", i, "develops_skills.skill_id", "Unknown skill")
                    continue
                _int_range(gain.get("gain"), 0, 5, errors, "events.json", i, "develops_skills.gain")
                _int_range(gain.get("max_level"), 0, 5, errors, "events.json", i, "develops_skills.max_level")
        prereqs = event.get("prerequisites")
        if not isinstance(prereqs, dict):
            _error(errors, "events.json", i, "prerequisites", "Expected skill-to-level object")
        else:
            for skill_id, level in prereqs.items():
                if skill_id not in skills:
                    _error(errors, "events.json", i, f"prerequisites.{skill_id}", "Unknown skill")
                _int_range(level, 0, 5, errors, "events.json", i, f"prerequisites.{skill_id}")
        sessions = event.get("upcoming_sessions")
        if not isinstance(sessions, list):
            _error(errors, "events.json", i, "upcoming_sessions", "Expected date array")
        else:
            for session in sessions:
                _iso_date(session, errors, "events.json", i, "upcoming_sessions")
    if as_of is None or errors:
        raise DatasetError("Invalid seed catalog: " + _json(errors[:20]))
    for event in dataset["events"]:
        for role in event["target_roles"]:
            if role not in {item[0] for item in profiles}:
                raise DatasetError(f"{event['event_id']}: unknown target role {role}")


def _classify(
    conn: sqlite3.Connection,
    employees: list[dict[str, Any]],
    history: list[tuple[int, dict[str, Any]]],
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    errors = errors if errors is not None else []
    warnings: list[str] = []
    skills, profiles, events = _catalog(conn)
    as_of = _meta(conn, "as_of_date")
    if not as_of:
        raise ImportConflict("Seed the catalog before importing profiles")
    current_employees = {
        row[0]: (json.loads(row[1]), row[2])
        for row in conn.execute("SELECT employee_id,data,version FROM employees")
    }
    current_history = {
        row[0]: json.loads(row[1])
        for row in conn.execute("SELECT record_id,data FROM history")
    }
    pending_ids: set[str] = set()
    pending_records: set[str] = set()
    valid_employees: list[dict[str, Any]] = []
    valid_history: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    counts = {
        "employees_new": 0, "employees_unchanged": 0, "employees_updates": 0,
        "history_new": 0, "history_unchanged": 0, "history_conflicts": 0,
    }

    for row_number, employee in enumerate(employees, 1):
        source = "employees.json"
        before = len(errors)
        if not isinstance(employee, dict):
            _error(errors, source, row_number, "employee", "Expected object")
            continue
        employee_id = employee.get("employee_id")
        if not isinstance(employee_id, str) or not ID_PATTERN.fullmatch(employee_id):
            _error(errors, source, row_number, "employee_id", "Invalid ID")
        elif employee_id in pending_ids:
            _error(errors, source, row_number, "employee_id", "Duplicate ID in upload")
        else:
            pending_ids.add(employee_id)
        for field in ("full_name", "department", "role"):
            _text(employee.get(field), errors, source, row_number, field)
        role, grade = employee.get("role"), employee.get("grade")
        if not isinstance(role, str) or not isinstance(grade, str) or (role, grade) not in profiles:
            _error(errors, source, row_number, "role/grade", "Unknown role profile")
        if not isinstance(employee.get("work_format"), str) or employee["work_format"] not in {"office", "hybrid", "remote"}:
            _error(errors, source, row_number, "work_format", "Unknown work format")
        if not isinstance(employee.get("preferred_language"), str) or employee["preferred_language"] not in {"kk", "ru", "en"}:
            _error(errors, source, row_number, "preferred_language", "Unknown language")
        _int_range(employee.get("tenure_months"), 0, 2000, errors, source, row_number, "tenure_months")
        hired = _iso_date(employee.get("hire_date"), errors, source, row_number, "hire_date")
        reviewed = _iso_date(employee.get("last_review_date"), errors, source, row_number, "last_review_date")
        if hired and hired > as_of:
            _error(errors, source, row_number, "hire_date", "Date is after dataset as_of_date")
        if reviewed and reviewed > as_of:
            _error(errors, source, row_number, "last_review_date", "Date is after dataset as_of_date")
        if hired and reviewed and reviewed < hired:
            _error(errors, source, row_number, "last_review_date", "Review predates hire")
        skill_levels = employee.get("skills")
        if not isinstance(skill_levels, dict):
            _error(errors, source, row_number, "skills", "Expected skill-to-level object")
        else:
            for skill_id, level in skill_levels.items():
                if skill_id not in skills:
                    _error(errors, source, row_number, f"skills.{skill_id}", "Unknown skill")
                _int_range(level, 0, 5, errors, source, row_number, f"skills.{skill_id}")
        goal = employee.get("career_goal")
        if goal is not None and (
            not isinstance(goal, dict)
            or not isinstance(goal.get("target_role"), str)
            or not isinstance(goal.get("target_grade"), str)
            or (goal.get("target_role"), goal.get("target_grade")) not in profiles
        ):
            _error(errors, source, row_number, "career_goal", "Unknown target role/grade")
        manager_id = employee.get("manager_id")
        if manager_id is not None and (
            not isinstance(manager_id, str) or not ID_PATTERN.fullmatch(manager_id) or manager_id == employee_id
        ):
            _error(errors, source, row_number, "manager_id", "Invalid manager reference")
        if len(errors) != before:
            continue
        valid_employees.append(employee)
        old = current_employees.get(employee_id)
        if old is None:
            counts["employees_new"] += 1
        elif old[0] == employee:
            counts["employees_unchanged"] += 1
        else:
            if reviewed < old[0]["last_review_date"]:
                _error(errors, source, row_number, "last_review_date", "Updated assessment cannot predate the stored assessment")
            counts["employees_updates"] += 1
            updates.append({
                "employee_id": employee_id,
                "old_version": old[1],
                "old_review_date": old[0]["last_review_date"],
                "new_review_date": reviewed,
            })

    known_employee_ids = set(current_employees) | {e["employee_id"] for e in valid_employees}
    for row_number, employee in enumerate(employees, 1):
        if not isinstance(employee, dict):
            continue
        manager_id = employee.get("manager_id")
        if isinstance(manager_id, str) and manager_id not in known_employee_ids:
            _error(errors, "employees.json", row_number, "manager_id", "Manager not found in existing or uploaded profiles")

    for line_number, record in history:
        source = "activity_history.csv"
        before = len(errors)
        if not isinstance(record, dict):
            _error(errors, source, line_number, "record", "Expected object")
            continue
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or not ID_PATTERN.fullmatch(record_id):
            _error(errors, source, line_number, "record_id", "Invalid ID")
        elif record_id in pending_records:
            _error(errors, source, line_number, "record_id", "Duplicate ID in upload")
        else:
            pending_records.add(record_id)
        employee_id, event_id = record.get("employee_id"), record.get("event_id")
        if not isinstance(employee_id, str) or employee_id not in known_employee_ids:
            _error(errors, source, line_number, "employee_id", "Employee not found in existing or uploaded profiles")
        if not isinstance(event_id, str) or event_id not in events:
            _error(errors, source, line_number, "event_id", "Event not found in catalog")
        happened = _iso_date(record.get("date"), errors, source, line_number, "date")
        if happened and happened > as_of:
            _error(errors, source, line_number, "date", "Date is after dataset as_of_date")
        due = record.get("due_date")
        due_valid = None
        if due is not None:
            due_valid = _iso_date(due, errors, source, line_number, "due_date")
            if isinstance(event_id, str) and event_id in events and not events[event_id]["mandatory"]:
                _error(errors, source, line_number, "due_date", "Only mandatory events have a due date")
            if due_valid and happened and due_valid < happened:
                _error(errors, source, line_number, "due_date", "Due date predates participation")
        status = record.get("status")
        if not isinstance(status, str) or status not in HISTORY_STATUSES:
            _error(errors, source, line_number, "status", "Unknown participation status")
        elif isinstance(event_id, str) and event_id in events:
            event = events[event_id]
            if status == "no_show" and event["format"] == "self_paced":
                _error(errors, source, line_number, "status", "No-show requires a scheduled online/offline event")
            if status == "overdue":
                if not event["mandatory"]:
                    _error(errors, source, line_number, "status", "Overdue applies only to mandatory events")
                if not due_valid:
                    _error(errors, source, line_number, "due_date", "Overdue requires a due date")
                elif due_valid >= as_of:
                    _error(errors, source, line_number, "due_date", "Overdue due date must be before as_of_date")
        pct = record.get("completion_pct")
        if _int_range(pct, 0, 100, errors, source, line_number, "completion_pct"):
            if status == "completed" and pct != 100:
                _error(errors, source, line_number, "completion_pct", "Completed requires 100")
            elif status in {"no_show", "declined"} and pct != 0:
                _error(errors, source, line_number, "completion_pct", f"{status} requires 0")
            elif status in {"in_progress", "overdue"} and pct > 95:
                _error(errors, source, line_number, "completion_pct", f"{status} requires at most 95")
            elif status == "dropped" and not 5 <= pct <= 95:
                _error(errors, source, line_number, "completion_pct", "Dropped requires 5–95")
        score = record.get("score")
        if score is not None:
            _int_range(score, 0, 100, errors, source, line_number, "score")
            if isinstance(event_id, str) and event_id in events and events[event_id]["type"] not in {"course", "certification", "compliance"}:
                _error(errors, source, line_number, "score", "Score is only defined for courses, certifications and compliance")
        feedback = record.get("feedback_rating")
        if feedback is not None:
            _int_range(feedback, 1, 5, errors, source, line_number, "feedback_rating")
        if not isinstance(record.get("assigned_by"), str) or record["assigned_by"] not in ASSIGNERS:
            _error(errors, source, line_number, "assigned_by", "Expected self, manager or hr")
        elif status == "declined" and record["assigned_by"] == "self":
            _error(errors, source, line_number, "assigned_by", "Declined applies to manager/HR assignments")
        if isinstance(employee_id, str) and employee_id in known_employee_ids and happened:
            employee = next((e for e in valid_employees if e["employee_id"] == employee_id), None)
            if employee is None:
                employee = current_employees[employee_id][0]
            if happened < employee["hire_date"]:
                _error(errors, source, line_number, "date", "Participation predates hire")
        if len(errors) != before:
            continue
        valid_history.append(record)
        old = current_history.get(record_id)
        if old is None:
            counts["history_new"] += 1
        elif old == record:
            counts["history_unchanged"] += 1
        else:
            counts["history_conflicts"] += 1
            _error(errors, source, line_number, "record_id", "Same ID exists with different content")

    if counts["employees_updates"]:
        warnings.append("Profile updates require explicit confirmation. The imported last review replaces the assessment baseline.")
    same_day_completions: list[dict[str, Any]] = []
    for update in updates:
        for row in conn.execute(
            """SELECT id,employee_id,event_id,completion_date FROM app_participations
            WHERE employee_id=? AND status='completed' AND completion_date=?
              AND covered_by_review_version IS NULL ORDER BY id""",
            (update["employee_id"], update["new_review_date"]),
        ):
            same_day_completions.append({
                "id": row[0], "employee_id": row[1],
                "event_id": row[2], "completion_date": row[3],
            })
    counts.update({
        "new_employees": counts["employees_new"],
        "updated_employees": counts["employees_updates"],
        "new_history": counts["history_new"],
        "duplicates": counts["employees_unchanged"] + counts["history_unchanged"],
    })
    return {
        "ok": not errors,
        "counts": counts,
        "errors": errors,
        "warnings": warnings,
        "employees": valid_employees,
        "history": valid_history,
        "updates": updates,
        "same_day_completions": same_day_completions,
        "fingerprint": _fingerprint(conn),
    }


def preview_import(
    conn: sqlite3.Connection,
    employees_bytes: bytes | None = None,
    history_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Validate uploaded data and return a server-side commit plan; make no writes."""
    started_read = not conn.in_transaction
    if started_read:
        conn.execute("BEGIN")
    try:
        errors: list[dict[str, Any]] = []
        if employees_bytes is None and history_bytes is None:
            _error(errors, "import", None, "files", "Provide employees JSON and/or activity history CSV")
        employees: list[dict[str, Any]] = []
        if employees_bytes is not None:
            if len(employees_bytes) > MAX_FILE_BYTES:
                _error(errors, "employees.json", None, "file", "File exceeds 16 MB")
            else:
                try:
                    payload = parse_json_wrapper(employees_bytes, "employees.json", "employees")
                    uploaded_date = payload["meta"].get("as_of_date")
                    if uploaded_date != _meta(conn, "as_of_date"):
                        _error(errors, "employees.json", None, "meta.as_of_date", "Dataset date differs from stored as_of_date")
                    employees = payload["employees"]
                except DatasetError as exc:
                    _error(errors, "employees.json", None, "file", str(exc))
        history: list[tuple[int, dict[str, Any]]] = []
        if history_bytes is not None:
            history = _parse_uploaded_history(history_bytes, errors)
        plan = _classify(conn, employees, history, errors)
        plan["history_lines"] = [line for line, _ in history]
        plan["payload_digest"] = hashlib.sha256(_json((
            plan["employees"], plan["history"], plan["history_lines"],
        )).encode("utf-8")).hexdigest()
        return plan
    finally:
        if started_read:
            conn.rollback()


def _commit_plan(
    conn: sqlite3.Connection,
    plan: dict[str, Any],
    confirm_updates: bool,
    cover_same_day_completions: bool = False,
) -> dict[str, Any]:
    if not plan.get("ok"):
        raise ImportConflict("Import preview contains errors")
    if plan["counts"]["employees_updates"] and not confirm_updates:
        raise ImportConflict("Profile updates require explicit confirmation")
    affected: set[str] = set()
    covered_same_day = 0
    recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for employee in plan["employees"]:
        employee_id = employee["employee_id"]
        existing = conn.execute(
            "SELECT data,version FROM employees WHERE employee_id=?", (employee_id,)
        ).fetchone()
        if existing is None:
            serialized = _json(employee)
            conn.execute(
                "INSERT INTO employees(employee_id,data,version) VALUES(?,?,1)",
                (employee_id, serialized),
            )
            conn.execute(
                "INSERT INTO employee_versions(employee_id,version,data,created_at) VALUES(?,?,?,?)",
                (employee_id, 1, serialized, recorded_at),
            )
            affected.add(employee_id)
        elif json.loads(existing[0]) != employee:
            old_review = json.loads(existing[0])["last_review_date"]
            new_version = existing[1] + 1
            serialized = _json(employee)
            conn.execute(
                "UPDATE employees SET data=?,version=? WHERE employee_id=?",
                (serialized, new_version, employee_id),
            )
            conn.execute(
                "INSERT INTO employee_versions(employee_id,version,data,created_at) VALUES(?,?,?,?)",
                (employee_id, new_version, serialized, recorded_at),
            )
            if employee["last_review_date"] > old_review:
                conn.execute(
                    """UPDATE app_participations
                    SET covered_by_review_version=?
                    WHERE employee_id=? AND status='completed' AND completion_date<?
                    AND covered_by_review_version IS NULL""",
                    (new_version, employee_id, employee["last_review_date"]),
                )
            if cover_same_day_completions:
                covered_same_day += conn.execute(
                    """UPDATE app_participations
                    SET covered_by_review_version=?
                    WHERE employee_id=? AND status='completed' AND completion_date=?
                    AND covered_by_review_version IS NULL""",
                    (new_version, employee_id, employee["last_review_date"]),
                ).rowcount
            conn.execute("DELETE FROM recommendations WHERE employee_id=?", (employee_id,))
            affected.add(employee_id)
    for record in plan["history"]:
        if conn.execute("SELECT 1 FROM history WHERE record_id=?", (record["record_id"],)).fetchone():
            continue
        conn.execute(
            "INSERT INTO history(record_id,employee_id,event_id,date,status,data) VALUES(?,?,?,?,?,?)",
            (record["record_id"], record["employee_id"], record["event_id"],
             record["date"], record["status"], _json(record)),
        )
        affected.add(record["employee_id"])
    for employee_id in affected:
        conn.execute("DELETE FROM recommendations WHERE employee_id=?", (employee_id,))
    return {
        "counts": plan["counts"],
        "affected_employees": sorted(affected),
        "covered_same_day_completions": covered_same_day,
    }


def commit_import(
    conn: sqlite3.Connection,
    plan: dict[str, Any],
    confirm_updates: bool = False,
    actor: str | None = None,
    *,
    cover_same_day_completions: bool = False,
) -> dict[str, Any]:
    """Commit a preview atomically; reject a stale database snapshot."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not plan.get("ok"):
            raise ImportConflict("Import preview contains errors")
        digest = hashlib.sha256(_json((
            plan.get("employees"), plan.get("history"), plan.get("history_lines"),
        )).encode("utf-8")).hexdigest()
        if digest != plan.get("payload_digest"):
            raise ImportConflict("Import plan changed; preview the files again")
        if plan.get("fingerprint") != _fingerprint(conn):
            raise ImportConflict("Database changed after preview; preview the files again")
        history = list(zip(plan.get("history_lines", []), plan.get("history", [])))
        if len(history) != len(plan.get("history", [])):
            raise ImportConflict("Incomplete import plan")
        refreshed = _classify(conn, plan.get("employees", []), history)
        if not refreshed["ok"] or refreshed["counts"] != plan.get("counts"):
            raise ImportConflict("Import plan changed; preview the files again")
        if cover_same_day_completions and not confirm_updates:
            raise ImportConflict("Covering same-day completions requires profile update confirmation")
        result = _commit_plan(conn, refreshed, confirm_updates, cover_same_day_completions)
        from . import db

        created = db.provision_demo_employees(
            conn, [employee["employee_id"] for employee in refreshed["employees"]]
        )
        result["demo_accounts_created"] = len(created)
        if actor is not None:
            db.audit(conn, actor, "import_committed", plan["payload_digest"][:16], _json(result["counts"]))
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def seed(conn: sqlite3.Connection, dataset_source: str | Path) -> dict[str, Any]:
    """Seed an empty SQLite database exactly once from the original dataset."""
    if _meta(conn, "seed_complete") == "1":
        # Databases created before version snapshots retain their current state.
        # Earlier snapshots cannot be reconstructed, but future updates are kept.
        conn.execute("BEGIN IMMEDIATE")
        try:
            recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn.execute(
                """INSERT OR IGNORE INTO employee_versions(employee_id,version,data,created_at)
                SELECT employee_id,version,data,? FROM employees""",
                (recorded_at,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {"already_seeded": True}
    dataset = load_dataset(dataset_source)
    _validate_catalog(dataset)
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table in ("skills", "role_profiles", "events", "employees", "history"):
            if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise ImportConflict("Refusing to seed a non-empty dataset without seed_complete marker")
        as_of = dataset["meta"]["as_of_date"]
        conn.executemany(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            (("as_of_date", as_of),
             ("dataset_version", str(dataset["meta"].get("version", ""))),
             ("catalog_version", str(dataset["meta"].get("version", ""))),
             ("proficiency_scale", _json(dataset["proficiency_scale"]))),
        )
        conn.executemany(
            "INSERT INTO skills(skill_id,data) VALUES(?,?)",
            ((skill["skill_id"], _json(skill)) for skill in dataset["skills"]),
        )
        conn.executemany(
            "INSERT INTO role_profiles(role,grade,data) VALUES(?,?,?)",
            ((profile["role"], profile["grade"], _json(profile)) for profile in dataset["role_profiles"]),
        )
        conn.executemany(
            "INSERT INTO events(event_id,data) VALUES(?,?)",
            ((event["event_id"], _json(event)) for event in dataset["events"]),
        )
        initial = _classify(conn, dataset["employees"], list(enumerate(dataset["history"], 2)))
        if not initial["ok"]:
            raise DatasetError("Invalid seed data: " + _json(initial["errors"][:30]))
        result = _commit_plan(conn, initial, confirm_updates=False)
        conn.execute("INSERT INTO meta(key,value) VALUES('seed_complete','1')")
        conn.commit()
        return {"already_seeded": False, **result}
    except Exception:
        conn.rollback()
        raise
