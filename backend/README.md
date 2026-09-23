# Career Quest API

Python/FastAPI backend for the Career Quest demo. It loads the checked-in dataset, validates its structure and references, exposes employee/HR data, generates grounded activity recommendations through the OpenAI API, and saves completed development activities to SQLite.

## Run locally

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
uvicorn app.main:app --app-dir backend --reload
```

Before starting the backend, install dependencies and set your OpenAI API key in the same PowerShell window. Never commit or share the key:

```powershell
python -m pip install -r backend/requirements.txt
$env:OPENAI_API_KEY = "your-api-key"
# Optional: choose a model enabled for your API account
$env:OPENAI_MODEL = "gpt-6-astra"
uvicorn app.main:app --app-dir backend --reload
```

The recommendation endpoint is `GET /api/employees/{employee_id}/recommendations`. It uses the same current profile, effective skills, history, and target gaps as the employee profile endpoint, then asks OpenAI to rank only eligible activities that can raise a target skill. The API validates event IDs, duplicate selections, rank order, and at least three distinct evidence factors. Missing API key returns `503`; provider or response errors return `502`.

The API docs are at `http://127.0.0.1:8000/docs`. By default, the data folder is `career_quest_dataset/` and the SQLite file is `backend/data/career_quest.sqlite3`. Override them with `DATASET_DIR` and `DATABASE_PATH` environment variables.

## Routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/hr` | Simple Russian-language HR dashboard page |
| GET | `/api/health` | Dataset load status and validation error, if any |
| GET | `/api/employees` | Employee list for the UI |
| GET | `/api/employees/{employee_id}` | Profile, hard/soft skill values, target gaps with activities that can raise each skill, and activity history |
| GET | `/api/employees/{employee_id}/recommendation-context` | Agreed input payload for the recommendation module |
| GET | `/api/employees/{employee_id}/recommendations` | Returns up to 3 OpenAI-ranked activities, each with at least 3 distinct evidence factors |
| POST | `/api/employees/{employee_id}/activities/{event_id}/complete` | Saves completion and returns recalculated skill levels |
| GET | `/api/hr/summary` | HR dashboard data: most common skill gaps, participation per activity/status, and employees without a skill-relevant next step |
| POST | `/api/data/import` | Validates and additively imports a matching four-file dataset bundle |

Import multipart field names: `employees_file`, `events_file`, `skills_file`, `history_file`. Existing records with the same ID and identical contents are ignored; conflicting IDs reject the entire import. References are validated after merging.

## Recommendation contract

`GET /api/employees/{employee_id}/recommendation-context` returns `employee`, `target_role_profile`, `skill_gaps`, `activity_history`, `eligible_events`, `effective_skills`, `target_source`, and the dataset's fixed `as_of_date`. The employee profile's hard/soft skill lists include only skills defined for the target role/grade, so `target_level` is always present. Each positive skill gap includes `development_activities`: eligible activities that develop that skill and can increase its current level, with their gain, cap, projected level, and remaining gap. Skills without a positive target gap return an empty activity list in the employee profile.

The OpenAI module in `app/recommendation_engine.py` selects activities. The endpoint enriches each result from the trusted event catalog with an `event` object (title, description, type, format, duration, and upcoming sessions), while preserving `event_id` for completion and history links. A response looks like:

```json
{
  "recommendations": [
    {
      "event_id": "EV_005",
      "event": {
        "title": "Readable activity title",
        "description": "Activity description from events.json",
        "type": "course",
        "format": "online",
        "duration_hours": 4.0,
        "upcoming_sessions": ["2026-10-12"]
      },
      "rank": 1,
      "explanations": [
        {"factor": "grade_gap", "detail": "..."},
        {"factor": "skill_gap", "detail": "..."},
        {"factor": "participation_history", "detail": "..."}
      ]
    }
  ]
}
```

Every recommendation must refer to an eligible event and include at least three different explanation factors (`grade_gap`, `skill_gap`, `participation_history`, `next_grade_requirements`). The service sends only the minimum career context needed to rank activities; it omits employee name, ID, manager, and other unused profile fields. If no eligible activity can improve a target skill, the endpoint returns an empty list without calling OpenAI.

The HR summary reports skill-gap prevalence, participation grouped by activity and status, and employees for whom no eligible activity can currently raise a skill with a positive target gap. This last list is computed from the catalog without making OpenAI calls; it means there is no available skill-relevant next step, not that an AI recommendation request was previously run.

## Data handling notes

- The dataset README defines `2026-10-01` as the application date. Upcoming sessions are checked against that date.
- Missing employee skill levels count as 0. Targets come from the requested `career_goal` role profile; if it is absent, the API falls back to the next grade in the same role when available.
- Employee skill levels are snapshots from `last_review_date`. Completed activities after that date are applied when calculating effective levels; an activity raises a skill by `gain` up to `max_level`, without reducing an already higher level.
- Mandatory activities are not offered as development recommendations. Completed activities are not repeated except `EV_036`.
- The API currently has no login/session enforcement. Connect the agreed Worker/HR role selector to backend access control before exposing employee or HR data beyond the local demo.
