from __future__ import annotations

import sqlite3

from .dataset import Dataset
from .models import ActivityHistoryRecord, Employee, Event, RoleProfile
from .store import ActivityStore

GRADE_ORDER = ["Junior", "Middle", "Senior", "Lead"]
REPEATABLE_EVENT_IDS = {"EV_036"}


def history_for_employee(
    dataset: Dataset,
    store: ActivityStore,
    employee_id: str,
) -> list[ActivityHistoryRecord]:
    records = [record for record in dataset.history if record.employee_id == employee_id]
    records.extend(store.list_for_employee(employee_id))
    return sorted(records, key=lambda record: (record.date, record.record_id))


def effective_skill_levels(
    employee: Employee,
    events_by_id: dict[str, Event],
    history: list[ActivityHistoryRecord],
) -> dict[str, int]:
    """Apply completed post-review activities on top of the last assessed profile."""
    levels = dict(employee.skills)
    for record in sorted(history, key=lambda item: (item.date, item.record_id)):
        if record.status != "completed" or record.date <= employee.last_review_date:
            continue
        event = events_by_id.get(record.event_id)
        if event is None:
            continue
        for development in event.develops_skills:
            current = levels.get(development.skill_id, 0)
            levels[development.skill_id] = max(
                current,
                min(current + development.gain, development.max_level),
            )
    return levels


def target_profile_for(employee: Employee, dataset: Dataset) -> tuple[RoleProfile | None, str]:
    if employee.career_goal:
        key = (employee.career_goal.target_role, employee.career_goal.target_grade)
        return dataset.role_profiles_by_key.get(key), "career_goal"

    try:
        current_index = GRADE_ORDER.index(employee.grade)
    except ValueError:
        return None, "no_target"
    if current_index + 1 >= len(GRADE_ORDER):
        return None, "no_higher_grade"
    key = (employee.role, GRADE_ORDER[current_index + 1])
    return dataset.role_profiles_by_key.get(key), "next_grade_fallback"


def skill_gap_vector(
    employee: Employee,
    dataset: Dataset,
    effective_levels: dict[str, int],
) -> list[dict]:
    target_profile, _ = target_profile_for(employee, dataset)
    if target_profile is None:
        return []
    skills_by_id = dataset.skills_by_id
    gaps = []
    for skill_id, target_level in target_profile.required_skills.items():
        skill = skills_by_id[skill_id]
        current_level = effective_levels.get(skill_id, 0)
        gaps.append(
            {
                "skill_id": skill_id,
                "name": skill.name,
                "type": skill.type,
                "current_level": current_level,
                "target_level": target_level,
                "gap": max(0, target_level - current_level),
                "is_critical": skill_id in target_profile.critical_skills,
            }
        )
    return sorted(gaps, key=lambda item: (-item["is_critical"], -item["gap"], item["name"]))


def eligible_events(
    employee: Employee,
    dataset: Dataset,
    history: list[ActivityHistoryRecord],
    effective_levels: dict[str, int],
) -> list[Event]:
    completed_ids = {
        record.event_id for record in history if record.status == "completed"
    }
    result = []
    for event in dataset.events:
        if event.mandatory:
            continue
        if employee.role not in event.target_roles or employee.grade not in event.target_grades:
            continue
        if event.event_id in completed_ids and event.event_id not in REPEATABLE_EVENT_IDS:
            continue
        if event.upcoming_sessions and not any(session >= dataset.as_of_date for session in event.upcoming_sessions):
            continue
        if any(effective_levels.get(skill_id, 0) < level for skill_id, level in event.prerequisites.items()):
            continue
        result.append(event)
    return result


def development_activities_for_skill(
    skill_id: str,
    current_level: int,
    gap: int,
    candidates: list[Event],
) -> list[dict]:
    """Return eligible activities that can increase this employee's skill."""
    if gap <= 0:
        return []

    result = []
    for event in candidates:
        development = next(
            (item for item in event.develops_skills if item.skill_id == skill_id),
            None,
        )
        if development is None:
            continue
        projected_level = max(
            current_level,
            min(current_level + development.gain, development.max_level),
        )
        if projected_level <= current_level:
            continue
        result.append(
            {
                "event_id": event.event_id,
                "title": event.title,
                "type": event.type,
                "format": event.format,
                "duration_hours": event.duration_hours,
                "gain": development.gain,
                "max_level": development.max_level,
                "projected_level": projected_level,
                "remaining_gap": max(0, current_level + gap - projected_level),
                "upcoming_sessions": [item.isoformat() for item in event.upcoming_sessions],
            }
        )
    return sorted(
        result,
        key=lambda item: (
            item["remaining_gap"],
            item["duration_hours"],
            item["title"],
        ),
    )


def recommendation_context(
    employee: Employee,
    dataset: Dataset,
    store: ActivityStore,
) -> dict:
    history = history_for_employee(dataset, store, employee.employee_id)
    effective_levels = effective_skill_levels(employee, dataset.events_by_id, history)
    target_profile, target_source = target_profile_for(employee, dataset)
    gaps = skill_gap_vector(employee, dataset, effective_levels)
    candidates = eligible_events(employee, dataset, history, effective_levels)
    for gap in gaps:
        gap["development_activities"] = development_activities_for_skill(
            gap["skill_id"], gap["current_level"], gap["gap"], candidates
        )
    return {
        "employee": employee.model_dump(mode="json"),
        "target_role_profile": target_profile.model_dump(mode="json") if target_profile else None,
        "target_source": target_source,
        "effective_skills": effective_levels,
        "skill_gaps": gaps,
        "activity_history": [record.model_dump(mode="json") for record in history],
        "eligible_events": [
            event.model_dump(mode="json")
            for event in eligible_events(employee, dataset, history, effective_levels)
        ],
        "as_of_date": dataset.as_of_date.isoformat(),
    }


def record_completion(
    employee: Employee,
    event: Event,
    dataset: Dataset,
    store: ActivityStore,
) -> tuple[ActivityHistoryRecord | None, str | None]:
    if event.mandatory:
        return None, "mandatory_activity_cannot_be_completed_as_a_development_step"
    if employee.role not in event.target_roles or employee.grade not in event.target_grades:
        return None, "activity_not_available_for_employee_role_or_grade"

    history = history_for_employee(dataset, store, employee.employee_id)
    if event.event_id not in REPEATABLE_EVENT_IDS and any(
        record.event_id == event.event_id and record.status == "completed"
        for record in history
    ):
        return None, "activity_already_completed"

    levels = effective_skill_levels(employee, dataset.events_by_id, history)
    if any(levels.get(skill_id, 0) < level for skill_id, level in event.prerequisites.items()):
        return None, "activity_prerequisites_not_met"

    try:
        record = store.complete(employee.employee_id, event.event_id, dataset.as_of_date)
    except sqlite3.IntegrityError as exc:
        # SQLite's unique constraint also protects against duplicate concurrent submissions.
        if "UNIQUE constraint failed" in str(exc):
            return None, "activity_already_completed"
        raise
    return record, None
