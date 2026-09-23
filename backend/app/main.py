from __future__ import annotations

import hashlib
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import domain
from .db import DEMO_MODE, audit, check_password, connection, init_db, loads, meta, transaction, utc_now
from .recommendations import current_recommendations, generate


app = FastAPI(title="Career Quest API", docs_url="/api/docs", openapi_url="/api/openapi.json")
_previews: dict[str, tuple[str, float, dict]] = {}
MAX_UPLOAD = 5 * 1024 * 1024


@app.on_event("startup")
def startup():
    init_db()


class LoginInput(BaseModel):
    email: str
    password: str


class GoalInput(BaseModel):
    role: str
    grade: str


class ReasonInput(BaseModel):
    reason: str | None = None


class StartInput(BaseModel):
    event_id: str
    session_date: str | None = None


class CompleteInput(BaseModel):
    simulate: bool = False


class ImportConfirm(BaseModel):
    token: str
    confirm_updates: bool = False
    cover_same_day_completions: bool = False


def _check_origin(request: Request):
    origin = request.headers.get("origin")
    if not origin:
        return
    own = f"{request.url.scheme}://{request.headers.get('host', '')}"
    allowed = {own, *[x.strip() for x in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if x.strip()]}
    if origin not in allowed:
        raise HTTPException(403, "Недопустимый Origin")


@app.middleware("http")
async def origin_middleware(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
        try:
            _check_origin(request)
        except HTTPException as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return await call_next(request)


def session_user(request: Request):
    token = request.cookies.get("cq_session")
    if not token:
        raise HTTPException(401, "Требуется вход")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with connection() as conn:
        row = conn.execute("SELECT s.csrf,s.expires_at,u.email,u.role,u.employee_id FROM sessions s JOIN users u ON u.email=s.email WHERE s.token_hash=?", (token_hash,)).fetchone()
    if not row or row["expires_at"] <= utc_now():
        raise HTTPException(401, "Сессия истекла")
    if not DEMO_MODE and row["email"].endswith("@careerquest.test"):
        raise HTTPException(401, "Демонстрационные аккаунты отключены")
    return dict(row)


def csrf_user(request: Request, user=Depends(session_user)):
    if request.headers.get("X-CSRF-Token") != user["csrf"]:
        raise HTTPException(403, "Недействительный CSRF токен")
    return user


def employee_user(user=Depends(session_user)):
    if user["role"] != "employee" or not user["employee_id"]:
        raise HTTPException(403, "Только для сотрудника")
    return user


def employee_write(request: Request, user=Depends(csrf_user)):
    if user["role"] != "employee" or not user["employee_id"]:
        raise HTTPException(403, "Только для сотрудника")
    return user


def hr_user(user=Depends(session_user)):
    if user["role"] != "hr":
        raise HTTPException(403, "Только для HR")
    return user


def hr_write(request: Request, user=Depends(csrf_user)):
    if user["role"] != "hr":
        raise HTTPException(403, "Только для HR")
    return user


def _profile_with_recommendations(conn, employee_id: str):
    result = domain.profile(conn, employee_id)
    if result is None:
        raise HTTPException(404, "Сотрудник не найден")
    result["demo_mode"] = DEMO_MODE
    result["recommendations"] = current_recommendations(conn, employee_id)
    info = domain.candidate_info(conn, employee_id)
    result["availability"] = domain.availability_state(conn, employee_id, info)
    result["exclusions"] = info["exclusions"]
    return result


def _require_event(conn, event_id: str):
    row = conn.execute("SELECT data FROM events WHERE event_id=?", (event_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Мероприятие не найдено")
    return loads(row["data"])


@app.post("/api/login")
def login(body: LoginInput, response: Response, request: Request):
    if not DEMO_MODE and body.email.lower().strip().endswith("@careerquest.test"):
        raise HTTPException(401, "Демонстрационные аккаунты отключены")
    with connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", (body.email.lower().strip(),)).fetchone()
        if not row or not check_password(body.password, row["password_hash"]):
            raise HTTPException(401, "Неверный email или пароль")
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        expires = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(timespec="seconds")
        with transaction(conn):
            conn.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), row["email"], csrf, expires))
            audit(conn, row["email"], "login", row["email"])
    secure = request.url.scheme == "https" or os.getenv("COOKIE_SECURE", "false").lower() == "true"
    response.set_cookie("cq_session", token, max_age=12 * 3600, httponly=True, secure=secure, samesite="strict", path="/")
    return {"role": row["role"], "employee_id": row["employee_id"], "demo_mode": DEMO_MODE}


