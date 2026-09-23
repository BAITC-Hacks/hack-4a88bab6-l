from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import (
    ActivityHistoryRecord,
    Employee,
    EmployeesDocument,
    Event,
    EventsDocument,
    RoleProfile,
    Skill,
    SkillsDocument,
)


class DatasetError(ValueError):
    """An actionable dataset parsing or cross-reference error."""


@dataclass(frozen=True)
class Dataset:
    employees: list[Employee]
    events: list[Event]
    skills: list[Skill]
    role_profiles: list[RoleProfile]
    history: list[ActivityHistoryRecord]
    as_of_date: Any

    @property
    def employees_by_id(self) -> dict[str, Employee]:
        return {item.employee_id: item for item in self.employees}

    @property
    def events_by_id(self) -> dict[str, Event]:
        return {item.event_id: item for item in self.events}

    @property
    def skills_by_id(self) -> dict[str, Skill]:
        return {item.skill_id: item for item in self.skills}

    @property
    def role_profiles_by_key(self) -> dict[tuple[str, str], RoleProfile]:
        return {(item.role, item.grade): item for item in self.role_profiles}


def _parse_json(raw: bytes, filename: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetError(f"{filename}: не удалось прочитать JSON ({exc})") from exc
    if not isinstance(value, dict):
        raise DatasetError(f"{filename}: ожидался JSON-объект")
    return value


def _validated(model: type, payload: Any, filename: str, row: int | None = None) -> Any:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        errors = []
        for item in exc.errors()[:8]:
            location = ".".join(str(part) for part in item["loc"])
            prefix = f"строка {row}, " if row is not None else ""
            errors.append(f"{prefix}{location}: {item['msg']}")
        suffix = " …" if exc.error_count() > 8 else ""
        raise DatasetError(f"{filename}: " + "; ".join(errors) + suffix) from exc


def _optional(value: str | None) -> str | None:
    return value if value not in (None, "") else None


def _history_from_csv(raw: bytes, filename: str = "activity_history.csv") -> list[ActivityHistoryRecord]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DatasetError(f"{filename}: файл должен быть в UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text))
    required = {
        "record_id", "employee_id", "event_id", "date", "due_date", "status",
        "completion_pct", "score", "feedback_rating", "assigned_by",
    }
    headers = set(reader.fieldnames or [])
    missing = sorted(required - headers)
    if missing:
        raise DatasetError(f"{filename}: отсутствуют столбцы: {', '.join(missing)}")
    records = []
    for row_number, row in enumerate(reader, start=2):
        payload = dict(row)
        for field in ("due_date", "score", "feedback_rating"):
            payload[field] = _optional(payload.get(field))
        records.append(_validated(ActivityHistoryRecord, payload, filename, row_number))
    return records


def dataset_from_bytes(
    employees_raw: bytes,
    events_raw: bytes,
    skills_raw: bytes,
    history_raw: bytes,
) -> Dataset:
    employees_doc = _validated(
        EmployeesDocument,
        _parse_json(employees_raw, "employees.json"),
        "employees.json",
    )
    events_doc = _validated(
        EventsDocument,
        _parse_json(events_raw, "events.json"),
        "events.json",
    )
    skills_doc = _validated(
        SkillsDocument,
        _parse_json(skills_raw, "skills.json"),
        "skills.json",
    )
    if not (
        employees_doc.meta.as_of_date
        == events_doc.meta.as_of_date
        == skills_doc.meta.as_of_date
    ):
        raise DatasetError("employees/events/skills: даты среза as_of_date должны совпадать")

    data = Dataset(
        employees=employees_doc.employees,
        events=events_doc.events,
        skills=skills_doc.skills,
        role_profiles=skills_doc.role_profiles,
        history=_history_from_csv(history_raw),
        as_of_date=employees_doc.meta.as_of_date,
    )
    validate_references(data)
    return data


def validate_references(data: Dataset) -> None:
    errors: list[str] = []
    employee_ids = [item.employee_id for item in data.employees]
    event_ids = [item.event_id for item in data.events]
    skill_ids = [item.skill_id for item in data.skills]
    profile_keys = [(item.role, item.grade) for item in data.role_profiles]
    history_ids = [item.record_id for item in data.history]

    for label, values in (
        ("employee_id", employee_ids), ("event_id", event_ids),
        ("skill_id", skill_ids), ("role_profile", profile_keys),
        ("record_id", history_ids),
    ):
        if len(values) != len(set(values)):
            errors.append(f"повторяются значения {label}")

    employee_map = data.employees_by_id
    event_map = data.events_by_id
    skill_set = set(skill_ids)
    profile_set = set(profile_keys)
    grade_set = {grade for _, grade in profile_keys}
    role_set = {role for role, _ in profile_keys}

    for employee in data.employees:
        if (employee.role, employee.grade) not in profile_set:
            errors.append(
                f"сотрудник {employee.employee_id}: нет role_profile для {employee.role}/{employee.grade}"
            )
        if employee.manager_id and employee.manager_id not in employee_map:
            errors.append(f"сотрудник {employee.employee_id}: manager_id {employee.manager_id} не найден")
        if employee.manager_id == employee.employee_id:
            errors.append(f"сотрудник {employee.employee_id}: не может быть собственным руководителем")
        if employee.career_goal and (employee.career_goal.target_role, employee.career_goal.target_grade) not in profile_set:
            errors.append(
                f"сотрудник {employee.employee_id}: не найдена целевая роль/грейд из career_goal"
            )
        for skill_id in employee.skills:
            if skill_id not in skill_set:
                errors.append(f"сотрудник {employee.employee_id}: неизвестный навык {skill_id}")

    for profile in data.role_profiles:
        for skill_id, level in profile.required_skills.items():
            if skill_id not in skill_set:
                errors.append(f"role_profile {profile.role}/{profile.grade}: неизвестный навык {skill_id}")
            if level < 0 or level > 5:
                errors.append(f"role_profile {profile.role}/{profile.grade}: уровень {skill_id} вне шкалы 0–5")
        for skill_id in profile.critical_skills:
            if skill_id not in skill_set:
                errors.append(f"role_profile {profile.role}/{profile.grade}: неизвестный critical_skill {skill_id}")

    for event in data.events:
        for role in event.target_roles:
            if role not in role_set:
                errors.append(f"активность {event.event_id}: неизвестная целевая роль {role}")
        for grade in event.target_grades:
            if grade not in grade_set:
                errors.append(f"активность {event.event_id}: неизвестный целевой грейд {grade}")
        for item in event.develops_skills:
            if item.skill_id not in skill_set:
                errors.append(f"активность {event.event_id}: неизвестный развиваемый навык {item.skill_id}")
            if item.gain > 5:
                errors.append(f"активность {event.event_id}: gain навыка {item.skill_id} больше 5")
        for skill_id, level in event.prerequisites.items():
            if skill_id not in skill_set:
                errors.append(f"активность {event.event_id}: неизвестный prerequisite {skill_id}")
            if level < 0 or level > 5:
                errors.append(f"активность {event.event_id}: prerequisite {skill_id} вне шкалы 0–5")

    for record in data.history:
        if record.employee_id not in employee_map:
            errors.append(f"запись {record.record_id}: сотрудник {record.employee_id} не найден")
        if record.event_id not in event_map:
            errors.append(f"запись {record.record_id}: активность {record.event_id} не найдена")

    if errors:
        shown = errors[:20]
        suffix = f"; и ещё {len(errors) - len(shown)} ошибок" if len(errors) > len(shown) else ""
        raise DatasetError("Ошибки ссылок в датасете: " + "; ".join(shown) + suffix)


def load_dataset(directory: Path) -> Dataset:
    try:
        return dataset_from_bytes(
            (directory / "employees.json").read_bytes(),
            (directory / "events.json").read_bytes(),
            (directory / "skills.json").read_bytes(),
            (directory / "activity_history.csv").read_bytes(),
        )
    except OSError as exc:
        raise DatasetError(f"не удалось открыть датасет в {directory}: {exc}") from exc
