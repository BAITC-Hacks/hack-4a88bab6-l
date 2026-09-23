# Career Quest — standalone ayko API

For the team's Russian-language setup guide, including Windows startup when PowerShell scripts are disabled, see the [root README](../README.md). This file describes the standalone `app.ayko_main:app` backend contract. The React application uses the separate `backend.app.main:app` entrypoint and different API/database; see the root README.

Python/FastAPI backend for the Career Quest demo. It loads the checked-in dataset, validates its structure and references, exposes employee/HR data, generates grounded activity recommendations through the OpenAI API, and saves completed development activities to SQLite.

## Run locally

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements-ayko.txt
uvicorn app.ayko_main:app --app-dir backend --port 8001 --reload
```

Before starting the backend, install dependencies and set your OpenAI API key in the same PowerShell window. Never commit or share the key:

```powershell
python -m pip install -r backend/requirements-ayko.txt
$env:OPENAI_API_KEY = "your-api-key"
# Optional: choose a model enabled for your API account
$env:OPENAI_MODEL = "gpt-6-astra"
uvicorn app.ayko_main:app --app-dir backend --port 8001 --reload
```

The recommendation endpoint is `GET /api/employees/{employee_id}/recommendations`. It uses the same current profile, effective skills, history, and target gaps as the employee profile endpoint, then asks OpenAI to rank only eligible activities that can raise a target skill. The API validates event IDs, duplicate selections, rank order, and at least three distinct evidence factors. Missing API key returns `503`; provider or response errors return `502`.

The API docs are at `http://127.0.0.1:8001/docs`. By default, the data folder is `career_quest_dataset/` and the SQLite file is `backend/data/career_quest.sqlite3`. Override them with `DATASET_DIR` and `DATABASE_PATH` environment variables.

## Routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/hr` | Simple Russian-language HR dashboard page |
| POST | `/api/hr/login` | Verify the configured HR password and create an HttpOnly session cookie |
| POST | `/api/hr/logout` | Invalidate the HR session |
| GET | `/api/hr/session` | Check the current HR session |
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

## HR dashboard

Implementation specification: [HR_VIEW_TASK.md](HR_VIEW_TASK.md).

Stop an existing server on port 8001 first. From the repository root, start the HR-enabled demo with:

```powershell
.\backend\start_hr.ps1
```

The script prompts for a password without displaying it, sets `HR_PASSWORD` for the server process, and runs the app using the repository's virtual environment. Open `http://127.0.0.1:8001/hr` and enter that password. Existing `OPENAI_API_KEY` configuration is inherited; the HR dashboard itself does not call OpenAI.

Alternatively, set `HR_PASSWORD` in your server environment before your usual uvicorn command. There is no default HR password. `/api/hr/login` accepts `{"password":"..."}` and sets an opaque HttpOnly, SameSite=Strict session cookie. `GET /api/hr/summary` requires that session (401 otherwise); sending a role name from the frontend does not grant access. Sessions expire after eight hours or on server restart/reload. This demo uses one HR account and one server worker; employee endpoints and data import retain their existing demo access behavior.

`GET /api/hr/summary` returns `overview`, `employees`, `skill_gaps`, `department_skill_gaps`, `employees_without_recommendation`, `employees_without_target`, `activity_statuses`, `activity_participation`, and `filter_options`. Query parameters: `department`, `role`, `grade`, `critical_only`. All sections use the same selected employee cohort. `critical_only=true` selects employees with a critical gap and restricts skill/heatmap rows to critical target requirements; next-step availability still uses all useful target-skill candidates. Empty outcome denominators and missing averages are returned as `null`, not 0.

The five KPI cards open employee lists or the relevant dashboard section. The page includes sortable skill bars/table, a department heatmap with cell details, separate no-step and no-target lists, voluntary/mandatory participation tabs, event-level outcome and assignment statistics, and an employee profile dialog with the career trajectory, current skills, history, and useful next steps. Filters refresh all sections together. Data refreshes every 30 seconds while the tab is visible, when returning to the tab, or with the Refresh button.

`recommendation_candidates()` in `service.py` is the shared source for profile `next_steps`, recommendation context, AI input, model-response validation, and HR. Candidates include `improved_skills` with current/target/projected levels and gap reduction, `requirements_closed_count`, and `total_gap_reduction`. `eligible_events` in the context remains a compatibility alias of `recommendation_candidates`; it now contains only useful candidates. Scheduled events need a session on/after the snapshot date; self-paced events do not.

The no-step list includes only employees with a defined target and positive gaps but no useful candidate. It contains computed reasons and per-skill event evidence. Employees without a target and employees who already meet all target requirements are not counted in that list. No AI calls or stored AI-result assumptions are used for this calculation.

Completion rate is `completed / (completed + dropped + no_show + declined)`, separately for voluntary and mandatory activities. In-progress and overdue records remain visible in status statistics, but are excluded from that denominator. Unique participants, participation records, ratings/scores, and assignment-source counts are separate fields.

Control values with the starter dataset and an empty completion database: **200 employees, 190 with targets, 187 with critical gaps, 30 without useful steps, 10 without targets; voluntary completion 1044/1503 = 69.46%**. SQLite completions and imported profiles/history dynamically change these values. Additive imports currently remain in memory until server restart; SQLite completions persist.

Run isolated regression checks (temporary SQLite databases, no OpenAI calls):

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
```

## Data handling notes

- The dataset README defines `2026-10-01` as the application date. Upcoming sessions are checked against that date.
- Missing employee skill levels count as 0. Targets come from the requested `career_goal` role profile; if it is absent, the API falls back to the next grade in the same role when available.
- Employee skill levels are snapshots from `last_review_date`. Completed activities after that date are applied when calculating effective levels; an activity raises a skill by `gain` up to `max_level`, without reducing an already higher level.
- Mandatory activities are not offered as development recommendations. Completed activities are not repeated except `EV_036`.
- HR analytics requires the server-verified HR session described above. The rest of this demo has no employee login enforcement.