@app.post("/api/logout")
def logout(response: Response, request: Request, user=Depends(csrf_user)):
    token = request.cookies.get("cq_session", "")
    with connection() as conn, transaction(conn):
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
        audit(conn, user["email"], "logout", user["email"])
    response.delete_cookie("cq_session", path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(session_user)):
    with connection() as conn:
        as_of = meta(conn, "as_of_date")
    return {"role": user["role"], "employee_id": user["employee_id"], "demo_mode": DEMO_MODE, "as_of_date": as_of, "csrf_token": user["csrf"]}


@app.get("/api/role-profiles")
def role_profiles(user=Depends(session_user)):
    with connection() as conn:
        rows = conn.execute("SELECT role,grade FROM role_profiles ORDER BY role,CASE grade WHEN 'Junior' THEN 1 WHEN 'Middle' THEN 2 WHEN 'Senior' THEN 3 ELSE 4 END").fetchall()
    return {"items": [dict(row) for row in rows]}


@app.get("/api/employee/profile")
def own_profile(user=Depends(employee_user)):
    with connection() as conn:
        return _profile_with_recommendations(conn, user["employee_id"])


@app.post("/api/employee/goal")
def set_goal(body: GoalInput, user=Depends(employee_write)):
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM role_profiles WHERE role=? AND grade=?", (body.role, body.grade)).fetchone():
            raise HTTPException(422, "Такого профиля роли и грейда нет")
        with transaction(conn):
            conn.execute("INSERT INTO goals VALUES(?,?,?,?) ON CONFLICT(employee_id) DO UPDATE SET role=excluded.role,grade=excluded.grade,updated_at=excluded.updated_at", (user["employee_id"], body.role, body.grade, utc_now()))
            audit(conn, user["email"], "goal_updated", user["employee_id"], f"{body.role}/{body.grade}")
        return _profile_with_recommendations(conn, user["employee_id"])


@app.get("/api/employee/recommendations")
def own_recommendations(user=Depends(employee_user)):
    with connection() as conn:
        return current_recommendations(conn, user["employee_id"])


def _generate_for(employee_id: str, refresh: bool = False):
    with connection() as conn:
        if not domain.employee(conn, employee_id)[0]:
            raise HTTPException(404, "Сотрудник не найден")
        try:
            return generate(conn, employee_id, force=refresh)
        except RuntimeError as exc:
            raise HTTPException(429 if "Подождите" in str(exc) else 409, str(exc)) from exc


@app.post("/api/employee/recommendations/generate")
def generate_own(user=Depends(employee_write)):
    return _generate_for(user["employee_id"])


def _proposal_exists(conn, employee_id: str, event_id: str):
    current = current_recommendations(conn, employee_id)
    return any(item["event_id"] == event_id for item in current["items"])


