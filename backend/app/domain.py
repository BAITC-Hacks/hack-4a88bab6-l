from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import date, timedelta

from .db import dumps, loads, meta


GRADES = ("Junior", "Middle", "Senior", "Lead")
REPEATABLE_EVENT = "EV_036"


def catalog(conn: sqlite3.Connection):
    events = {r["event_id"]: loads(r["data"]) for r in conn.execute("SELECT * FROM events")}
    skills = {r["skill_id"]: loads(r["data"]) for r in conn.execute("SELECT * FROM skills")}
    profiles = {(r["role"], r["grade"]): loads(r["data"]) for r in conn.execute("SELECT * FROM role_profiles")}
    return events, skills, profiles


def employee(conn: sqlite3.Connection, employee_id: str):
    row = conn.execute("SELECT * FROM employees WHERE employee_id=?", (employee_id,)).fetchone()
    return (loads(row["data"]), row["version"]) if row else (None, None)


def goal_for(conn: sqlite3.Connection, person: dict, profiles: dict):
    row = conn.execute("SELECT role,grade FROM goals WHERE employee_id=?", (person["employee_id"],)).fetchone()
    if row:
        role, grade, source = row["role"], row["grade"], "explicit"
    elif person.get("career_goal"):
        role, grade = person["career_goal"]["target_role"], person["career_goal"]["target_grade"]
        source = "explicit"
    elif person["grade"] != "Lead":
        role, grade = person["role"], GRADES[GRADES.index(person["grade"]) + 1]
        source = "suggested_next_grade"
    else:
        role, grade, source = person["role"], "Lead", "current_role_development"
    return {"role": role, "grade": grade, "source": source, "profile": profiles.get((role, grade), {})}


def apply_event(levels: dict[str, int], event: dict) -> list[dict]:
    changes = []
    for item in event["develops_skills"]:
        skill_id = item["skill_id"]
        before = levels.get(skill_id, 0)
        gain = item["gain"]
        ceiling = item["max_level"]
        if not isinstance(before, int) or not 0 <= before <= 5 or not isinstance(gain, int) or gain < 0 or not isinstance(ceiling, int) or not 0 <= ceiling <= 5:
            raise ValueError(f"Invalid skill level or gain for {skill_id}")
        after = max(before, min(5, before + gain, ceiling))
        if after > before:
            levels[skill_id] = after
            changes.append({"skill_id": skill_id, "before": before, "after": after, "gain": after - before})
    return changes


def import_completion_shadowed(record, app_completions: list, events: dict) -> bool:
    """Resolve CSV/app identity where CSV has no completion timestamp or UUID.

    An ordinary course is completed once per person. Repeating clubs and annual
    compliance retain distinct sessions. Original source rows remain unchanged.
    """
    if record["status"] != "completed":
        return False
    event = events[record["event_id"]]
    ordinary = event["event_id"] != REPEATABLE_EVENT and (not event["mandatory"] or event["type"] == "onboarding")
    for app_row in app_completions:
        if app_row["employee_id"] != record["employee_id"] or app_row["event_id"] != record["event_id"]:
            continue
        if ordinary or app_row["linked_record_id"] == record["record_id"] or record["date"] in {app_row["completion_date"], app_row["session_date"]}:
            return True
    return False


def replay(conn: sqlite3.Connection, person: dict, events: dict):
    levels = dict(person["skills"])
    review = person["last_review_date"]
    as_of = meta(conn, "as_of_date")
    if not as_of:
        raise ValueError("Missing business as_of_date")
    completions = []
    all_app_rows = list(conn.execute("SELECT * FROM app_participations WHERE employee_id=? AND status='completed'", (person["employee_id"],)))
    app_rows = [r for r in all_app_rows if r["covered_by_review_version"] is None and r["completion_date"] <= as_of]
    # A later import may describe the same app completion. The source format has
    # no participation UUID, so matching employee/event/business date is our
    # explicit deduplication key for that ambiguity.
    for row in conn.execute("SELECT * FROM history WHERE employee_id=? AND status='completed' AND date>? AND date<=?", (person["employee_id"], review, as_of)):
        if import_completion_shadowed(row, all_app_rows, events):
            continue
        completions.append((row["date"], 0, row["record_id"], row["event_id"], "imported"))
    for row in app_rows:
        # New actions remain in replay even when performed on the review's calendar day.
        completions.append((row["completion_date"], 1, row["completed_at"] or row["id"], row["event_id"], "app"))
    completions.sort()
    dynamics = [{"date": review, "skills": dict(levels), "source": "last_review"}]
    for completed_date, _, identity, event_id, source in completions:
        event = events.get(event_id)
        if not event:
            continue
        changes = apply_event(levels, event)
        if changes:
            dynamics.append({"date": completed_date, "skills": dict(levels), "source": source, "record_id": identity})
    return levels, dynamics


