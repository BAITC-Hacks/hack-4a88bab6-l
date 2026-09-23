"""HR analytics over effective skills and the current dataset, without AI calls."""
from __future__ import annotations

from .dataset import Dataset
from .store import ActivityStore
from .service import (effective_skill_levels, history_for_employee, recommendation_blockers,
                      recommendation_candidates, skill_gap_vector, target_profile_for)

STATUSES = ("completed", "in_progress", "dropped", "no_show", "declined", "overdue")
FINAL_OUTCOMES = ("completed", "dropped", "no_show", "declined")


def _skill_bucket(gap: dict) -> dict:
    return {"skill_id": gap["skill_id"], "name": gap["name"], "type": gap["type"],
            "employees_requiring_skill": 0, "employees_with_gap": 0, "gap_rate": 0.0,
            "total_missing_levels": 0, "critical_gap_count": 0}


def _add_gap(bucket: dict, gap: dict) -> None:
    bucket["employees_requiring_skill"] += 1
    bucket["employees_with_gap"] += int(gap["gap"] > 0)
    bucket["total_missing_levels"] += gap["gap"]
    bucket["critical_gap_count"] += int(gap["gap"] > 0 and gap["is_critical"])
    bucket["gap_rate"] = bucket["employees_with_gap"] / bucket["employees_requiring_skill"]