@app.post("/api/employee/recommendations/{event_id}/skip")
def skip(event_id: str, body: ReasonInput, user=Depends(employee_write)):
    with connection() as conn:
        with transaction(conn):
            event = _require_event(conn, event_id)
            if event["mandatory"]:
                raise HTTPException(422, "Обязательное мероприятие нельзя пропустить как рекомендацию")
            existing = conn.execute("SELECT active FROM skips WHERE employee_id=? AND event_id=?", (user["employee_id"], event_id)).fetchone()
            if not (existing and existing["active"]) and not _proposal_exists(conn, user["employee_id"], event_id):
                raise HTTPException(422, "Нет активного предложения для пропуска")
            if domain._ongoing(conn, user["employee_id"], event_id):
                raise HTTPException(422, "Для начатого обучения используйте прекращение участия")
            conn.execute("INSERT INTO skips VALUES(?,?,?,?,?) ON CONFLICT(employee_id,event_id) DO UPDATE SET reason=excluded.reason,active=1,updated_at=excluded.updated_at", (user["employee_id"], event_id, body.reason, 1, utc_now()))
            audit(conn, user["email"], "employee_skipped", f"{user['employee_id']}/{event_id}", body.reason)
        return _profile_with_recommendations(conn, user["employee_id"])


@app.delete("/api/employee/recommendations/{event_id}/skip")
def restore_skip(event_id: str, user=Depends(employee_write)):
    with connection() as conn:
        row = conn.execute("SELECT active FROM skips WHERE employee_id=? AND event_id=?", (user["employee_id"], event_id)).fetchone()
        if not row:
            raise HTTPException(404, "Пропуск не найден")
        with transaction(conn):
            conn.execute("UPDATE skips SET active=0,updated_at=? WHERE employee_id=? AND event_id=?", (utc_now(), user["employee_id"], event_id))
            audit(conn, user["email"], "employee_skip_restored", f"{user['employee_id']}/{event_id}")
        return _profile_with_recommendations(conn, user["employee_id"])


@app.post("/api/employee/participations")
def start_participation(body: StartInput, user=Depends(employee_write)):
    employee_id = user["employee_id"]
    with connection() as conn, transaction(conn):
        event = _require_event(conn, body.event_id)
        info = domain.candidate_info(conn, employee_id)
        candidate = next((c for c in info["candidates"] if c["event_id"] == body.event_id), None)
        if not candidate:
            raise HTTPException(422, "Мероприятие сейчас недоступно")
        if candidate["action"] == "continue":
            participation_id = candidate["participation_id"]
            if participation_id.startswith("history:"):
                record_id = participation_id.split(":", 1)[1]
                participation_id = str(uuid.uuid4())
                conn.execute("INSERT INTO app_participations(id,employee_id,event_id,linked_record_id,session_date,status,created_at) VALUES(?,?,?,?,?,?,?)", (participation_id, employee_id, body.event_id, record_id, candidate["session_date"], "in_progress", utc_now()))
                audit(conn, user["email"], "participation_continued", participation_id)
            return {"id": participation_id, "status": candidate.get("participation_status") or "in_progress"}
        session_date = None if event["format"] == "self_paced" else (body.session_date or candidate["session_date"])
        if event["format"] != "self_paced" and (session_date not in event["upcoming_sessions"] or session_date < meta(conn, "as_of_date")):
            raise HTTPException(422, "Сессия недоступна")
        if body.event_id == domain.REPEATABLE_EVENT and session_date in domain._completed_dates(conn, employee_id, body.event_id):
            raise HTTPException(422, "Эта сессия уже завершена")
        status = "in_progress" if event["format"] == "self_paced" else "planned"
        participation_id = str(uuid.uuid4())
        conn.execute("INSERT INTO app_participations(id,employee_id,event_id,linked_record_id,session_date,status,created_at) VALUES(?,?,?,?,?,?,?)", (participation_id, employee_id, body.event_id, None, session_date, status, utc_now()))
        audit(conn, user["email"], "participation_started" if status == "in_progress" else "participation_planned", participation_id)
        return {"id": participation_id, "status": status}