def skill_metrics(levels: dict, goal: dict, skills: dict):
    profile = goal["profile"]
    required = profile.get("required_skills", {})
    critical = set(profile.get("critical_skills", []))
    rows, total, covered, critical_gaps = [], 0, 0, 0
    for skill_id, needed in required.items():
        if needed <= 0:
            continue
        current = levels.get(skill_id, 0)
        gap = max(0, needed - current)
        total += needed
        covered += min(current, needed)
        if gap and skill_id in critical:
            critical_gaps += 1
        definition = skills.get(skill_id, {})
        rows.append({"skill_id": skill_id, "name": definition.get("name", skill_id), "type": definition.get("type", "hard"), "current": current, "required": needed, "gap": gap, "critical": skill_id in critical})
    rows.sort(key=lambda r: (-int(r["critical"]), -r["gap"], r["name"]))
    return rows, (round(100 * covered / total, 1) if total else None), critical_gaps


def history_for(conn: sqlite3.Connection, employee_id: str, events: dict):
    app_by_link = {r["linked_record_id"]: r for r in conn.execute("SELECT * FROM app_participations WHERE employee_id=? AND linked_record_id IS NOT NULL", (employee_id,))}
    app_completions = list(conn.execute("SELECT * FROM app_participations WHERE employee_id=? AND status='completed'", (employee_id,)))
    output = []
    for row in conn.execute("SELECT * FROM history WHERE employee_id=? ORDER BY date DESC,record_id DESC", (employee_id,)):
        source = loads(row["data"])
        update = app_by_link.get(row["record_id"])
        if not update and import_completion_shadowed(row, app_completions, events):
            continue
        event = events.get(row["event_id"], {})
        output.append({"id": row["record_id"], "event_id": row["event_id"], "title": event.get("title", row["event_id"]), "date": update["completion_date"] if update and update["status"] == "completed" else row["date"], "status": update["status"] if update else row["status"], "completion_pct": 100 if update and update["status"] == "completed" else source.get("completion_pct"), "source": "imported", "is_simulated": bool(update["is_simulated"]) if update else False, "mandatory": bool(event.get("mandatory")), "date_meaning": "completion" if update and update["status"] == "completed" else ("enrollment_or_assignment" if event.get("format") == "self_paced" else "session"), "linked_participation_id": update["id"] if update else None})
        output[-1].update({"format": event.get("format"), "skill_ids": [d["skill_id"] for d in event.get("develops_skills", [])], "score": source.get("score"), "feedback_rating": source.get("feedback_rating"), "assigned_by": source.get("assigned_by")})
    for row in conn.execute("SELECT * FROM app_participations WHERE employee_id=? AND linked_record_id IS NULL ORDER BY created_at DESC", (employee_id,)):
        event = events.get(row["event_id"], {})
        output.append({"id": row["id"], "event_id": row["event_id"], "title": event.get("title", row["event_id"]), "date": row["completion_date"] or row["session_date"] or row["created_at"][:10], "status": row["status"], "completion_pct": 100 if row["status"] == "completed" else 0, "source": "app", "is_simulated": bool(row["is_simulated"]), "mandatory": bool(event.get("mandatory")), "date_meaning": "completion" if row["status"] == "completed" else "planned_or_started", "linked_participation_id": row["id"]})
        output[-1].update({"format": event.get("format"), "skill_ids": [d["skill_id"] for d in event.get("develops_skills", [])], "score": None, "feedback_rating": None, "assigned_by": "self"})
    output.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
    return output


def state_rows(conn: sqlite3.Connection, table: str, employee_id: str):
    if table not in {"skips", "archives"}:
        raise ValueError("Invalid state table")
    return [{"event_id": row["event_id"], "reason": row["reason"], "updated_at": row["updated_at"], "title": loads(row["data"])["title"]} for row in conn.execute(f"SELECT s.event_id,s.reason,s.updated_at,e.data FROM {table} s JOIN events e ON e.event_id=s.event_id WHERE s.employee_id=? AND s.active=1", (employee_id,))]


