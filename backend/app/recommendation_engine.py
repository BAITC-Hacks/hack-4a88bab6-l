from __future__ import annotations

import json
import os

from openai import APIError, OpenAI
from pydantic import ValidationError

from .models import RecommendationsDocument


class RecommendationEngineUnavailable(RuntimeError):
    pass


class RecommendationEngineError(RuntimeError):
    pass


FACTORS = [
    "grade_gap",
    "skill_gap",
    "participation_history",
    "next_grade_requirements",
]

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "rank": {"type": "integer"},
                    "explanations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "factor": {"type": "string", "enum": FACTORS},
                                "detail": {"type": "string"},
                            },
                            "required": ["factor", "detail"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["event_id", "rank", "explanations"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["recommendations"],
    "additionalProperties": False,
}


def _model_input(context: dict) -> dict:
    """Minimize data sent to the model and only offer activities that close a gap."""
    gaps = [gap for gap in context["skill_gaps"] if gap["gap"] > 0]
    candidates = context["recommendation_candidates"]

    employee = context["employee"]
    return {
        "employee": {
            "role": employee["role"],
            "grade": employee["grade"],
            "career_goal": employee.get("career_goal"),
            "preferred_language": employee.get("preferred_language", "ru"),
        },
        "target_role_profile": context.get("target_role_profile"),
        "target_source": context.get("target_source"),
        "as_of_date": context["as_of_date"],
        "skill_gaps": gaps,
        "activity_history": [
            {"event_id": item["event_id"], "date": item["date"], "status": item["status"],
             "completion_pct": item["completion_pct"]}
            for item in context["activity_history"]
        ],
        "eligible_activities_that_raise_a_target_skill": candidates,
    }


def recommend(context: dict) -> dict:
    payload = _model_input(context)
    if not payload["eligible_activities_that_raise_a_target_skill"]:
        return {"recommendations": []}
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RecommendationEngineUnavailable(
            "Не задан OPENAI_API_KEY. Установите ключ OpenAI API в переменную окружения."
        )

    model = os.getenv("OPENAI_MODEL", "gpt-6-astra")
    try:
        client = OpenAI(api_key=api_key, timeout=10.0, max_retries=0)
        response = client.responses.create(
            model=model,
            instructions=(
                "Ты карьерный навигатор Career Quest. Выбери от 1 до 3 лучших активностей "
                "ТОЛЬКО из переданного списка. Не придумывай event_id, навыки, требования "
                "или факты. Ранжируй по тому, насколько активность закрывает важный разрыв "
                "на пути к целевой роли. Верни объяснения на языке preferred_language и "
                "минимум три разных фактора для каждой рекомендации: grade_gap, skill_gap, "
                "participation_history, next_grade_requirements. Каждый detail должен содержать "
                "конкретные значения из входных данных; если фактор истории/грейда не дает "
                "полезного подтверждения, опирайся на другой доступный фактор, не выдумывай. "
                "Если нет подходящих активностей, верни пустой список. Учти participation_history, а именно,"
                "как часто сотрудник завершал или не завершал похожие курсы."
                "Какие могут быть причины этой модели поведения, вдруг нужны рекомендации по обновлению target_role_profile и нужна встреча с manager/hr"

            ),
            input=json.dumps(payload, ensure_ascii=False),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "career_quest_recommendations",
                    "strict": True,
                    "schema": OUTPUT_SCHEMA,
                }
            },
        )
        if not response.output_text:
            raise RecommendationEngineError("OpenAI не вернул текст рекомендаций")
        return RecommendationsDocument.model_validate_json(response.output_text).model_dump()
    except RecommendationEngineError:
        raise
    except APIError as exc:
        # Do not expose provider request details, which may contain sensitive metadata.
        raise RecommendationEngineError("OpenAI API временно недоступен или отклонил запрос") from exc
    except (ValidationError, ValueError, TypeError) as exc:
        raise RecommendationEngineError("OpenAI вернул ответ, который не прошёл проверку") from exc