def _resolve_participation(conn, employee_id: str, participation_id: str):
    if participation_id.startswith("history:"):
        record_id = participation_id.split(":", 1)[1]
        record = conn.execute("SELECT * FROM history WHERE record_id=? AND employee_id=? AND status='in_progress'", (record_id, employee_id)).fetchone()
        if not record:
            raise HTTPException(404, "Участие не найдено")
        existing = conn.execute("SELECT * FROM app_participations WHERE linked_record_id=?", (record_id,)).fetchone()
        if existing:
            return existing
        new_id = str(uuid.uuid4())
        conn.execute("INSERT INTO app_participations(id,employee_id,event_id,linked_record_id,session_date,status,created_at) VALUES(?,?,?,?,?,?,?)", (new_id, employee_id, record["event_id"], record_id, record["date"], "in_progress", utc_now()))
        return conn.execute("SELECT * FROM app_participations WHERE id=?", (new_id,)).fetchone()
    row = conn.execute("SELECT * FROM app_participations WHERE id=? AND employee_id=?", (participation_id, employee_id)).fetchone()
    if not row:
        raise HTTPException(404, "Участие не найдено")
    return row


@app.post("/api/employee/participations/{participation_id}/complete")
def complete_participation(participation_id: str, body: CompleteInput, user=Depends(employee_write)):
    employee_id = user["employee_id"]
    with connection() as conn:
        with transaction(conn):
            row = _resolve_participation(conn, employee_id, participation_id)
            if row["status"] == "completed":
                return _profile_with_recommendations(conn, employee_id)
            if row["status"] not in {"planned", "in_progress"}:
                raise HTTPException(422, "Недопустимый переход состояния")
            session = row["session_date"]
            as_of = meta(conn, "as_of_date")
            future = bool(session and session > as_of)
            if future and (not DEMO_MODE or not body.simulate):
                raise HTTPException(422, "Будущую сессию можно завершить только явной демонстрацией")
            if body.simulate and not DEMO_MODE:
                raise HTTPException(403, "Демонстрация отключена")
            conn.execute("UPDATE app_participations SET status='completed',completion_date=?,completed_at=?,is_simulated=? WHERE id=?", (as_of, utc_now(), int(body.simulate), row["id"]))
            audit(conn, user["email"], "participation_completed", row["id"], "simulated" if body.simulate else None)
        return _profile_with_recommendations(conn, employee_id)


@app.post("/api/employee/participations/{participation_id}/stop")
def stop_participation(participation_id: str, user=Depends(employee_write)):
    with connection() as conn:
        with transaction(conn):
            row = _resolve_participation(conn, user["employee_id"], participation_id)
            if _require_event(conn, row["event_id"])["mandatory"]:
                raise HTTPException(422, "Обязательное назначение нельзя отменить в этом MVP")
            if row["status"] not in {"planned", "in_progress"}:
                raise HTTPException(422, "Недопустимый переход состояния")
            conn.execute("UPDATE app_participations SET status='stopped' WHERE id=?", (row["id"],))
            audit(conn, user["email"], "participation_stopped", row["id"])
        return _profile_with_recommendations(conn, user["employee_id"])


@app.get("/api/hr/employees")
def hr_employees(q: str = "", department: str = "", role: str = "", grade: str = "", user=Depends(hr_user)):
    with connection() as conn:
        items = []
        for row in conn.execute("SELECT employee_id,data FROM employees ORDER BY employee_id"):
            person = loads(row["data"])
            if q and q.casefold() not in (person["full_name"] + " " + person["employee_id"]).casefold():
                continue
            if department and person["department"] != department or role and person["role"] != role or grade and person["grade"] != grade:
                continue
            items.append({key: person[key] for key in ("employee_id", "full_name", "department", "role", "grade")})
        all_people = [loads(r["data"]) for r in conn.execute("SELECT data FROM employees")]
    return {"items": items, "departments": sorted({p["department"] for p in all_people}), "roles": sorted({p["role"] for p in all_people}), "grades": list(domain.GRADES)}


@app.get("/api/hr/employees/{employee_id}")
def hr_profile(employee_id: str, user=Depends(hr_user)):
    with connection() as conn:
        return _profile_with_recommendations(conn, employee_id)