def activity_calendar(history: list[dict], as_of: str):
    last = date.fromisoformat(as_of)
    first = last - timedelta(days=364)
    days: dict[str, dict] = {}
    app_keys = {(item["event_id"], item["date"]) for item in history if item["source"] == "app" and item["status"] == "completed"}
    for item in history:
        if item["mandatory"] or item["status"] != "completed":
            continue
        day = date.fromisoformat(item["date"])
        if not first <= day <= last:
            continue
        if item["source"] == "imported" and (item["event_id"], item["date"]) in app_keys:
            continue
        value = days.setdefault(item["date"], {"date": item["date"], "count": 0, "estimated_count": 0, "simulated_count": 0})
        value["count"] += 1
        value["estimated_count"] += int(item["date_meaning"] == "enrollment_or_assignment")
        value["simulated_count"] += int(item["is_simulated"])
    return {"from": first.isoformat(), "to": last.isoformat(), "total": sum(value["count"] for value in days.values()), "days": sorted(days.values(), key=lambda value: value["date"])}


def profile(conn: sqlite3.Connection, employee_id: str):
    person, person_version = employee(conn, employee_id)
    if not person:
        return None
    events, skills, profiles = catalog(conn)
    goal = goal_for(conn, person, profiles)
    levels, dynamics = replay(conn, person, events)
    metrics, coverage, critical_gaps = skill_metrics(levels, goal, skills)
    participations = [dict(r) for r in conn.execute("SELECT id,event_id,status,session_date,is_simulated,linked_record_id FROM app_participations WHERE employee_id=? ORDER BY created_at DESC", (employee_id,))]
    for item in participations:
        item["title"] = events.get(item["event_id"], {}).get("title", item["event_id"])
        item["is_simulated"] = bool(item["is_simulated"])
    history = history_for(conn, employee_id, events)
    as_of = meta(conn, "as_of_date")
    return {"employee": {key: person[key] for key in ("employee_id", "full_name", "department", "role", "grade", "preferred_language", "last_review_date")}, "profile_version": person_version, "as_of_date": as_of, "goal": {k: goal[k] for k in ("role", "grade", "source")}, "coverage_pct": coverage, "critical_gaps": critical_gaps, "skills": metrics, "dynamics": dynamics, "history": history, "activity_calendar": activity_calendar(history, as_of), "participations": participations, "skips": state_rows(conn, "skips", employee_id), "archives": state_rows(conn, "archives", employee_id)}


def _completed_dates(conn: sqlite3.Connection, employee_id: str, event_id: str):
    dates = {r["date"] for r in conn.execute("SELECT date FROM history WHERE employee_id=? AND event_id=? AND status='completed'", (employee_id, event_id))}
    dates.update(r["session_date"] for r in conn.execute("SELECT session_date FROM app_participations WHERE employee_id=? AND event_id=? AND status='completed'", (employee_id, event_id)))
    return dates


def _ongoing(conn: sqlite3.Connection, employee_id: str, event_id: str):
    row = conn.execute("SELECT id,session_date,status FROM app_participations WHERE employee_id=? AND event_id=? AND status IN ('planned','in_progress') ORDER BY created_at DESC LIMIT 1", (employee_id, event_id)).fetchone()
    if row:
        return {"id": row["id"], "session_date": row["session_date"], "status": row["status"]}
    row = conn.execute("SELECT record_id,date FROM history WHERE employee_id=? AND event_id=? AND status='in_progress' ORDER BY date DESC LIMIT 1", (employee_id, event_id)).fetchone()
    if row:
        linked = conn.execute("SELECT 1 FROM app_participations WHERE linked_record_id=?", (row["record_id"],)).fetchone()
        if not linked:
            return {"id": "history:" + row["record_id"], "session_date": row["date"], "status": "in_progress"}
    return None


