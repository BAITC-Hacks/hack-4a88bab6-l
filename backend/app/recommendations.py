from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .db import dumps, loads, transaction, utc_now
from .domain import candidate_info, history_for, recommendation_version


class ModelChoice(BaseModel):
    event_id: str
    action: Literal["start", "continue"]
    rationale: str
    evidence_ids: list[str]
    tradeoff: str | None


class ModelSelection(BaseModel):
    recommendations: list[ModelChoice]
    limitations: list[str]


_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}
_last_attempt: dict[str, float] = {}

STATUS_LABELS = {
    "completed": "завершено", "in_progress": "в работе", "planned": "запланировано",
    "dropped": "прервано", "no_show": "неявка", "declined": "отклонено", "overdue": "просрочено",
}
FORMAT_LABELS = {"online": "онлайн", "offline": "очно", "self_paced": "самостоятельно"}
GOAL_SOURCE_LABELS = {"explicit": "выбрана сотрудником", "suggested_next_grade": "предложена по следующему грейду", "current_role_development": "развитие в текущей роли"}


def generation_lock(employee_id: str):
    with _locks_guard:
        if employee_id not in _locks:
            _locks[employee_id] = threading.Lock()
        return _locks[employee_id]


def evidence_pool(info: dict, history: list[dict]):
    pool = {}
    goal = info["goal"]
    pool["goal"] = {"type": "goal", "label": f"Цель: {goal['role']}, {goal['grade']} ({GOAL_SOURCE_LABELS.get(goal['source'], 'текущая цель')})", "source": "career_goal / role_profiles", "event_id": None}
    person = info["person"]
    pool["current_role"] = {"type": "eligibility", "label": f"Текущая роль и грейд: {person['role']} / {person['grade']}", "source": "employees.role, employees.grade", "event_id": None}
    recent = history[:12]
    counts = Counter(r["status"] for r in history)
    pool["history_summary"] = {"type": "history", "label": "История участия: " + (", ".join(f"{STATUS_LABELS.get(k, 'другой статус')}: {v}" for k, v in sorted(counts.items())) if counts else "записей пока нет"), "source": "activity_history.csv + app_participations", "event_id": None}
    for record in recent:
        pool[f"history:{record['id']}"] = {"type": "history", "label": f"«{record['title']}» ({record['date']}): {STATUS_LABELS.get(record['status'], 'статус не указан')}", "source": f"history/{record['id']}", "event_id": None}
    for candidate in info["candidates"]:
        event_id = candidate["event_id"]
        pool[f"format:{event_id}"] = {"type": "format", "label": f"{FORMAT_LABELS.get(candidate['format'], 'Формат уточняется')}, {candidate['duration_hours']} ч; {candidate['session_date'] or 'в своём темпе'}", "source": f"events/{event_id}", "event_id": event_id}
        related_skills = {change["skill_id"] for change in candidate["develops"]}
        related_history = [record for record in history if record["event_id"] == event_id or related_skills.intersection(record.get("skill_ids", []))]
        completed = next((record for record in related_history if record["status"] == "completed"), None)
        if completed:
            history_label = f"По связанным навыкам ранее завершено «{completed['title']}» ({completed['date']})."
        elif related_history:
            record = related_history[0]
            history_label = f"По связанным навыкам есть запись «{record['title']}» ({record['date']}): {STATUS_LABELS.get(record['status'], 'статус не указан')}; завершений пока нет."
        else:
            history_label = "По навыкам этого шага в истории пока нет записей об участии."
        pool[f"history_context:{event_id}"] = {"type": "history", "label": history_label, "source": "activity_history.csv + app_participations", "event_id": event_id}
        action_label = "Уже запланированная сессия" if candidate.get("participation_status") == "planned" else ("Продолжение начатого обучения" if candidate["action"] == "continue" else "Доступное добровольное мероприятие")
        pool[f"action:{event_id}"] = {"type": "action", "label": action_label, "source": f"eligibility/{event_id}", "event_id": event_id}
        for change in candidate["develops"]:
            skill_id = change["skill_id"]
            pool[f"gap:{event_id}:{skill_id}"] = {"type": "gap", "label": f"{change['name']}: сейчас {change['current']}, требуется {change['required']}; разрыв {max(0, change['required'] - change['current'])}" + ("; критический" if change["critical"] else ""), "source": f"role_profiles/{goal['role']}/{goal['grade']}/{skill_id}", "event_id": event_id}
            pool[f"effect:{event_id}:{skill_id}"] = {"type": "effect", "label": f"{change['name']}: расчётный прирост {change['current']} → {change['after']} при требовании {change['required']}", "source": f"events/{event_id}/develops_skills/{skill_id} + replay", "event_id": event_id}
    return pool