@app.get("/api/hr/employees/{employee_id}/recommendations")
def hr_recommendations(employee_id: str, user=Depends(hr_user)):
    with connection() as conn:
        if not domain.employee(conn, employee_id)[0]:
            raise HTTPException(404, "Сотрудник не найден")
        return current_recommendations(conn, employee_id)


@app.post("/api/hr/employees/{employee_id}/recommendations/generate")
def generate_hr(employee_id: str, user=Depends(hr_write)):
    return _generate_for(employee_id, refresh=True)


@app.post("/api/hr/employees/{employee_id}/archive/{event_id}")
def archive(employee_id: str, event_id: str, body: ReasonInput, user=Depends(hr_write)):
    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(422, "Укажите причину архива")
    with connection() as conn:
        with transaction(conn):
            if not domain.employee(conn, employee_id)[0]:
                raise HTTPException(404, "Сотрудник не найден")
            event = _require_event(conn, event_id)
            if event["mandatory"]:
                raise HTTPException(422, "Обязательное мероприятие не архивируется")
            existing = conn.execute("SELECT active FROM archives WHERE employee_id=? AND event_id=?", (employee_id, event_id)).fetchone()
            if not (existing and existing["active"]) and not _proposal_exists(conn, employee_id, event_id):
                raise HTTPException(422, "Нет активной рекомендации для архива")
            if domain._ongoing(conn, employee_id, event_id):
                raise HTTPException(422, "Начатое или выполненное обучение нельзя убрать из истории")
            conn.execute("INSERT INTO archives VALUES(?,?,?,?,?,?) ON CONFLICT(employee_id,event_id) DO UPDATE SET reason=excluded.reason,actor=excluded.actor,active=1,updated_at=excluded.updated_at", (employee_id, event_id, reason, user["email"], 1, utc_now()))
            audit(conn, user["email"], "recommendation_archived", f"{employee_id}/{event_id}", reason)
        return _profile_with_recommendations(conn, employee_id)


@app.delete("/api/hr/employees/{employee_id}/archive/{event_id}")
def restore_archive(employee_id: str, event_id: str, user=Depends(hr_write)):
    with connection() as conn:
        row = conn.execute("SELECT active,actor FROM archives WHERE employee_id=? AND event_id=?", (employee_id, event_id)).fetchone()
        if not row:
            raise HTTPException(404, "Архив не найден")
        if row["actor"] != user["email"]:
            raise HTTPException(403, "Восстановить архив может создавший его HR")
        with transaction(conn):
            conn.execute("UPDATE archives SET active=0,updated_at=? WHERE employee_id=? AND event_id=?", (utc_now(), employee_id, event_id))
            audit(conn, user["email"], "recommendation_restored", f"{employee_id}/{event_id}")
        return _profile_with_recommendations(conn, employee_id)


def _skill_coverage(person: dict, levels: dict, needed: int, skill_id: str, events: dict, as_of: str, eligible_ids: set[str]):
    options = [e for e in events.values() if not e["mandatory"] and any(d["skill_id"] == skill_id for d in e["develops_skills"])]
    if not options:
        return "not_in_catalog"
    eligible = [event for event in options if event["event_id"] in eligible_ids]
    if not eligible:
        return "not_eligible"
    ceilings = [next(d["max_level"] for d in e["develops_skills"] if d["skill_id"] == skill_id) for e in eligible]
    if max(ceilings) <= levels.get(skill_id, 0):
        return "ceiling_reached"
    return "partial_ceiling" if max(ceilings) < needed else "available"


