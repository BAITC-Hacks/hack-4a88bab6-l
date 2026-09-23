"""Business rules and API regressions using the published dataset and synthetic imports."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db, domain, main, recommendations  # noqa: E402
from app.catalog import HISTORY_COLUMNS  # noqa: E402
from app.imports import ImportConflict, commit_import, preview_import  # noqa: E402


PASSWORD = "test-only-password"
AS_OF = "2026-10-01"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "career_quest.sqlite")
    monkeypatch.setattr(db, "DATASET_SOURCE", str(ROOT / "career_quest_dataset.zip"))
    monkeypatch.setattr(db, "DEMO_MODE", True)
    monkeypatch.setattr(main, "DEMO_MODE", True)
    monkeypatch.setenv("DEMO_PASSWORD", PASSWORD)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    main._previews.clear()
    recommendations._last_attempt.clear()
    with TestClient(main.app) as test_client:
        yield test_client


def _employee(employee_id: str, *, review: str = "2026-03-01", skills: dict | None = None) -> dict:
    return {
        "employee_id": employee_id,
        "full_name": "Synthetic Test Person",
        "department": "Backend Development",
        "role": "Backend Engineer",
        "grade": "Middle",
        "manager_id": "E0050",
        "hire_date": "2026-01-01",
        "tenure_months": 9,
        "work_format": "hybrid",
        "preferred_language": "ru",
        "career_goal": {"target_role": "Backend Engineer", "target_grade": "Senior"},
        "skills": skills or {},
        "last_review_date": review,
    }


def _employee_bytes(*employees: dict) -> bytes:
    return json.dumps({"meta": {"dataset": "Career Quest", "version": "1.0", "as_of_date": AS_OF}, "employees": list(employees)}).encode()


def _history_bytes(*rows: dict) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=HISTORY_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in HISTORY_COLUMNS})
    return buffer.getvalue().encode()


def _row(record_id: str, employee_id: str, event_id: str, on: str, status: str, pct: int, **extra) -> dict:
    return {
        "record_id": record_id,
        "employee_id": employee_id,
        "event_id": event_id,
        "date": on,
        "due_date": extra.get("due_date", ""),
        "status": status,
        "completion_pct": pct,
        "score": extra.get("score", ""),
        "feedback_rating": extra.get("feedback_rating", ""),
        "assigned_by": extra.get("assigned_by", "self"),
    }


def _login(client: TestClient, email: str) -> str:
    result = client.post("/api/login", json={"email": email, "password": PASSWORD})
    assert result.status_code == 200, result.text
    me = client.get("/api/me")
    assert me.status_code == 200, me.text
    csrf = me.json()["csrf_token"]
    client.headers.update({"X-CSRF-Token": csrf})
    return csrf


def _fake_openai(info: dict, history: list, pool: dict, model: str):
    candidate = next((item for item in info["candidates"] if item["event_id"] == "EV_007"), info["candidates"][0])
    event_id = candidate["event_id"]
    skill_id = candidate["develops"][0]["skill_id"]
    return recommendations.ModelSelection(
        recommendations=[recommendations.ModelChoice(
            event_id=event_id,
            action=candidate["action"],
            rationale="A useful next step",
            evidence_ids=["goal", "history_summary", f"gap:{event_id}:{skill_id}", f"effect:{event_id}:{skill_id}", f"format:{event_id}"],
            tradeoff=None,
        )],
        limitations=[],
    )


def _import_person(conn, person: dict, *rows: dict):
    plan = preview_import(conn, _employee_bytes(person), _history_bytes(*rows) if rows else None)
    assert plan["ok"], plan["errors"]
    return commit_import(conn, plan)


def test_seed_and_real_e0005_regression(client: TestClient):
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 200
        assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 2743
        assert conn.execute("SELECT COUNT(*) FROM employee_versions").fetchone()[0] == 200
        person, version = domain.employee(conn, "E0005")
        assert version == 1
        assert person["role"] == "Backend Engineer" and person["grade"] == "Middle"
        assert person["career_goal"] == {"target_role": "Backend Engineer", "target_grade": "Senior"}
        assert person["skills"]["SK_SYSTEM_DESIGN"] == 2
        events, _, profiles = domain.catalog(conn)
        assert profiles[("Backend Engineer", "Senior")]["required_skills"]["SK_SYSTEM_DESIGN"] == 4
        completed_club = conn.execute("SELECT COUNT(*) FROM history WHERE employee_id='E0005' AND event_id='EV_036' AND status='completed'").fetchone()[0]
        assert completed_club == 3
        assert conn.execute("SELECT COUNT(*) FROM history WHERE employee_id='E0005' AND event_id='EV_006' AND status='completed'").fetchone()[0] == 1
        candidates = {c["event_id"] for c in domain.candidate_info(conn, "E0005")["candidates"]}
        assert "EV_007" in candidates
        assert "EV_006" not in candidates
        assert not events["EV_007"]["mandatory"]


def test_replay_review_boundary_mandatory_and_assessment_version(client: TestClient):
    person = _employee("E0901", skills={
        "SK_TEAMWORK": 0, "SK_PRODUCT_KNOWLEDGE": 0, "SK_PUBLIC_SPEAKING": 0,
        "SK_WRITTEN_COMMUNICATION": 0, "SK_COMMUNICATION": 0,
    })
    rows = (
        _row("R900001", "E0901", "EV_036", "2026-02-01", "completed", 100),
        _row("R900002", "E0901", "EV_036", "2026-03-01", "completed", 100),
        _row("R900003", "E0901", "EV_036", "2026-03-02", "completed", 100),
        _row("R900004", "E0901", "EV_004", "2026-03-03", "completed", 100, due_date="2026-03-15", assigned_by="hr"),
        _row("R900005", "E0901", "EV_001", "2026-03-04", "completed", 100, due_date="2026-04-03", score=80, assigned_by="hr"),
        _row("R900006", "E0901", "EV_036", "2026-03-05", "in_progress", 40),
        _row("R900007", "E0901", "EV_036", "2026-03-06", "no_show", 0),
    )
    with db.connection() as conn:
        plan = preview_import(conn, _employee_bytes(person), _history_bytes(*rows))
        assert plan["ok"], plan["errors"]
        commit_import(conn, plan)
        events, _, _ = domain.catalog(conn)
        levels, dynamics = domain.replay(conn, person, events)
        assert levels["SK_PUBLIC_SPEAKING"] == 1  # only the post-review completion
        assert levels["SK_TEAMWORK"] == 1 and levels["SK_PRODUCT_KNOWLEDGE"] == 1  # EV_004
        assert len(dynamics) == 3  # assessment + EV_036 + EV_004; compliance adds no skill
        assert domain.apply_event({"SK_PUBLIC_SPEAKING": 5}, events["EV_036"]) == []
        assert "EV_004" not in {c["event_id"] for c in domain.candidate_info(conn, "E0901")["candidates"]}

        # New app completions count even when the date equals the prior review day.
        conn.execute(
            """INSERT INTO app_participations(id,employee_id,event_id,linked_record_id,session_date,status,completion_date,completed_at,covered_by_review_version,is_simulated,created_at)
            VALUES('app-review-day','E0901','EV_008',NULL,'2026-03-01','completed','2026-03-01','2026-03-01T12:00:00+00:00',NULL,0,'2026-03-01T11:00:00+00:00')"""
        )
        levels, _ = domain.replay(conn, person, events)
        assert levels["SK_WRITTEN_COMMUNICATION"] == 1

        updated = dict(person)
        updated["last_review_date"] = "2026-04-01"
        updated["skills"] = {**levels}
        update_plan = preview_import(conn, _employee_bytes(updated))
        assert update_plan["counts"]["updated_employees"] == 1
        with pytest.raises(ImportConflict):
            commit_import(conn, update_plan, confirm_updates=False)
        commit_import(conn, update_plan, confirm_updates=True)
        assert conn.execute("SELECT version FROM employees WHERE employee_id='E0901'").fetchone()[0] == 2
        versions = [json.loads(r[0]) for r in conn.execute("SELECT data FROM employee_versions WHERE employee_id='E0901' ORDER BY version")]
        assert len(versions) == 2 and versions[0]["last_review_date"] == "2026-03-01"
        assert conn.execute("SELECT covered_by_review_version FROM app_participations WHERE id='app-review-day'").fetchone()[0] == 2
        assert domain.replay(conn, updated, events)[0]["SK_WRITTEN_COMMUNICATION"] == 1


def test_explicit_same_day_assessment_cover_prevents_double_gain(client: TestClient):
    person = _employee("E0923", review="2026-03-01", skills={
        "SK_COMMUNICATION": 0, "SK_PUBLIC_SPEAKING": 0,
    })
    with db.connection() as conn:
        _import_person(conn, person)
        conn.execute(
            """INSERT INTO app_participations(id,employee_id,event_id,linked_record_id,session_date,status,completion_date,completed_at,covered_by_review_version,is_simulated,created_at)
            VALUES('same-day-before-review','E0923','EV_008',NULL,'2026-10-12','completed','2026-10-01','2026-10-01T09:00:00+00:00',NULL,1,'2026-10-01T08:00:00+00:00')"""
        )
        events, _, _ = domain.catalog(conn)
        assert domain.replay(conn, person, events)[0]["SK_COMMUNICATION"] == 1

        reassessed = {**person, "last_review_date": AS_OF, "skills": {
            "SK_COMMUNICATION": 1, "SK_PUBLIC_SPEAKING": 0,
        }}
        plan = preview_import(conn, _employee_bytes(reassessed))
        assert plan["ok"] and plan["counts"]["updated_employees"] == 1
        assert plan["same_day_completions"] == [{
            "id": "same-day-before-review", "employee_id": "E0923",
            "event_id": "EV_008", "completion_date": AS_OF,
        }]
        with pytest.raises(ImportConflict):
            commit_import(conn, plan, cover_same_day_completions=True)
        committed = commit_import(conn, plan, confirm_updates=True, cover_same_day_completions=True)
        assert committed["covered_same_day_completions"] == 1
        assert conn.execute(
            "SELECT covered_by_review_version FROM app_participations WHERE id='same-day-before-review'"
        ).fetchone()[0] == 2
        assert domain.replay(conn, reassessed, events)[0]["SK_COMMUNICATION"] == 1

        # This action is created after the assessment import, on the same business day.
        conn.execute(
            """INSERT INTO app_participations(id,employee_id,event_id,linked_record_id,session_date,status,completion_date,completed_at,covered_by_review_version,is_simulated,created_at)
            VALUES('same-day-after-review','E0923','EV_036',NULL,'2026-10-08','completed','2026-10-01','2026-10-01T12:00:00+00:00',NULL,1,'2026-10-01T11:00:00+00:00')"""
        )
        levels, _ = domain.replay(conn, reassessed, events)
        assert levels["SK_COMMUNICATION"] == 1
        assert levels["SK_PUBLIC_SPEAKING"] == 1
        assert conn.execute(
            "SELECT covered_by_review_version FROM app_participations WHERE id='same-day-after-review'"
        ).fetchone()[0] is None

        # When the new assessment does not include a same-day completion, the
        # default preserves that completion's gain.
        second_person = _employee("E0926", review="2026-03-01", skills={"SK_COMMUNICATION": 0})
        _import_person(conn, second_person)
        conn.execute(
            """INSERT INTO app_participations(id,employee_id,event_id,linked_record_id,session_date,status,completion_date,completed_at,covered_by_review_version,is_simulated,created_at)
            VALUES('same-day-uncovered','E0926','EV_008',NULL,'2026-10-12','completed','2026-10-01','2026-10-01T09:00:00+00:00',NULL,1,'2026-10-01T08:00:00+00:00')"""
        )
        second_review = {**second_person, "last_review_date": AS_OF}
        uncovered_plan = preview_import(conn, _employee_bytes(second_review))
        assert uncovered_plan["same_day_completions"][0]["id"] == "same-day-uncovered"
        commit_import(conn, uncovered_plan, confirm_updates=True)
        assert conn.execute(
            "SELECT covered_by_review_version FROM app_participations WHERE id='same-day-uncovered'"
        ).fetchone()[0] is None
        assert domain.replay(conn, second_review, events)[0]["SK_COMMUNICATION"] == 1


def test_hr_import_api_exposes_and_commits_same_day_cover(client: TestClient):
    person = _employee("E0925", review="2026-03-01", skills={"SK_COMMUNICATION": 0})
    with db.connection() as conn:
        _import_person(conn, person)
        conn.execute(
            """INSERT INTO app_participations(id,employee_id,event_id,linked_record_id,session_date,status,completion_date,completed_at,covered_by_review_version,is_simulated,created_at)
            VALUES('api-same-day-cover','E0925','EV_008',NULL,'2026-10-12','completed','2026-10-01','2026-10-01T09:00:00+00:00',NULL,1,'2026-10-01T08:00:00+00:00')"""
        )
    updated = {**person, "last_review_date": AS_OF, "skills": {"SK_COMMUNICATION": 1}}
    _login(client, "hr@careerquest.test")
    preview = client.post(
        "/api/hr/import/preview",
        files={"employees_file": ("employees.json", _employee_bytes(updated), "application/json")},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["same_day_completions"][0]["id"] == "api-same-day-cover"
    committed = client.post("/api/hr/import/commit", json={
        "token": preview.json()["token"],
        "confirm_updates": True,
        "cover_same_day_completions": True,
    })
    assert committed.status_code == 200, committed.text
    assert committed.json()["covered_same_day_completions"] == 1
    with db.connection() as conn:
        assert conn.execute(
            "SELECT covered_by_review_version FROM app_participations WHERE id='api-same-day-cover'"
        ).fetchone()[0] == 2
        events, _, _ = domain.catalog(conn)
        assert domain.replay(conn, updated, events)[0]["SK_COMMUNICATION"] == 1


def test_same_day_preview_rejects_new_completion_added_before_commit(client: TestClient):
    person = _employee("E0927", review="2026-03-01", skills={"SK_COMMUNICATION": 0})
    with db.connection() as conn:
        _import_person(conn, person)
        updated = {**person, "last_review_date": AS_OF, "skills": {"SK_COMMUNICATION": 1}}
        stale_plan = preview_import(conn, _employee_bytes(updated))
        assert stale_plan["same_day_completions"] == []
        conn.execute(
            """INSERT INTO app_participations(id,employee_id,event_id,linked_record_id,session_date,status,completion_date,completed_at,covered_by_review_version,is_simulated,created_at)
            VALUES('added-after-preview','E0927','EV_008',NULL,'2026-10-12','completed','2026-10-01','2026-10-01T09:00:00+00:00',NULL,1,'2026-10-01T08:00:00+00:00')"""
        )
        with pytest.raises(ImportConflict, match="preview"):
            commit_import(conn, stale_plan, confirm_updates=True, cover_same_day_completions=True)
        assert conn.execute("SELECT version FROM employees WHERE employee_id='E0927'").fetchone()[0] == 1
        fresh_plan = preview_import(conn, _employee_bytes(updated))
        assert fresh_plan["same_day_completions"][0]["id"] == "added-after-preview"


def test_import_extra_profile_history_idempotency_and_conflict(client: TestClient):
    person = _employee("E0902", review="2026-02-01", skills={"SK_WRITTEN_COMMUNICATION": 0})
    row = _row("R900101", "E0902", "EV_008", "2026-04-01", "completed", 100, feedback_rating=4)
    employee_bytes, history_bytes = _employee_bytes(person), _history_bytes(row)
    with db.connection() as conn:
        plan = preview_import(conn, employee_bytes, history_bytes)
        assert plan["ok"] and plan["counts"]["new_employees"] == 1 and plan["counts"]["new_history"] == 1
        commit_import(conn, plan)
        assert conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 201
        assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 2744
        again = preview_import(conn, employee_bytes, history_bytes)
        assert again["ok"] and again["counts"]["duplicates"] == 2
        commit_import(conn, again)
        assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 2744

        conflict = preview_import(conn, None, _history_bytes({**row, "feedback_rating": 5}))
        assert not conflict["ok"]
        assert any(error["field"] == "record_id" and error["row"] == 2 for error in conflict["errors"])
        with pytest.raises(ImportConflict):
            commit_import(conn, conflict)
        assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 2744

        invalid = _employee("E0904")
        invalid["manager_id"] = "E_DOES_NOT_EXIST"
        rejected = preview_import(conn, _employee_bytes(invalid))
        assert not rejected["ok"] and any(error["field"] == "manager_id" for error in rejected["errors"])
        assert not conn.execute("SELECT 1 FROM employees WHERE employee_id='E0904'").fetchone()


def test_import_rejects_invalid_status_semantics_without_partial_write(client: TestClient):
    person = _employee("E0918")
    invalid_rows = (
        _row("R901001", "E0918", "EV_009", "2026-06-01", "no_show", 0),  # self-paced
        _row("R901002", "E0918", "EV_008", "2026-06-01", "overdue", 0, due_date="2026-06-20"),  # voluntary
        _row("R901003", "E0918", "EV_008", "2026-06-01", "declined", 0, assigned_by="self"),
        _row("R901004", "E0918", "EV_001", "2026-06-01", "overdue", 0, assigned_by="hr"),  # no due date
        _row("R901005", "E0918", "EV_004", "2026-06-01", "completed", 100, due_date="2026-05-01", assigned_by="hr"),
    )
    with db.connection() as conn:
        before_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        plan = preview_import(conn, _employee_bytes(person), _history_bytes(*invalid_rows))
        assert not plan["ok"]
        assert {(error["row"], error["field"]) for error in plan["errors"]} >= {
            (2, "status"), (3, "status"), (4, "assigned_by"), (5, "due_date"), (6, "due_date"),
        }
        with pytest.raises(ImportConflict):
            commit_import(conn, plan, actor="hr@careerquest.test")
        assert not conn.execute("SELECT 1 FROM employees WHERE employee_id='E0918'").fetchone()
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == before_users
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='import_committed'").fetchone()[0] == 0


def test_import_of_same_completed_participation_does_not_add_skill_twice(client: TestClient):
    person = _employee("E0906", review="2026-02-01", skills={
        "SK_WRITTEN_COMMUNICATION": 0, "SK_COMMUNICATION": 0,
    })
    with db.connection() as conn:
        plan = preview_import(conn, _employee_bytes(person))
        assert plan["ok"], plan["errors"]
        commit_import(conn, plan)
        conn.execute(
            """INSERT INTO app_participations(id,employee_id,event_id,linked_record_id,session_date,status,completion_date,completed_at,covered_by_review_version,is_simulated,created_at)
            VALUES('app-import-overlap','E0906','EV_008',NULL,'2026-09-01','completed','2026-09-01','2026-09-01T12:00:00+00:00',NULL,0,'2026-09-01T11:00:00+00:00')"""
        )
        history = _history_bytes(_row("R900301", "E0906", "EV_008", "2026-09-01", "completed", 100))
        plan = preview_import(conn, history_bytes=history)
        assert plan["ok"], plan["errors"]
        commit_import(conn, plan)
        events, _, _ = domain.catalog(conn)
        levels, _ = domain.replay(conn, person, events)
        assert levels["SK_WRITTEN_COMMUNICATION"] == 1
        assert levels["SK_COMMUNICATION"] == 1
        other_completions = conn.execute(
            "SELECT COUNT(*) FROM history WHERE event_id='EV_008' AND status='completed' AND employee_id!='E0906'"
        ).fetchone()[0]
    _login(client, "hr@careerquest.test")
    overview = client.get("/api/hr/competencies")
    assert overview.status_code == 200, overview.text
    business_writing = next(item for item in overview.json()["participation"] if item["event_id"] == "EV_008")
    assert business_writing["completed"] == other_completions + 1


def test_self_paced_import_enrollment_date_does_not_duplicate_app_completion(client: TestClient):
    person = _employee("E0919", review="2026-02-01", skills={"SK_PYTHON": 1})
    with db.connection() as conn:
        _import_person(conn, person)
        conn.execute(
            """INSERT INTO app_participations(id,employee_id,event_id,linked_record_id,session_date,status,completion_date,completed_at,covered_by_review_version,is_simulated,created_at)
            VALUES('app-self-paced-overlap','E0919','EV_012',NULL,NULL,'completed','2026-09-01','2026-09-01T12:00:00+00:00',NULL,0,'2026-07-01T11:00:00+00:00')"""
        )
        # Imported self-paced date is enrollment, not the date of completion.
        history = _history_bytes(_row("R900401", "E0919", "EV_012", "2026-07-01", "completed", 100, score=88))
        plan = preview_import(conn, history_bytes=history)
        assert plan["ok"], plan["errors"]
        commit_import(conn, plan)
        events, _, _ = domain.catalog(conn)
        levels, _ = domain.replay(conn, person, events)
        assert levels["SK_PYTHON"] == 2
        other_completions = conn.execute(
            "SELECT COUNT(*) FROM history WHERE event_id='EV_012' AND status='completed' AND employee_id!='E0919'"
        ).fetchone()[0]
    _login(client, "hr@careerquest.test")
    overview = client.get("/api/hr/competencies")
    assert overview.status_code == 200, overview.text
    advanced_python = next(row for row in overview.json()["participation"] if row["event_id"] == "EV_012")
    assert advanced_python["completed"] == other_completions + 1


def test_separate_synthetic_no_show_fixture(client: TestClient):
    # Explicitly synthetic case; E0005 has three completed club sessions instead.
    person = _employee("E0903", review="2026-08-01", skills={"SK_PUBLIC_SPEAKING": 0, "SK_SYSTEM_DESIGN": 0})
    rows = (
        _row("R900201", "E0903", "EV_036", "2026-08-14", "no_show", 0),
        _row("R900202", "E0903", "EV_036", "2026-08-28", "no_show", 0),
    )
    with db.connection() as conn:
        plan = preview_import(conn, _employee_bytes(person), _history_bytes(*rows))
        assert plan["ok"], plan["errors"]
        commit_import(conn, plan)
        events, _, _ = domain.catalog(conn)
        levels, _ = domain.replay(conn, person, events)
        assert levels["SK_PUBLIC_SPEAKING"] == 0
        assert levels["SK_SYSTEM_DESIGN"] == 0
        assert [r["status"] for r in domain.history_for(conn, "E0903", events)] == ["no_show", "no_show"]
        assert "EV_007" not in {c["event_id"] for c in domain.candidate_info(conn, "E0903")["candidates"]}


def test_null_goal_lead_cross_role_and_empty_coverage(client: TestClient):
    middle = _employee("E0910")
    middle["career_goal"] = None
    lead = _employee("E0911")
    lead["grade"] = "Lead"
    lead["manager_id"] = None
    lead["career_goal"] = None
    cross_role = _employee("E0912", skills={"SK_SYSTEM_DESIGN": 1, "SK_PUBLIC_SPEAKING": 5})
    cross_role["career_goal"] = {"target_role": "Product Manager", "target_grade": "Middle"}
    with db.connection() as conn:
        for person in (middle, lead, cross_role):
            _import_person(conn, person)
        events, skills, profiles = domain.catalog(conn)
        assert domain.goal_for(conn, middle, profiles)["source"] == "suggested_next_grade"
        assert domain.goal_for(conn, middle, profiles)["grade"] == "Senior"
        assert domain.goal_for(conn, lead, profiles)["source"] == "current_role_development"
        assert domain.goal_for(conn, lead, profiles)["grade"] == "Lead"
        selected = domain.goal_for(conn, cross_role, profiles)
        assert selected["role"] == "Product Manager" and selected["source"] == "explicit"
        assert domain.profile(conn, "E0912")["employee"]["role"] == "Backend Engineer"
        metrics, coverage, critical = domain.skill_metrics({}, {"profile": {"required_skills": {}, "critical_skills": []}}, skills)
        assert metrics == [] and coverage is None and critical == 0
        assert events["EV_026"]["target_roles"] == ["Product Manager"]


def test_candidate_filters_use_current_role_prereqs_and_real_gain(client: TestClient):
    cross_role = _employee("E0913", skills={"SK_SYSTEM_DESIGN": 1, "SK_PUBLIC_SPEAKING": 5})
    cross_role["career_goal"] = {"target_role": "Product Manager", "target_grade": "Middle"}
    backend_goal = _employee("E0914", skills={"SK_SYSTEM_DESIGN": 1, "SK_CLOUD": 0, "SK_CICD": 0, "SK_PUBLIC_SPEAKING": 5})
    with db.connection() as conn:
        _import_person(conn, cross_role)
        _import_person(conn, backend_goal)
        cross_info = domain.candidate_info(conn, "E0913")
        cross_ids = {candidate["event_id"] for candidate in cross_info["candidates"]}
        assert "EV_026" not in cross_ids  # target profession does not grant admission
        assert "EV_020" not in cross_ids
        assert "EV_007" not in cross_ids  # System Design 1 < prerequisite 2
        assert "EV_036" not in cross_ids  # at 5 the club's max_level=4 gives no gain
        assert cross_info["exclusions"]["role_or_grade"] > 0
        assert cross_info["exclusions"]["prerequisites"] > 0
        assert cross_info["exclusions"]["no_useful_gain"] > 0
        backend_info = domain.candidate_info(conn, "E0914")
        self_paced = next(item for item in backend_info["candidates"] if item["event_id"] == "EV_009")
        assert self_paced["format"] == "self_paced" and self_paced["session_date"] is None
        assert self_paced["develops"]


def test_club_can_repeat_on_new_session_but_normal_course_cannot(client: TestClient):
    person = _employee("E0915", skills={"SK_PUBLIC_SPEAKING": 0, "SK_COMMUNICATION": 0})
    with db.connection() as conn:
        _import_person(conn, person)
    _login(client, "e0915@careerquest.test")

    first = client.post("/api/employee/participations", json={"event_id": "EV_036", "session_date": "2026-10-08"})
    assert first.status_code == 200, first.text
    same_ongoing = client.post("/api/employee/participations", json={"event_id": "EV_036", "session_date": "2026-10-08"})
    assert same_ongoing.status_code == 200 and same_ongoing.json()["id"] == first.json()["id"]
    assert client.post(f"/api/employee/participations/{first.json()['id']}/complete", json={"simulate": True}).status_code == 200
    old_session = client.post("/api/employee/participations", json={"event_id": "EV_036", "session_date": "2026-10-08"})
    assert old_session.status_code == 422
    second = client.post("/api/employee/participations", json={"event_id": "EV_036", "session_date": "2026-10-22"})
    assert second.status_code == 200, second.text
    finished = client.post(f"/api/employee/participations/{second.json()['id']}/complete", json={"simulate": True})
    assert finished.status_code == 200
    with db.connection() as conn:
        current, _ = domain.employee(conn, "E0915")
        events, _, _ = domain.catalog(conn)
        assert domain.replay(conn, current, events)[0]["SK_PUBLIC_SPEAKING"] == 2

    ordinary = client.post("/api/employee/participations", json={"event_id": "EV_008", "session_date": "2026-10-12"})
    assert ordinary.status_code == 200, ordinary.text
    assert client.post(f"/api/employee/participations/{ordinary.json()['id']}/complete", json={"simulate": True}).status_code == 200
    repeated = client.post("/api/employee/participations", json={"event_id": "EV_008", "session_date": "2026-11-12"})
    assert repeated.status_code == 422


def test_api_ai_skip_archive_and_restores_are_reversible(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(recommendations, "_call_openai", _fake_openai)
    _login(client, "e0005@careerquest.test")
    initial = client.get("/api/employee/profile").json()
    history_count = len(initial["history"])
    system_design = next(skill["current"] for skill in initial["skills"] if skill["skill_id"] == "SK_SYSTEM_DESIGN")
    assert system_design == 2

    generated = client.post("/api/employee/recommendations/generate")
    assert generated.status_code == 200, generated.text
    assert generated.json()["source"] == "ai"
    assert generated.json()["items"][0]["event_id"] == "EV_007"
    assert len(generated.json()["items"][0]["factors"]) >= 3

    skipped = client.post("/api/employee/recommendations/EV_007/skip", json={"reason": "Другой формат"})
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["skips"][0]["event_id"] == "EV_007"
    assert len(skipped.json()["history"]) == history_count
    assert next(skill["current"] for skill in skipped.json()["skills"] if skill["skill_id"] == "SK_SYSTEM_DESIGN") == 2
    alternative = client.post("/api/employee/recommendations/generate")
    assert alternative.status_code == 200 and "EV_007" not in {item["event_id"] for item in alternative.json()["items"]}

    restored = client.delete("/api/employee/recommendations/EV_007/skip")
    assert restored.status_code == 200 and restored.json()["skips"] == []
    regenerated = client.post("/api/employee/recommendations/generate")
    assert regenerated.status_code == 200 and regenerated.json()["items"][0]["event_id"] == "EV_007"

    _login(client, "hr@careerquest.test")
    archived = client.post("/api/hr/employees/E0005/archive/EV_007", json={"reason": "Повторим после оценки"})
    assert archived.status_code == 200, archived.text
    assert archived.json()["archives"][0]["reason"] == "Повторим после оценки"
    assert len(archived.json()["history"]) == history_count
    _login(client, "e0005@careerquest.test")
    employee_view = client.get("/api/employee/profile").json()
    assert employee_view["archives"][0]["event_id"] == "EV_007"
    assert len(employee_view["history"]) == history_count
    assert "EV_007" not in {item["event_id"] for item in client.post("/api/employee/recommendations/generate").json()["items"]}
    _login(client, "hr@careerquest.test")
    restored_archive = client.delete("/api/hr/employees/E0005/archive/EV_007")
    assert restored_archive.status_code == 200 and restored_archive.json()["archives"] == []


def test_api_completion_once_persists_across_reinitialization(client: TestClient):
    _login(client, "e0005@careerquest.test")
    started = client.post("/api/employee/participations", json={"event_id": "EV_007", "session_date": "2026-10-23"})
    assert started.status_code == 200, started.text
    participation_id = started.json()["id"]
    assert started.json()["status"] == "planned"
    completed = client.post(f"/api/employee/participations/{participation_id}/complete", json={"simulate": True})
    assert completed.status_code == 200, completed.text
    current = next(skill["current"] for skill in completed.json()["skills"] if skill["skill_id"] == "SK_SYSTEM_DESIGN")
    assert current == 3
    duplicate = client.post(f"/api/employee/participations/{participation_id}/complete", json={"simulate": True})
    assert duplicate.status_code == 200
    assert next(skill["current"] for skill in duplicate.json()["skills"] if skill["skill_id"] == "SK_SYSTEM_DESIGN") == 3

    db.init_db()  # same file, as on server restart; seed must not overwrite actions
    persisted = client.get("/api/employee/profile")
    assert persisted.status_code == 200
    assert next(skill["current"] for skill in persisted.json()["skills"] if skill["skill_id"] == "SK_SYSTEM_DESIGN") == 3
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 200
        assert conn.execute("SELECT COUNT(*) FROM app_participations WHERE id=? AND status='completed'", (participation_id,)).fetchone()[0] == 1
    _login(client, "hr@careerquest.test")
    hr_view = client.get("/api/hr/employees/E0005")
    assert hr_view.status_code == 200
    assert next(skill["current"] for skill in hr_view.json()["skills"] if skill["skill_id"] == "SK_SYSTEM_DESIGN") == 3


def test_api_permissions_csrf_and_import(client: TestClient):
    assert client.get("/api/employee/profile").status_code == 401
    assert client.get("/api/hr/employees").status_code == 401
    _login(client, "e0005@careerquest.test")
    assert client.get("/api/hr/employees").status_code == 403
    assert client.get("/api/hr/employees/E0001").status_code == 403
    assert client.post("/api/hr/employees/E0005/archive/EV_007", json={"reason": "test"}).status_code == 403
    assert client.post("/api/hr/import/preview", files={"employees_file": ("employees.json", _employee_bytes(_employee("E0905")), "application/json")}).status_code == 403

    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/employee/goal", json={"role": "Backend Engineer", "grade": "Senior"}).status_code == 403
    _login(client, "hr@careerquest.test")
    assert client.get("/api/employee/profile").status_code == 403
    assert client.post("/api/employee/goal", json={"role": "Backend Engineer", "grade": "Senior"}).status_code == 403
    preview = client.post("/api/hr/import/preview", files={"employees_file": ("employees.json", _employee_bytes(_employee("E0905")), "application/json")})
    assert preview.status_code == 200, preview.text
    assert preview.json()["ok"] and preview.json()["new_employees"] == 1
    committed = client.post("/api/hr/import/commit", json={"token": preview.json()["token"], "confirm_updates": False})
    assert committed.status_code == 200, committed.text
    assert client.get("/api/hr/employees/E0905").status_code == 200
    assert client.get("/api/hr/employees").json()["items"][-1]["employee_id"] == "E0905"
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='import_committed'").fetchone()[0] == 1
    _login(client, "e0905@careerquest.test")
    assert client.get("/api/employee/profile").json()["employee"]["employee_id"] == "E0905"


def test_demo_password_rotation_invalidates_sessions(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _login(client, "e0005@careerquest.test")
    with db.connection() as conn:
        before_hash = conn.execute("SELECT password_hash FROM users WHERE email='e0005@careerquest.test'").fetchone()[0]
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE email='e0005@careerquest.test'").fetchone()[0] == 1
    monkeypatch.setenv("DEMO_PASSWORD", "rotated-test-password")
    db.init_db()
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/login", json={"email": "e0005@careerquest.test", "password": PASSWORD}).status_code == 401
    replacement = client.post("/api/login", json={"email": "e0005@careerquest.test", "password": "rotated-test-password"})
    assert replacement.status_code == 200, replacement.text
    with db.connection() as conn:
        after_hash = conn.execute("SELECT password_hash FROM users WHERE email='e0005@careerquest.test'").fetchone()[0]
        assert after_hash != before_hash
        assert db.check_password("rotated-test-password", after_hash)
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE email='e0005@careerquest.test'").fetchone()[0] == 1


def test_import_provisions_only_new_demo_account_and_audits_atomically(client: TestClient):
    new_person = _employee("E0920")
    reserved_person = _employee("E0921")
    foreign_hash = db.hash_password("foreign-password")
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO users(email,password_hash,role,employee_id) VALUES(?,?,?,NULL)",
            ("e0921@careerquest.test", foreign_hash, "hr"),
        )
        plan = preview_import(conn, _employee_bytes(new_person, reserved_person))
        assert plan["ok"], plan["errors"]
        result = commit_import(conn, plan, actor="hr@careerquest.test")
        assert result["counts"]["new_employees"] == 2
        assert result["demo_accounts_created"] == 1
        account = conn.execute("SELECT role,employee_id FROM users WHERE email='e0920@careerquest.test'").fetchone()
        assert tuple(account) == ("employee", "E0920")
        reserved = conn.execute("SELECT role,password_hash FROM users WHERE email='e0921@careerquest.test'").fetchone()
        assert tuple(reserved) == ("hr", foreign_hash)
        assert not conn.execute("SELECT 1 FROM demo_users WHERE email='e0921@careerquest.test'").fetchone()
        audit_rows = conn.execute("SELECT actor,action,reason FROM audit_log WHERE action='import_committed'").fetchall()
        assert len(audit_rows) == 1 and audit_rows[0]["actor"] == "hr@careerquest.test"
        assert PASSWORD not in audit_rows[0]["reason"]
    _login(client, "e0920@careerquest.test")
    assert client.get("/api/employee/profile").json()["employee"]["employee_id"] == "E0920"


def test_import_audit_failure_rolls_back_profile_and_demo_account(client: TestClient):
    with db.connection() as conn:
        conn.execute(
            """CREATE TRIGGER reject_import_audit BEFORE INSERT ON audit_log
            WHEN NEW.action='import_committed' BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"""
        )
        plan = preview_import(conn, _employee_bytes(_employee("E0922")))
        assert plan["ok"], plan["errors"]
        with pytest.raises(sqlite3.IntegrityError):
            commit_import(conn, plan, actor="hr@careerquest.test")
        assert not conn.execute("SELECT 1 FROM employees WHERE employee_id='E0922'").fetchone()
        assert not conn.execute("SELECT 1 FROM employee_versions WHERE employee_id='E0922'").fetchone()
        assert not conn.execute("SELECT 1 FROM users WHERE email='e0922@careerquest.test'").fetchone()


def test_saved_demo_login_and_session_are_rejected_when_demo_mode_off(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _login(client, "e0005@careerquest.test")
    monkeypatch.setattr(db, "DEMO_MODE", False)
    monkeypatch.setattr(main, "DEMO_MODE", False)
    db.init_db()
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/login", json={"email": "e0005@careerquest.test", "password": PASSWORD}).status_code == 401


def test_invalid_ai_ids_duplicates_and_stale_result(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    with db.connection() as conn:
        info = domain.candidate_info(conn, "E0005")
        history = domain.history_for(conn, "E0005", info["events"])
        pool = recommendations.evidence_pool(info, history)
        valid = _fake_openai(info, history, pool, "test-model")
        choice = valid.recommendations[0]
        unknown_event = valid.model_copy(update={"recommendations": [choice.model_copy(update={"event_id": "EV_UNKNOWN"})]})
        wrong_evidence = valid.model_copy(update={"recommendations": [choice.model_copy(update={"evidence_ids": ["goal", "history_summary", "missing:evidence"]})]})
        duplicated = valid.model_copy(update={"recommendations": [choice, choice]})
        for invalid in (unknown_event, wrong_evidence, duplicated):
            with pytest.raises(ValueError):
                recommendations._validate_selection(invalid, info, pool)

        monkeypatch.setattr(recommendations, "_call_openai", lambda *_args: unknown_event)
        fallback = recommendations.generate(conn, "E0005", force=True)
        assert fallback["source"] == "fallback" and fallback["items"]
        prior_runs = conn.execute("SELECT COUNT(*) FROM recommendation_runs WHERE employee_id='E0005'").fetchone()[0]

        def changes_goal_mid_request(*_args):
            with db.connection() as other, db.transaction(other):
                other.execute(
                    "INSERT INTO goals(employee_id,role,grade,updated_at) VALUES(?,?,?,?)",
                    ("E0005", "Product Manager", "Middle", db.utc_now()),
                )
            return valid

        monkeypatch.setattr(recommendations, "_call_openai", changes_goal_mid_request)
        recommendations._last_attempt.clear()
        with pytest.raises(RuntimeError, match="changed during generation"):
            recommendations.generate(conn, "E0005", force=True)
        assert conn.execute("SELECT COUNT(*) FROM recommendation_runs WHERE employee_id='E0005'").fetchone()[0] == prior_runs
        assert recommendations.current_recommendations(conn, "E0005")["status"] == "stale"


def test_hr_denominators_and_separate_mandatory_participation(client: TestClient):
    _login(client, "hr@careerquest.test")
    result = client.get("/api/hr/competencies", params={"department": "Backend Development", "type": "hard"})
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["employee_count"] == 40
    assert all(skill["type"] == "hard" for skill in payload["skills"])
    aggregate = next(skill for skill in payload["skills"] if skill["skill_id"] == "SK_SYSTEM_DESIGN")
    with db.connection() as conn:
        events, _, profiles = domain.catalog(conn)
        people = [json.loads(row[0]) for row in conn.execute("SELECT data FROM employees")]
        backend_people = [person for person in people if person["department"] == "Backend Development"]
        required_count = gap_count = 0
        for person in backend_people:
            goal = domain.goal_for(conn, person, profiles)
            required = goal["profile"]["required_skills"].get("SK_SYSTEM_DESIGN", 0)
            if required:
                required_count += 1
                levels, _ = domain.replay(conn, person, events)
                gap_count += int(levels.get("SK_SYSTEM_DESIGN", 0) < required)
        assert aggregate["required_count"] == required_count
        assert aggregate["gap_count"] == gap_count
        assert aggregate["gap_pct"] == round(100 * gap_count / required_count, 1)
        club_completions = conn.execute("SELECT COUNT(*) FROM history h JOIN employees e ON h.employee_id=e.employee_id WHERE h.event_id='EV_036' AND h.status='completed' AND json_extract(e.data,'$.department')='Backend Development'").fetchone()[0]
    assert len(payload["participation"]) == 36
    assert all(not item["mandatory"] for item in payload["participation"])
    club = next(item for item in payload["participation"] if item["event_id"] == "EV_036")
    assert club["completed"] == club_completions
    e0005 = next(item for item in payload["availability"] if item["employee_id"] == "E0005")
    assert e0005["candidate_count"] > 0 and e0005["state"] == "not_requested_or_stale"

    mandatory = client.get("/api/hr/competencies", params={"mandatory": "mandatory"})
    assert mandatory.status_code == 200
    assert {row["event_id"] for row in mandatory.json()["participation"]} == {"EV_001", "EV_002", "EV_003", "EV_004"}
    all_events = client.get("/api/hr/competencies", params={"mandatory": "all"})
    assert all_events.status_code == 200 and len(all_events.json()["participation"]) == 40


def test_hr_department_skill_matrix_uses_goal_denominators_and_filters(client: TestClient):
    _login(client, "hr@careerquest.test")
    params = {"department": "Backend Development", "grade": "Middle", "type": "hard"}
    response = client.get("/api/hr/competencies", params=params)
    assert response.status_code == 200, response.text
    payload = response.json()
    observed = {(cell["department"], cell["skill_id"]): cell for cell in payload["department_skills"]}

    expected = {}
    critical_people = unconfirmed_goals = 0
    with db.connection() as conn:
        events, skills, profiles = domain.catalog(conn)
        people = [json.loads(row[0]) for row in conn.execute("SELECT data FROM employees")]
        people = [person for person in people if person["department"] == params["department"] and person["grade"] == params["grade"]]
        for person in people:
            goal = domain.goal_for(conn, person, profiles)
            unconfirmed_goals += int(goal["source"] != "explicit")
            levels, _ = domain.replay(conn, person, events)
            rows, _, _ = domain.skill_metrics(levels, goal, skills)
            critical_person = False
            for row in rows:
                if row["type"] != "hard":
                    continue
                key = (person["department"], row["skill_id"])
                counts = expected.setdefault(key, {"required_count": 0, "gap_count": 0, "critical_count": 0})
                counts["required_count"] += 1
                counts["gap_count"] += int(row["gap"] > 0)
                counts["critical_count"] += int(row["gap"] > 0 and row["critical"])
                critical_person = critical_person or bool(row["gap"] and row["critical"])
            critical_people += int(critical_person)

    assert set(observed) == set(expected)
    assert payload["employee_count"] == payload["employees_total"] == len(people)
    assert payload["employees_with_critical_gap"] == critical_people
    assert payload["employees_unconfirmed_goal"] == unconfirmed_goals
    assert 0 <= payload["employees_without_step"] <= len(people)
    for key, counts in expected.items():
        cell = observed[key]
        assert cell["required_count"] == counts["required_count"]
        assert cell["gap_count"] == counts["gap_count"]
        assert cell["critical_count"] == counts["critical_count"]
        assert cell["gap_pct"] == round(100 * counts["gap_count"] / counts["required_count"], 1)


def test_model_failure_uses_explicit_fallback(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    def failed_call(*_args, **_kwargs):
        raise TimeoutError("controlled test timeout")

    monkeypatch.setattr(recommendations, "_call_openai", failed_call)
    _login(client, "e0005@careerquest.test")
    result = client.post("/api/employee/recommendations/generate")
    assert result.status_code == 200, result.text
    assert result.json()["source"] == "fallback"
    assert result.json()["status"] == "fallback"
    assert 1 <= len(result.json()["items"]) <= 3
    assert all(len(item["factors"]) >= 3 for item in result.json()["items"])