def model_context(info: dict, history: list[dict], pool: dict):
    person = info["person"]
    by_format = {}
    by_skill = {}
    for item in history:
        fmt = item.get("format") or "unknown"
        by_format.setdefault(fmt, Counter())[item["status"]] += 1
        for skill_id in item.get("skill_ids", []):
            by_skill.setdefault(skill_id, Counter())[item["status"]] += 1
    return {"current_role": person["role"], "current_grade": person["grade"], "target_role": info["goal"]["role"], "target_grade": info["goal"]["grade"], "target_source": info["goal"]["source"], "required_skills": info["metrics"], "history": [{"record_id": r["id"], **{key: r.get(key) for key in ("event_id", "title", "status", "date", "format", "skill_ids", "score", "feedback_rating")}} for r in history[:40]], "history_by_format": by_format, "history_by_skill": by_skill, "eligible_candidates": info["candidates"], "excluded_alternative_counts": info["exclusions"], "evidence_pool": {key: {"type": value["type"], "label": value["label"]} for key, value in pool.items()}}


def _fallback_choices(info: dict, pool: dict):
    def rank(candidate):
        critical = sum(max(0, min(d["after"], d["required"]) - min(d["current"], d["required"])) for d in candidate["develops"] if d["critical"])
        gain = sum(max(0, min(d["after"], d["required"]) - min(d["current"], d["required"])) for d in candidate["develops"])
        return (-critical, -gain, candidate["action"] != "continue", candidate["duration_hours"], candidate["event_id"])
    return [(candidate, ["goal", f"gap:{candidate['event_id']}:{candidate['develops'][0]['skill_id']}", f"history_context:{candidate['event_id']}", f"effect:{candidate['event_id']}:{candidate['develops'][0]['skill_id']}", f"format:{candidate['event_id']}"]) for candidate in sorted(info["candidates"], key=rank)[:3]]


def _validate_selection(selection: ModelSelection, info: dict, pool: dict):
    by_id = {c["event_id"]: c for c in info["candidates"]}
    if not 1 <= len(selection.recommendations) <= min(3, len(by_id)):
        raise ValueError("Invalid recommendation count")
    seen = set()
    choices = []
    for item in selection.recommendations:
        if item.event_id not in by_id or item.event_id in seen:
            raise ValueError("Unknown or duplicate event_id")
        seen.add(item.event_id)
        candidate = by_id[item.event_id]
        if item.action != candidate["action"]:
            raise ValueError("Invalid recommendation action")
        if len(item.evidence_ids) < 3:
            raise ValueError("Too little evidence")
        facts = []
        ids = list(dict.fromkeys(item.evidence_ids))
        for evidence_id in ids:
            fact = pool.get(evidence_id)
            if not fact or fact["event_id"] not in (None, item.event_id):
                raise ValueError("Unknown or unrelated evidence_id")
            facts.append(fact)
        history_id = f"history_context:{item.event_id}"
        if history_id in pool and history_id not in ids:
            ids.append(history_id)
            facts.append(pool[history_id])
        types = {f["type"] for f in facts}
        if not {"goal", "gap", "history"}.issubset(types):
            raise ValueError("Evidence does not cover three factors")
        choices.append((candidate, ids))
    return choices


def _render(choices: list[tuple[dict, list[str]]], pool: dict, source: str, goal: dict):
    result = []
    for candidate, ids in choices:
        facts = [{"id": evidence_id, "label": pool[evidence_id]["label"], "source": pool[evidence_id]["source"]} for evidence_id in ids]
        # The model selects events and evidence; the final prose uses only verified facts.
        develops = candidate["develops"]
        names = " и ".join(f"«{change['name']}»" for change in develops[:2])
        if len(develops) > 2:
            names += " и другие навыки"
        leading = develops[0]
        rationale = f"Этот шаг поможет развить {names} и приблизиться к требованиям уровня {goal['grade']}. "
        if leading["after"] > leading["current"]:
            rationale += f"После завершения ожидается рост навыка «{leading['name']}» с {leading['current']} до {leading['after']} при цели {leading['required']}. "
        else:
            rationale += f"По текущему расчёту уровень навыка «{leading['name']}» остаётся {leading['current']} при цели {leading['required']}. "
        if leading["critical"]:
            rationale += "Этот навык критичен для выбранной цели. "
        rationale += pool[f"history_context:{candidate['event_id']}"]["label"]
        result.append({**candidate, "factors": facts, "rationale": rationale, "tradeoff": "Прирост расчётный; окончательное решение об участии остаётся за сотрудником."})
    return result