def build_hr_summary(dataset: Dataset, store: ActivityStore, *, department: str | None = None,
                     role: str | None = None, grade: str | None = None,
                     critical_only: bool = False) -> dict:
    employees = []
    without_step = []
    without_target = []
    skills: dict[str, dict] = {}
    department_skills: dict[tuple[str, str], dict] = {}
    participation = {}
    for event in dataset.events:
        participation[event.event_id] = {
            "event_id": event.event_id, "title": event.title, "type": event.type,
            "format": event.format, "mandatory": event.mandatory,
            "total_records": 0, "employees_count": 0,
            "status_counts": dict.fromkeys(STATUSES, 0),
            "assigned_by_counts": dict.fromkeys(("self", "manager", "hr"), 0),
            "completion_rate": None, "completed_outcomes": 0,
            "average_feedback_rating": None, "average_score": None,
        }
    participants: dict[str, set[str]] = {key: set() for key in participation}
    feedback: dict[str, list[int]] = {key: [] for key in participation}
    scores: dict[str, list[int]] = {key: [] for key in participation}
    statuses = {group: dict.fromkeys(STATUSES, 0) for group in ("voluntary", "mandatory")}
    critical_count = target_count = 0
    events_by_id = dataset.events_by_id

    for employee in dataset.employees:
        if ((department is not None and employee.department != department)
                or (role is not None and employee.role != role)
                or (grade is not None and employee.grade != grade)):
            continue
        history = history_for_employee(dataset, store, employee.employee_id)
        levels = effective_skill_levels(employee, events_by_id, history)
        target, source = target_profile_for(employee, dataset)
        gaps = skill_gap_vector(employee, dataset, levels)
        positive_gaps = [gap for gap in gaps if gap["gap"] > 0]
        critical_gaps = [gap for gap in positive_gaps if gap["is_critical"]]
        if critical_only and not critical_gaps:
            continue
        candidates = recommendation_candidates(employee, dataset, history, levels)
        target_count += int(target is not None)
        critical_count += int(bool(critical_gaps))
        row = {
            "employee_id": employee.employee_id, "full_name": employee.full_name,
            "department": employee.department, "role": employee.role, "grade": employee.grade,
            "target": {"role": target.role, "grade": target.grade} if target else None,
            "target_source": source, "critical_gaps": critical_gaps,
            "other_gaps_count": len(positive_gaps) - len(critical_gaps),
            "useful_candidates_count": len(candidates),
            "profile_url": f"/api/employees/{employee.employee_id}",
        }
        employees.append(row)
        if target is None or (positive_gaps and not candidates):
            blockers = recommendation_blockers(employee, dataset, history, levels)
            blocked_row = {**row, "reasons": sorted({item["code"] for item in blockers}),
                           "reason_details": blockers}
            if target is None:
                without_target.append(blocked_row)
            else:
                without_step.append(blocked_row)
        for gap in gaps:
            if critical_only and not gap["is_critical"]:
                continue
            _add_gap(skills.setdefault(gap["skill_id"], _skill_bucket(gap)), gap)
            key = (employee.department, gap["skill_id"])
            bucket = department_skills.setdefault(key, {**_skill_bucket(gap), "department": employee.department})
            _add_gap(bucket, gap)

        for record in history:
            event = events_by_id.get(record.event_id)
            if event is None:
                continue
            event_id = event.event_id
            item = participation[event_id]
            item["total_records"] += 1
            item["status_counts"][record.status] += 1
            item["assigned_by_counts"][record.assigned_by] += 1
            participants[event_id].add(employee.employee_id)
            statuses["mandatory" if event.mandatory else "voluntary"][record.status] += 1
            if record.feedback_rating is not None:
                feedback[event_id].append(record.feedback_rating)
            if record.score is not None:
                scores[event_id].append(record.score)

    for event_id, item in participation.items():
        item["employees_count"] = len(participants[event_id])
        count = sum(item["status_counts"][status] for status in FINAL_OUTCOMES)
        item["completed_outcomes"] = count
        item["completion_rate"] = item["status_counts"]["completed"] / count if count else None
        item["average_feedback_rating"] = sum(feedback[event_id]) / len(feedback[event_id]) if feedback[event_id] else None
        item["average_score"] = sum(scores[event_id]) / len(scores[event_id]) if scores[event_id] else None

    voluntary_outcomes = sum(statuses["voluntary"][key] for key in FINAL_OUTCOMES)
    skill_rows = sorted(skills.values(), key=lambda item: (-item["employees_with_gap"], item["name"]))
    activity_rows = sorted(participation.values(), key=lambda item: (-item["employees_count"], item["title"]))
    overview = {
        "employees_total": len(employees), "employees_with_target": target_count,
        "employees_with_critical_gap": critical_count,
        "employees_without_recommendation": len(without_step),
        "employees_without_target": len(without_target),
        "voluntary_completion_rate": statuses["voluntary"]["completed"] / voluntary_outcomes if voluntary_outcomes else None,
        "voluntary_completed": statuses["voluntary"]["completed"],
        "voluntary_completed_outcomes": voluntary_outcomes,
    }
    return {
        "as_of_date": dataset.as_of_date.isoformat(), "overview": overview,
        "filters": {"department": department, "role": role, "grade": grade, "critical_only": critical_only},
        "filter_options": {
            "departments": sorted({item.department for item in dataset.employees}),
            "roles": sorted({item.role for item in dataset.employees}),
            "grades": sorted({item.grade for item in dataset.employees}),
        },
        "employees": sorted(employees, key=lambda item: item["full_name"]),
        "skill_gaps": skill_rows,
        "department_skill_gaps": sorted(department_skills.values(), key=lambda item: (item["name"], item["department"])),
        "employees_without_recommendation": sorted(without_step, key=lambda item: item["full_name"]),
        "employees_without_target": sorted(without_target, key=lambda item: item["full_name"]),
        "activity_statuses": statuses, "activity_participation": activity_rows,
        # Compatibility aliases for existing frontend consumers.
        "employees_count": len(employees),
        "skills_with_most_gaps": [{**item, "total_gap_levels": item["total_missing_levels"],
                                   "critical_for_count": item["critical_gap_count"]} for item in skill_rows],
        "participation_by_activity": activity_rows,
        "participation_by_status": {key: sum(group[key] for group in statuses.values()) for key in STATUSES},
        "employees_without_recommended_step": without_step,
    }
