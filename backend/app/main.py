from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from . import recommendation_engine
from .dataset import Dataset, DatasetError, dataset_from_bytes, load_dataset, validate_references
from .models import RecommendationsDocument
from .service import (
    development_activities_for_skill,
    effective_skill_levels,
    eligible_events,
    history_for_employee,
    recommendation_context,
    record_completion,
    skill_gap_vector,
    target_profile_for,
)
from .store import ActivityStore

ROOT_DIR = Path(__file__).resolve().parents[2]
DATASET_DIR = Path(os.getenv("DATASET_DIR", ROOT_DIR / "career_quest_dataset"))
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", ROOT_DIR / "backend" / "data" / "career_quest.sqlite3"))

app = FastAPI(title="Career Quest API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
activity_store = ActivityStore(DATABASE_PATH)
try:
    app.state.dataset = load_dataset(DATASET_DIR)
    app.state.dataset_error = None
except DatasetError as exc:
    app.state.dataset = None
    app.state.dataset_error = str(exc)


def get_dataset(request: Request) -> Dataset:
    dataset = request.app.state.dataset
    if dataset is None:
        raise HTTPException(
            status_code=503,
            detail={"message": "Датасет не загружен", "errors": [request.app.state.dataset_error]},
        )
    return dataset


def get_employee_or_404(dataset: Dataset, employee_id: str):
    employee = dataset.employees_by_id.get(employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail=f"Сотрудник {employee_id} не найден")
    return employee


def _history_with_titles(dataset: Dataset, employee_id: str) -> list[dict]:
    records = history_for_employee(dataset, activity_store, employee_id)
    result = []
    for record in records:
        payload = record.model_dump(mode="json")
        event = dataset.events_by_id.get(record.event_id)
        payload["event_title"] = event.title if event else None
        result.append(payload)
    return result


@app.get("/api/health")
def health(request: Request) -> dict:
    dataset = request.app.state.dataset
    return {
        "status": "ok" if dataset else "dataset_error",
        "dataset_loaded": dataset is not None,
        "dataset_error": request.app.state.dataset_error,
    }


@app.get("/api/employees")
def list_employees(request: Request) -> dict:
    dataset = get_dataset(request)
    return {
        "employees": [
            {
                "employee_id": employee.employee_id,
                "full_name": employee.full_name,
                "department": employee.department,
                "role": employee.role,
                "grade": employee.grade,
                "preferred_language": employee.preferred_language,
            }
            for employee in sorted(dataset.employees, key=lambda item: item.full_name)
        ]
    }


@app.get("/api/employees/{employee_id}")
def get_employee(employee_id: str, request: Request) -> dict:
    dataset = get_dataset(request)
    employee = get_employee_or_404(dataset, employee_id)
    history = history_for_employee(dataset, activity_store, employee_id)
    levels = effective_skill_levels(employee, dataset.events_by_id, history)
    target_profile, target_source = target_profile_for(employee, dataset)
    gaps = skill_gap_vector(employee, dataset, levels)
    candidates = eligible_events(employee, dataset, history, levels)
    for gap in gaps:
        gap["development_activities"] = development_activities_for_skill(
            gap["skill_id"], gap["current_level"], gap["gap"], candidates
        )
    gaps_by_id = {item["skill_id"]: item for item in gaps}
    profile_skills = []
    target_skill_ids = target_profile.required_skills if target_profile else {}
    for skill_id in target_skill_ids:
        skill = dataset.skills_by_id[skill_id]
        target = gaps_by_id.get(skill_id)
        profile_skills.append({
            "skill_id": skill_id,
            "name": skill.name,
            "type": skill.type,
            "category": skill.category,
            "level": levels.get(skill_id, 0),
            "target_level": target["target_level"],
            "gap": target["gap"],
            "is_critical": target["is_critical"],
            "development_activities": target["development_activities"],
        })
    employee_payload = employee.model_dump(mode="json")
    employee_payload["skills"] = {
        skill_id: levels.get(skill_id, 0) for skill_id in target_skill_ids
    }
    return {
        "employee": employee_payload,
        "target_role_profile": target_profile.model_dump(mode="json") if target_profile else None,
        "target_source": target_source,
        "skills": {
            "hard": [item for item in profile_skills if item["type"] == "hard"],
            "soft": [item for item in profile_skills if item["type"] == "soft"],
        },
        "skill_gaps": gaps,
        "activity_history": _history_with_titles(dataset, employee_id),
    }


@app.get("/api/employees/{employee_id}/recommendation-context")
def get_recommendation_context(employee_id: str, request: Request) -> dict:
    dataset = get_dataset(request)
    employee = get_employee_or_404(dataset, employee_id)
    return recommendation_context(employee, dataset, activity_store)


@app.get("/api/employees/{employee_id}/recommendations")
def get_recommendations(employee_id: str, request: Request) -> dict:
    dataset = get_dataset(request)
    employee = get_employee_or_404(dataset, employee_id)
    context = recommendation_context(employee, dataset, activity_store)
    try:
        result = recommendation_engine.recommend(context)
        response = RecommendationsDocument.model_validate(result)
    except recommendation_engine.RecommendationEngineUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except recommendation_engine.RecommendationEngineError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=502,
            detail={"message": "Модуль рекомендаций вернул данные неверного формата", "errors": exc.errors()},
        ) from exc

    eligible_ids = {event["event_id"] for event in context["eligible_events"]}
    unknown_ids = [item.event_id for item in response.recommendations if item.event_id not in eligible_ids]
    if unknown_ids:
        raise HTTPException(
            status_code=502,
            detail=f"Модуль рекомендаций вернул недоступные активности: {', '.join(unknown_ids)}",
        )
    recommended_ids = [item.event_id for item in response.recommendations]
    if len(recommended_ids) != len(set(recommended_ids)):
        raise HTTPException(status_code=502, detail="Модуль рекомендаций повторил одну активность")
    ranks = sorted(item.rank for item in response.recommendations)
    if ranks != list(range(1, len(ranks) + 1)):
        raise HTTPException(status_code=502, detail="Ранги рекомендаций должны идти подряд с 1")
    return response.model_dump(mode="json")


