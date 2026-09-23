from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.dataset import load_dataset
from app.hr import build_hr_summary
from app.models import ActivityHistoryRecord
from app.recommendation_engine import _model_input
from app.service import (effective_skill_levels, eligible_events, history_for_employee, recommendation_blockers,
                         recommendation_candidates, recommendation_context, target_profile_for)
from app.store import ActivityStore

DATA_PATH = Path(__file__).resolve().parents[2] / "career_quest_dataset"


class HRFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(DATA_PATH)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ActivityStore(Path(self.temp.name) / "hr.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def candidate_employee(self):
        for employee in self.dataset.employees:
            history = history_for_employee(self.dataset, self.store, employee.employee_id)
            levels = effective_skill_levels(employee, self.dataset.events_by_id, history)
            candidates = recommendation_candidates(employee, self.dataset, history, levels)
            if candidates:
                return employee, history, levels, candidates
        self.fail("No employee with candidates")

class HRTests(HRFixture):
    def test_control_values(self):
        summary = build_hr_summary(self.dataset, self.store)
        overview = summary["overview"]
        for key, expected in {
            "employees_total": 200, "employees_with_target": 190,
            "employees_with_critical_gap": 187, "employees_without_recommendation": 30,
            "employees_without_target": 10, "voluntary_completed": 1044,
            "voluntary_completed_outcomes": 1503,
        }.items():
            self.assertEqual(overview[key], expected, key)
        self.assertAlmostEqual(overview["voluntary_completion_rate"], 1044 / 1503)
        self.assertEqual(summary["activity_statuses"]["voluntary"], {
            "completed": 1044, "in_progress": 16, "dropped": 160,
            "no_show": 195, "declined": 104, "overdue": 0,
        })
        self.assertTrue(all(item["reasons"] for item in summary["employees_without_recommendation"]))
        self.assertTrue(all(item["reasons"] == ["career_goal_missing"]
                            for item in summary["employees_without_target"]))
        for skill in summary["skill_gaps"]:
            required = sum(bool((target := target_profile_for(e, self.dataset)[0])
                                and skill["skill_id"] in target.required_skills)
                           for e in self.dataset.employees)
            self.assertEqual(skill["employees_requiring_skill"], required)
            self.assertAlmostEqual(skill["gap_rate"], skill["employees_with_gap"] / required)

    def test_filters_apply_to_employee_cohort_and_participation(self):
        department = self.dataset.employees[0].department
        role = self.dataset.employees[0].role
        grade = self.dataset.employees[0].grade
        selected = [e for e in self.dataset.employees if (e.department, e.role, e.grade) == (department, role, grade)]
        summary = build_hr_summary(self.dataset, self.store, department=department, role=role, grade=grade)
        self.assertEqual(summary["overview"]["employees_total"], len(selected))
        employee_ids = {e.employee_id for e in selected}
        expected_records = sum(r.employee_id in employee_ids for r in self.dataset.history)
        self.assertEqual(sum(i["total_records"] for i in summary["activity_participation"]), expected_records)
        self.assertTrue(all(i["department"] == department for i in summary["department_skill_gaps"]))
        critical = build_hr_summary(self.dataset, self.store, critical_only=True)
        self.assertEqual(critical["overview"]["employees_total"], 187)
        self.assertEqual(critical["overview"]["employees_without_target"], 0)
        self.assertTrue(all(i["critical_gaps"] for i in critical["employees"]))
        empty = build_hr_summary(self.dataset, self.store, department="does not exist")
        self.assertEqual(empty["overview"]["employees_total"], 0)
        self.assertIsNone(empty["overview"]["voluntary_completion_rate"])
        self.assertEqual(empty["skill_gaps"], [])

    def test_candidate_contract_and_schedule(self):
        employee, history, levels, candidates = self.candidate_employee()
        context = recommendation_context(employee, self.dataset, self.store)
        self.assertEqual(context["recommendation_candidates"], candidates)
        self.assertEqual(_model_input(context)["eligible_activities_that_raise_a_target_skill"], candidates)
        for candidate in candidates:
            self.assertGreater(candidate["total_gap_reduction"], 0)
            self.assertEqual(candidate["total_gap_reduction"], sum(g["gap_reduction"] for g in candidate["improved_skills"]))
            self.assertEqual(candidate["requirements_closed_count"], sum(g["remaining_gap"] == 0 for g in candidate["improved_skills"]))
        event = self.dataset.events_by_id[candidates[0]["event_id"]]
        scheduled = event.model_copy(update={"format": "online", "upcoming_sessions": []})
        self.assertEqual(recommendation_candidates(employee, replace(self.dataset, events=[scheduled]), history, levels), [])
        self_paced = scheduled.model_copy(update={"format": "self_paced", "upcoming_sessions": [employee.hire_date]})
        self.assertTrue(recommendation_candidates(employee, replace(self.dataset, events=[self_paced]), history, levels))
        repeatable = self_paced.model_copy(update={"event_id": "EV_036"})
        completion = ActivityHistoryRecord(record_id="REPEAT", employee_id=employee.employee_id,
            event_id="EV_036", date=employee.last_review_date, due_date=None,
            status="completed", completion_pct=100, assigned_by="self")
        self.assertTrue(recommendation_candidates(employee, replace(self.dataset, events=[repeatable]), [completion], levels))
        mandatory = self_paced.model_copy(update={"mandatory": True})
        self.assertEqual(recommendation_candidates(employee, replace(self.dataset, events=[mandatory]), history, levels), [])

    def test_all_blocking_reasons_are_supported(self):
        employee, history, levels, candidates = self.candidate_employee()
        event = self.dataset.events_by_id[candidates[0]["event_id"]]
        gap_skill = candidates[0]["improved_skills"][0]["skill_id"]
        blocked = event.model_copy(update={
            "event_id": "TEST_BLOCKED", "target_roles": [], "target_grades": [],
            "format": "online", "upcoming_sessions": [], "prerequisites": {gap_skill: levels.get(gap_skill, 0) + 1},
            "develops_skills": [d.model_copy(update={"max_level": levels.get(d.skill_id, 0)}) for d in event.develops_skills],
        })
        record = ActivityHistoryRecord(record_id="TEST", employee_id=employee.employee_id,
            event_id=blocked.event_id, date=employee.last_review_date, due_date=None,
            status="completed", completion_pct=100, assigned_by="self")
        codes = {r["code"] for r in recommendation_blockers(employee, replace(self.dataset, events=[blocked]), [record], levels)}
        self.assertTrue({"role_not_eligible", "grade_not_eligible", "prerequisites_not_met", "already_completed",
                         "no_upcoming_session", "skill_cap_prevents_gain"} <= codes)
        codes = {r["code"] for r in recommendation_blockers(employee, replace(self.dataset, events=[]), history, levels)}
        self.assertEqual(codes, {"catalog_has_no_skill_event"})

    def test_employee_with_goal_already_met_is_not_without_step(self):
        employee, _, _, _ = self.candidate_employee()
        target = target_profile_for(employee, self.dataset)[0]
        employee = employee.model_copy(update={"skills": target.required_skills, "last_review_date": self.dataset.as_of_date})
        summary = build_hr_summary(replace(self.dataset, employees=[employee], history=[]), self.store)
        self.assertEqual(summary["overview"]["employees_with_target"], 1)
        self.assertEqual(summary["overview"]["employees_without_recommendation"], 0)


class HRAPITests(HRFixture):
    def setUp(self):
        super().setUp()
        self.env = patch.dict(os.environ, {"DATABASE_PATH": str(self.store.database_path), "HR_PASSWORD": "test-only-password"})
        self.env.start()
        from app import ayko_main as main, auth
        from fastapi.testclient import TestClient
        self.main = main
        self.previous_store, self.previous_dataset = main.activity_store, main.app.state.dataset
        main.activity_store = self.store
        main.app.state.dataset = self.dataset
        auth._sessions.clear()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        self.main.activity_store, self.main.app.state.dataset = self.previous_store, self.previous_dataset
        self.env.stop()
        super().tearDown()

    def login(self):
        response = self.client.post('/api/hr/login', json={"password": "test-only-password"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('HttpOnly', response.headers['set-cookie'])

    def test_hr_access_and_logout(self):
        self.assertEqual(self.client.get('/api/hr/summary', headers={"X-Role": "hr"}).status_code, 401)
        self.assertEqual(self.client.post('/api/hr/login', json={"password": "bad"}).status_code, 401)
        self.login()
        response = self.client.get('/api/hr/summary')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.json()['overview']['employees_total'], 200)
        self.assertEqual(self.client.get('/hr').status_code, 200)
        self.client.post('/api/hr/logout')
        self.assertEqual(self.client.get('/api/hr/summary').status_code, 401)

    def test_completion_recalculates_profile_and_hr(self):
        self.login()
        employee, _, _, candidates = self.candidate_employee()
        candidate = next(c for c in candidates if c['event_id'] != 'EV_036')
        before = self.client.get('/api/hr/summary').json()
        completion = self.client.post(f'/api/employees/{employee.employee_id}/activities/{candidate["event_id"]}/complete')
        self.assertEqual(completion.status_code, 200, completion.text)
        after = self.client.get('/api/hr/summary').json()
        self.assertEqual(after['overview']['voluntary_completed'], before['overview']['voluntary_completed'] + 1)
        self.assertEqual(after['overview']['voluntary_completed_outcomes'], before['overview']['voluntary_completed_outcomes'] + 1)
        profile = self.client.get(f'/api/employees/{employee.employee_id}').json()
        actual_levels = profile['employee']['skills']
        for improvement in candidate['improved_skills']:
            self.assertEqual(actual_levels[improvement['skill_id']], improvement['projected_level'])
        self.assertNotIn(candidate['event_id'], {c['event_id'] for c in profile['next_steps']})
        self.assertEqual(self.client.post(f'/api/employees/{employee.employee_id}/activities/{candidate["event_id"]}/complete').status_code, 409)

    def test_import_updates_hr_without_restart(self):
        self.login()
        employees_doc = json.loads((DATA_PATH / 'employees.json').read_text(encoding='utf-8'))
        employee = next(e for e in self.dataset.employees if e.career_goal)
        new_employee = employee.model_copy(update={"employee_id": "HR_IMPORT_EMPLOYEE", "full_name": "Imported Test", "manager_id": None})
        employees_doc['employees'].append(new_employee.model_dump(mode='json'))
        reader = csv.DictReader(io.StringIO((DATA_PATH / 'activity_history.csv').read_text(encoding='utf-8-sig')))
        rows = list(reader)
        sample = next(row for row in rows if row['employee_id'] == employee.employee_id and not self.dataset.events_by_id[row['event_id']].mandatory)
        rows.append({**sample, 'employee_id': new_employee.employee_id, 'record_id': 'HR_IMPORT_RECORD'})
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        response = self.client.post('/api/data/import', files={
            'employees_file': ('employees.json', json.dumps(employees_doc), 'application/json'),
            'events_file': ('events.json', (DATA_PATH / 'events.json').read_bytes(), 'application/json'),
            'skills_file': ('skills.json', (DATA_PATH / 'skills.json').read_bytes(), 'application/json'),
            'history_file': ('activity_history.csv', output.getvalue(), 'text/csv'),
        })
        self.assertEqual(response.status_code, 200, response.text)
        summary = self.client.get('/api/hr/summary').json()
        self.assertEqual(summary['overview']['employees_total'], 201)
        self.assertIn(new_employee.employee_id, {e['employee_id'] for e in summary['employees']})
        self.assertEqual(sum(i['total_records'] for i in summary['activity_participation']), len(self.dataset.history) + 1)

    def test_model_cannot_select_eligible_but_useless_activity(self):
        for employee in self.dataset.employees:
            history = history_for_employee(self.dataset, self.store, employee.employee_id)
            levels = effective_skill_levels(employee, self.dataset.events_by_id, history)
            allowed = {c['event_id'] for c in recommendation_candidates(employee, self.dataset, history, levels)}
            invalid_ids = {event.event_id for event in eligible_events(employee, self.dataset, history, levels)} - allowed
            if invalid_ids:
                invalid_id = sorted(invalid_ids)[0]
                break
        else:
            self.fail('Expected an eligible event that does not reduce a target gap')
        result = {'recommendations': [{'event_id': invalid_id, 'rank': 1, 'explanations': [
            {'factor': factor, 'detail': 'Test'} for factor in ('grade_gap', 'skill_gap', 'participation_history')]}]}
        with patch.object(self.main.recommendation_engine, 'recommend', return_value=result):
            response = self.client.get(f'/api/employees/{employee.employee_id}/recommendations')
        self.assertEqual(response.status_code, 502)


if __name__ == '__main__':
    unittest.main()
