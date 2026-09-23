from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetMeta(StrictModel):
    dataset: str
    version: str
    as_of_date: date


class CareerGoal(StrictModel):
    target_role: str
    target_grade: str


class Employee(StrictModel):
    employee_id: str
    full_name: str
    department: str
    role: str
    grade: str
    manager_id: str | None
    hire_date: date
    tenure_months: int = Field(ge=0)
    work_format: Literal["office", "hybrid", "remote"]
    preferred_language: Literal["kk", "ru", "en"]
    career_goal: CareerGoal | None
    skills: dict[str, int] = Field(default_factory=dict)
    last_review_date: date

    @field_validator("skills")
    @classmethod
    def validate_skill_levels(cls, value: dict[str, int]) -> dict[str, int]:
        if any(level < 0 or level > 5 for level in value.values()):
            raise ValueError("уровень каждого навыка должен быть от 0 до 5")
        return value


class EmployeesDocument(StrictModel):
    meta: DatasetMeta
    employees: list[Employee]


class Skill(StrictModel):
    skill_id: str
    name: str
    type: Literal["hard", "soft"]
    category: str
    description: str


class RoleProfile(StrictModel):
    role: str
    grade: str
    required_skills: dict[str, int]
    critical_skills: list[str]


class SkillsDocument(StrictModel):
    meta: DatasetMeta
    proficiency_scale: dict[str, str]
    skills: list[Skill]
    role_profiles: list[RoleProfile]


class SkillDevelopment(StrictModel):
    skill_id: str
    gain: int = Field(ge=0)
    max_level: int = Field(ge=0, le=5)


class Event(StrictModel):
    event_id: str
    title: str
    description: str
    type: str
    format: Literal["online", "offline", "self_paced"]
    duration_hours: float = Field(gt=0)
    mandatory: bool
    target_roles: list[str]
    target_grades: list[str]
    develops_skills: list[SkillDevelopment]
    prerequisites: dict[str, int]
    upcoming_sessions: list[date]


class EventsDocument(StrictModel):
    meta: DatasetMeta
    events: list[Event]


HistoryStatus = Literal[
    "completed", "in_progress", "dropped", "no_show", "declined", "overdue"
]


class ActivityHistoryRecord(StrictModel):
    record_id: str
    employee_id: str
    event_id: str
    date: date
    due_date: date | None
    status: HistoryStatus
    completion_pct: int = Field(ge=0, le=100)
    score: int | None = Field(default=None, ge=0, le=100)
    feedback_rating: int | None = Field(default=None, ge=1, le=5)
    assigned_by: Literal["self", "manager", "hr"]

    @model_validator(mode="after")
    def validate_status_progress(self) -> "ActivityHistoryRecord":
        allowed = {
            "completed": (100, 100),
            "in_progress": (0, 95),
            "dropped": (5, 95),
            "no_show": (0, 0),
            "declined": (0, 0),
            "overdue": (0, 95),
        }[self.status]
        if not allowed[0] <= self.completion_pct <= allowed[1]:
            raise ValueError(
                f"completion_pct для статуса {self.status} должен быть от {allowed[0]} до {allowed[1]}"
            )
        return self


class SkillGap(StrictModel):
    skill_id: str
    name: str
    type: Literal["hard", "soft"]
    current_level: int = Field(ge=0, le=5)
    target_level: int = Field(ge=0, le=5)
    gap: int = Field(ge=0, le=5)
    is_critical: bool


class RecommendationExplanation(StrictModel):
    factor: Literal["grade_gap", "skill_gap", "participation_history", "next_grade_requirements"]
    detail: str


class Recommendation(StrictModel):
    event_id: str
    rank: int = Field(ge=1, le=3)
    explanations: list[RecommendationExplanation] = Field(min_length=3)

    @model_validator(mode="after")
    def validate_distinct_factors(self) -> "Recommendation":
        factors = [item.factor for item in self.explanations]
        if len(set(factors)) < 3:
            raise ValueError("объяснение должно опираться минимум на три разных фактора")
        return self


class RecommendationsDocument(StrictModel):
    recommendations: list[Recommendation] = Field(max_length=3)