@app.get("/api/hr/competencies")
def competencies(department: str = "", role: str = "", grade: str = "", type: str = "", date_from: str = "", date_to: str = "", mandatory: str = "voluntary", user=Depends(hr_user)):
    with connection() as conn:
        events, skills, profiles = domain.catalog(conn)
        people = [loads(r["data"]) for r in conn.execute("SELECT data FROM employees")]
        people = [p for p in people if (not department or p["department"] == department) and (not role or p["role"] == role) and (not grade or p["grade"] == grade)]
        aggregates = {sid: {"skill_id": sid, "name": s["name"], "type": s["type"], "required_count": 0, "gap_count": 0, "gap_pct": 0, "critical_count": 0, "people": [], "events": []} for sid, s in skills.items() if not type or s["type"] == type}
        department_aggregates = {}
        employees_with_critical_gap = employees_without_step = employees_unconfirmed_goal = 0
        availability = []
        as_of = meta(conn, "as_of_date")
        for person in people:
            employee_id = person["employee_id"]
            info = domain.candidate_info(conn, employee_id)
            eligible_ids = {c["event_id"] for c in info["candidates"]}
            goal = domain.goal_for(conn, person, profiles)
            employees_unconfirmed_goal += int(goal["source"] != "explicit")
            levels, _ = domain.replay(conn, person, events)
            rows, coverage, _ = domain.skill_metrics(levels, goal, skills)
            has_critical_gap = False
            for row in rows:
                aggregate = aggregates.get(row["skill_id"])
                if not aggregate:
                    continue
                aggregate["required_count"] += 1
                key = (person["department"], row["skill_id"])
                department_aggregate = department_aggregates.setdefault(key, {"department": person["department"], "skill_id": row["skill_id"], "required_count": 0, "gap_count": 0, "gap_pct": None, "critical_count": 0})
                department_aggregate["required_count"] += 1
                if row["gap"]:
                    aggregate["gap_count"] += 1
                    aggregate["critical_count"] += int(row["critical"])
                    department_aggregate["gap_count"] += 1
                    department_aggregate["critical_count"] += int(row["critical"])
                    has_critical_gap = has_critical_gap or row["critical"]
                    aggregate["people"].append({"employee_id": employee_id, "full_name": person["full_name"], "current": row["current"], "required": row["required"], "gap": row["gap"], "coverage_status": _skill_coverage(person, levels, row["required"], row["skill_id"], events, as_of, eligible_ids)})
            employees_with_critical_gap += int(has_critical_gap)
            state = domain.availability_state(conn, employee_id, info)
            employees_without_step += int(state["candidate_count"] == 0 and state["state"] != "requirements_covered")
            rec = current_recommendations(conn, employee_id)
            availability.append({"employee_id": employee_id, "full_name": person["full_name"], "state": state["state"], "candidate_count": state["candidate_count"], "goal_source": state["goal_source"], "source": rec["source"], "calculated_at": rec["calculated_at"]})
        for sid, aggregate in aggregates.items():
            denominator = aggregate["required_count"]
            aggregate["gap_pct"] = round(100 * aggregate["gap_count"] / denominator, 1) if denominator else None
            aggregate["events"] = [{"event_id": e["event_id"], "title": e["title"], "coverage_status": "catalogued"} for e in events.values() if not e["mandatory"] and any(d["skill_id"] == sid for d in e["develops_skills"])]
        for aggregate in department_aggregates.values():
            denominator = aggregate["required_count"]
            aggregate["gap_pct"] = round(100 * aggregate["gap_count"] / denominator, 1) if denominator else None
        selected_ids = {p["employee_id"] for p in people}
        participation = {eid: {"event_id": eid, "title": e["title"], "mandatory": bool(e["mandatory"]), **{s: 0 for s in ("completed", "in_progress", "dropped", "no_show", "declined", "overdue")}, "skips": 0, "archives": 0} for eid, e in events.items() if mandatory == "all" or bool(e["mandatory"]) == (mandatory == "mandatory")}
        linked = {r["linked_record_id"]: r for r in conn.execute("SELECT * FROM app_participations WHERE linked_record_id IS NOT NULL")}
        app_completions = list(conn.execute("SELECT * FROM app_participations WHERE status='completed'"))
        for row in conn.execute("SELECT * FROM history"):
            updated = linked.get(row["record_id"])
            activity_date = updated["completion_date"] if updated and updated["status"] == "completed" else row["date"]
            if row["employee_id"] not in selected_ids or row["event_id"] not in participation or date_from and activity_date < date_from or date_to and activity_date > date_to:
                continue
            if not updated and domain.import_completion_shadowed(row, app_completions, events):
                continue
            status = updated["status"] if updated else row["status"]
            if status in participation[row["event_id"]]:
                participation[row["event_id"]][status] += 1
        for row in conn.execute("SELECT * FROM app_participations WHERE linked_record_id IS NULL"):
            activity_date = row["completion_date"] or row["session_date"] or row["created_at"][:10]
            if row["employee_id"] not in selected_ids or row["event_id"] not in participation or date_from and activity_date < date_from or date_to and activity_date > date_to:
                continue
            if row["status"] in participation[row["event_id"]]:
                participation[row["event_id"]][row["status"]] += 1
        for table, field in (("skips", "skips"), ("archives", "archives")):
            for row in conn.execute(f"SELECT employee_id,event_id FROM {table} WHERE active=1"):
                if row["employee_id"] in selected_ids and row["event_id"] in participation:
                    participation[row["event_id"]][field] += 1
        return {"skills": sorted(aggregates.values(), key=lambda a: (-a["gap_count"], a["name"])), "department_skills": sorted(department_aggregates.values(), key=lambda a: (a["department"], -a["gap_count"], a["skill_id"])), "availability": availability, "participation": list(participation.values()), "employee_count": len(people), "employees_total": len(people), "employees_with_critical_gap": employees_with_critical_gap, "employees_without_step": employees_without_step, "employees_unconfirmed_goal": employees_unconfirmed_goal, "as_of_date": as_of}