def _call_openai(info: dict, history: list[dict], pool: dict, model: str):
    from openai import OpenAI

    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("OpenAI key is not configured")
    prompt = (Path(__file__).parent / "ai_system_prompt.txt").read_text(encoding="utf-8")
    client = OpenAI(api_key=key, timeout=8.0, max_retries=0)
    kwargs = {}
    if model.startswith("gpt-5.6-"):
        kwargs["reasoning"] = {"effort": "none"}
    response = client.responses.parse(model=model, instructions=prompt + "\nОтвет должен быть кратким: rationale не длиннее 12 слов, tradeoff=null, limitations=[]; выбери 3–5 evidence_ids. На каждую рекомендацию обязательны факторы gap, history и goal либо eligibility.", input=[{"role": "user", "content": dumps(model_context(info, history, pool))}], text_format=ModelSelection, max_output_tokens=900, store=False, **kwargs)
    if response.status != "completed" or response.output_parsed is None:
        raise ValueError("OpenAI returned incomplete, refused, or unparsed response")
    return response.output_parsed


def current_recommendations(conn: sqlite3.Connection, employee_id: str):
    version = recommendation_version(conn, employee_id)
    row = conn.execute("SELECT * FROM recommendations WHERE employee_id=?", (employee_id,)).fetchone()
    if row and row["version"] == version:
        return {"source": row["source"], "version": version, "calculated_at": row["calculated_at"], "status": row["status"], "items": loads(row["data"], [])}
    return {"source": None, "version": version, "calculated_at": None, "status": "stale" if row else "not_requested", "items": []}


def generate(conn: sqlite3.Connection, employee_id: str, force: bool = False):
    lock = generation_lock(employee_id)
    if not lock.acquire(blocking=False):
        raise RuntimeError("A recommendation request is already running")
    try:
        before = recommendation_version(conn, employee_id)
        old = current_recommendations(conn, employee_id)
        if not force and old["status"] in {"ready", "no_candidates"}:
            return old
        attempt_key = f"{employee_id}:{before}"
        elapsed = time.monotonic() - _last_attempt.get(attempt_key, -1000)
        if elapsed < 10:
            if old["status"] == "fallback":
                return old
            raise RuntimeError("Подождите несколько секунд перед повторным запросом")
        _last_attempt[attempt_key] = time.monotonic()
        info = candidate_info(conn, employee_id)
        if info is None:
            return None
        history = history_for(conn, employee_id, info["events"])
        pool = evidence_pool(info, history)
        source, status = None, "no_candidates"
        choices = []
        if info["candidates"]:
            model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
            try:
                selection = _call_openai(info, history, pool, model)
                choices = _validate_selection(selection, info, pool)
                source, status = "ai", "ready"
            except Exception:
                choices = _fallback_choices(info, pool)
                source, status = "fallback", "fallback"
        result = _render(choices, pool, source, info["goal"])
        calculated_at = utc_now()
        with transaction(conn):
            if recommendation_version(conn, employee_id) != before:
                raise RuntimeError("Profile changed during generation; request again")
            conn.execute("INSERT INTO recommendations(employee_id,version,source,status,calculated_at,data) VALUES(?,?,?,?,?,?) ON CONFLICT(employee_id) DO UPDATE SET version=excluded.version,source=excluded.source,status=excluded.status,calculated_at=excluded.calculated_at,data=excluded.data", (employee_id, before, source, status, calculated_at, dumps(result)))
            conn.execute("INSERT INTO recommendation_runs(employee_id,version,source,status,calculated_at,data) VALUES(?,?,?,?,?,?)", (employee_id, before, source, status, calculated_at, dumps(result)))
        return {"source": source, "version": before, "calculated_at": calculated_at, "status": status, "items": result}
    finally:
        lock.release()