@app.post("/api/employees/{employee_id}/activities/{event_id}/complete")
def complete_activity(employee_id: str, event_id: str, request: Request) -> dict:
    dataset = get_dataset(request)
    employee = get_employee_or_404(dataset, employee_id)
    event = dataset.events_by_id.get(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Активность {event_id} не найдена")

    record, error = record_completion(employee, event, dataset, activity_store)
    if error == "activity_already_completed":
        raise HTTPException(status_code=409, detail="Эта активность уже отмечена выполненной")
    if error:
        raise HTTPException(status_code=422, detail=error)

    updated_history = history_for_employee(dataset, activity_store, employee_id)
    updated_levels = effective_skill_levels(employee, dataset.events_by_id, updated_history)
    return {
        "completion": record.model_dump(mode="json"),
        "effective_skills": updated_levels,
    }


@app.get("/api/hr/summary")
def hr_summary(request: Request) -> dict:
    dataset = get_dataset(request)
    gap_totals: dict[str, dict] = {}
    participation: dict[str, int] = {}
    no_eligible_events = []

    for employee in dataset.employees:
        history = history_for_employee(dataset, activity_store, employee.employee_id)
        levels = effective_skill_levels(employee, dataset.events_by_id, history)
        for gap in skill_gap_vector(employee, dataset, levels):
            if gap["gap"] <= 0:
                continue
            summary = gap_totals.setdefault(
                gap["skill_id"],
                {"skill_id": gap["skill_id"], "name": gap["name"], "type": gap["type"],
                 "employees_with_gap": 0, "total_gap_levels": 0, "critical_for_count": 0},
            )
            summary["employees_with_gap"] += 1
            summary["total_gap_levels"] += gap["gap"]
            summary["critical_for_count"] += int(gap["is_critical"])
        if not eligible_events(employee, dataset, history, levels):
            no_eligible_events.append(
                {"employee_id": employee.employee_id, "full_name": employee.full_name}
            )

    for record in dataset.history:
        participation[record.status] = participation.get(record.status, 0) + 1
    for employee in dataset.employees:
        for record in activity_store.list_for_employee(employee.employee_id):
            participation[record.status] = participation.get(record.status, 0) + 1

    skills = sorted(
        gap_totals.values(),
        key=lambda item: (-item["employees_with_gap"], -item["total_gap_levels"], item["name"]),
    )
    return {
        "employees_count": len(dataset.employees),
        "skills_with_most_gaps": skills,
        "participation_by_status": participation,
        "employees_without_eligible_events": no_eligible_events,
    }


def _merge_items(existing: list, incoming: list, key_fn, label: str) -> tuple[list, int]:
    merged = {key_fn(item): item for item in existing}
    added = 0
    for item in incoming:
        key = key_fn(item)
        previous = merged.get(key)
        if previous is not None:
            if previous.model_dump(mode="json") != item.model_dump(mode="json"):
                raise DatasetError(f"импорт: {label} с ID {key} уже существует, но содержимое отличается")
            continue
        merged[key] = item
        added += 1
    return list(merged.values()), added


@app.post("/api/data/import")
async def import_dataset(
    request: Request,
    employees_file: UploadFile = File(...),
    events_file: UploadFile = File(...),
    skills_file: UploadFile = File(...),
    history_file: UploadFile = File(...),
) -> dict:
    current = get_dataset(request)
    try:
        incoming = dataset_from_bytes(
            await employees_file.read(),
            await events_file.read(),
            await skills_file.read(),
            await history_file.read(),
        )
        if incoming.as_of_date != current.as_of_date:
            raise DatasetError(
                f"дата среза импортируемого набора ({incoming.as_of_date}) "
                f"не совпадает с текущей ({current.as_of_date})"
            )

        employees, new_employees = _merge_items(
            current.employees, incoming.employees, lambda item: item.employee_id, "сотрудник"
        )
        events, new_events = _merge_items(
            current.events, incoming.events, lambda item: item.event_id, "активность"
        )
        skills, new_skills = _merge_items(
            current.skills, incoming.skills, lambda item: item.skill_id, "навык"
        )
        role_profiles, new_profiles = _merge_items(
            current.role_profiles, incoming.role_profiles,
            lambda item: (item.role, item.grade), "профиль роли/грейда"
        )
        history, new_history = _merge_items(
            current.history, incoming.history, lambda item: item.record_id, "запись истории"
        )
        merged = Dataset(
            employees=employees,
            events=events,
            skills=skills,
            role_profiles=role_profiles,
            history=history,
            as_of_date=current.as_of_date,
        )
        validate_references(merged)
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    request.app.state.dataset = merged
    return {
        "message": "Данные проверены и добавлены",
        "added": {
            "employees": new_employees,
            "events": new_events,
            "skills": new_skills,
            "role_profiles": new_profiles,
            "history_records": new_history,
        },
        "totals": {
            "employees": len(merged.employees),
            "events": len(merged.events),
            "skills": len(merged.skills),
            "history_records": len(merged.history),
        },
    }