def candidate_info(conn: sqlite3.Connection, employee_id: str):
    person, _ = employee(conn, employee_id)
    if not person:
        return None
    events, skills, profiles = catalog(conn)
    goal = goal_for(conn, person, profiles)
    levels, _ = replay(conn, person, events)
    metrics, coverage, _ = skill_metrics(levels, goal, skills)
    required = goal["profile"].get("required_skills", {})
    skipped = {r["event_id"] for r in state_rows(conn, "skips", employee_id)}
    archived = {r["event_id"] for r in state_rows(conn, "archives", employee_id)}
    as_of = meta(conn, "as_of_date")
    candidates, exclusions = [], Counter()
    for event_id, event in events.items():
        reason = None
        ongoing = _ongoing(conn, employee_id, event_id)
        if event["mandatory"]:
            reason = "mandatory"
        elif not ongoing and (person["role"] not in event["target_roles"] or person["grade"] not in event["target_grades"]):
            reason = "role_or_grade"
        elif not ongoing and any(levels.get(s, 0) < needed for s, needed in event["prerequisites"].items()):
            reason = "prerequisites"
        elif not ongoing and event_id != REPEATABLE_EVENT and _completed_dates(conn, employee_id, event_id):
            reason = "completed"
        if reason:
            exclusions[reason] += 1
            continue
        session_date = ongoing["session_date"] if ongoing else None
        if not ongoing and event["format"] != "self_paced":
            completed_dates = _completed_dates(conn, employee_id, event_id) if event_id == REPEATABLE_EVENT else set()
            future = sorted(d for d in event["upcoming_sessions"] if d >= as_of and d not in completed_dates)
            if not future:
                exclusions["no_session"] += 1
                continue
            session_date = future[0]
        after = dict(levels)
        changes = apply_event(after, event)
        useful = []
        for change in changes:
            skill_id = change["skill_id"]
            needed = required.get(skill_id, 0)
            if needed > change["before"]:
                useful.append({"skill_id": skill_id, "name": skills[skill_id]["name"], "current": change["before"], "after": change["after"], "required": needed, "critical": skill_id in goal["profile"].get("critical_skills", [])})
        if not useful:
            exclusions["no_useful_gain"] += 1
            continue
        if not ongoing and event_id in skipped:
            exclusions["skipped"] += 1
            continue
        if not ongoing and event_id in archived:
            exclusions["archived"] += 1
            continue
        candidates.append({"event_id": event_id, "title": event["title"], "description": event["description"], "type": event["type"], "format": event["format"], "duration_hours": event["duration_hours"], "session_date": session_date, "action": "continue" if ongoing else "start", "participation_id": ongoing["id"] if ongoing else None, "participation_status": ongoing["status"] if ongoing else None, "prerequisites": event["prerequisites"], "develops": useful})
    return {"person": person, "goal": goal, "levels": levels, "metrics": metrics, "coverage_pct": coverage, "candidates": candidates, "exclusions": dict(exclusions), "events": events, "skills": skills}


def recommendation_version(conn: sqlite3.Connection, employee_id: str):
    person, person_version = employee(conn, employee_id)
    if not person:
        return None
    data = {"employee": person, "profile_version": person_version, "goal": [dict(r) for r in conn.execute("SELECT * FROM goals WHERE employee_id=?", (employee_id,))], "history": [dict(r) for r in conn.execute("SELECT * FROM history WHERE employee_id=? ORDER BY record_id", (employee_id,))], "participations": [dict(r) for r in conn.execute("SELECT * FROM app_participations WHERE employee_id=? ORDER BY id", (employee_id,))], "skips": [dict(r) for r in conn.execute("SELECT * FROM skips WHERE employee_id=? ORDER BY event_id", (employee_id,))], "archives": [dict(r) for r in conn.execute("SELECT * FROM archives WHERE employee_id=? ORDER BY event_id", (employee_id,))], "as_of_date": meta(conn, "as_of_date"), "catalog_version": meta(conn, "catalog_version"), "rules": 4, "model": __import__("os").getenv("OPENAI_MODEL", "gpt-4.1-mini")}
    return hashlib.sha256(dumps(data).encode()).hexdigest()[:24]


def availability_state(conn: sqlite3.Connection, employee_id: str, info: dict | None = None):
    info = info if info is not None else candidate_info(conn, employee_id)
    if info is None:
        return None
    goal = info["goal"]
    if info["coverage_pct"] == 100:
        state = "requirements_covered"
    elif info["candidates"]:
        row = conn.execute("SELECT version,source,status,calculated_at FROM recommendations WHERE employee_id=?", (employee_id,)).fetchone()
        version = recommendation_version(conn, employee_id)
        state = "recommended" if row and row["version"] == version and row["status"] == "ready" else ("ai_error" if row and row["version"] == version and row["status"] == "fallback" else "not_requested_or_stale")
    elif info["exclusions"].get("skipped"):
        state = "skipped"
    elif info["exclusions"].get("archived"):
        state = "archived"
    else:
        state = "no_eligible_candidates"
    return {"state": state, "candidate_count": len(info["candidates"]), "goal_source": goal["source"]}