@app.post("/api/hr/import/preview")
async def import_preview(employees_file: UploadFile | None = File(None), history_file: UploadFile | None = File(None), user=Depends(hr_write)):
    from .imports import preview_import

    if not employees_file and not history_file:
        raise HTTPException(422, "Загрузите employees JSON и/или activity_history CSV")
    employee_bytes = await employees_file.read(MAX_UPLOAD + 1) if employees_file else None
    history_bytes = await history_file.read(MAX_UPLOAD + 1) if history_file else None
    if employee_bytes is not None and len(employee_bytes) > MAX_UPLOAD or history_bytes is not None and len(history_bytes) > MAX_UPLOAD:
        raise HTTPException(413, "Файл слишком велик")
    with connection() as conn:
        plan = preview_import(conn, employee_bytes, history_bytes)
    token = secrets.token_urlsafe(24)
    _previews[token] = (user["email"], time.monotonic() + 600, plan)
    return {"token": token, "new_employees": plan["counts"].get("new_employees", 0), "updated_employees": plan["counts"].get("updated_employees", 0), "new_history": plan["counts"].get("new_history", 0), "duplicates": plan["counts"].get("duplicates", 0), "errors": plan["errors"], "warnings": plan["warnings"], "ok": plan["ok"], "same_day_completions": plan.get("same_day_completions", [])}


@app.post("/api/hr/import/commit")
def import_commit(body: ImportConfirm, user=Depends(hr_write)):
    from .imports import commit_import

    entry = _previews.get(body.token)
    if not entry or entry[0] != user["email"] or entry[1] < time.monotonic():
        raise HTTPException(404, "Предпросмотр истёк или не найден")
    if not entry[2]["ok"]:
        raise HTTPException(422, "Предпросмотр содержит ошибки")
    with connection() as conn:
        try:
            result = commit_import(conn, entry[2], body.confirm_updates, actor=user["email"], cover_same_day_completions=body.cover_same_day_completions)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    _previews.pop(body.token, None)
    return result


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "API route not found")
    asset = (FRONTEND_DIST / path).resolve()
    if asset.is_file() and FRONTEND_DIST.resolve() in asset.parents:
        return FileResponse(asset)
    index = FRONTEND_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(404, "Frontend build is not available")
