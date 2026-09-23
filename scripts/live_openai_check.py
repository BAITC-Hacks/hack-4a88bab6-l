"""Explicit paid smoke check. Run manually; never included in pytest.

Reads OPENAI_API_KEY only from the server environment/.env. It prints safe
metadata, never the key, full prompt, profile name, or provider error body.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.db import connection, init_db  # noqa: E402
from backend.app.domain import candidate_info, history_for  # noqa: E402
from backend.app.recommendations import _call_openai, _validate_selection, evidence_pool  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicit paid OpenAI validation (no recommendation state changes)")
    parser.add_argument("--employee", default="E0005")
    args = parser.parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not configured in server environment")
        return 1
    init_db()
    with connection() as conn:
        info = candidate_info(conn, args.employee)
        if not info or not info["candidates"]:
            print("No eligible candidates for this profile; no paid call made")
            return 1
        history = history_for(conn, args.employee, info["events"])
        pool = evidence_pool(info, history)
        model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        started = time.perf_counter()
        try:
            selection = _call_openai(info, history, pool, model)
            choices = _validate_selection(selection, info, pool)
        except Exception as exc:
            safe_reasons = {"Invalid recommendation count", "Unknown or duplicate event_id", "Invalid recommendation action", "Too little evidence", "Unknown or unrelated evidence_id", "Evidence does not cover three factors", "OpenAI returned incomplete, refused, or unparsed response"}
            print({"model": model, "result": "failed", "error_type": type(exc).__name__, "reason": str(exc) if str(exc) in safe_reasons else "provider_or_schema_error", "http_status": getattr(exc, "status_code", None), "seconds": round(time.perf_counter() - started, 2)})
            return 1
        print({"model": model, "result": "validated_ai", "seconds": round(time.perf_counter() - started, 2), "event_ids": [candidate["event_id"] for candidate, _ in choices], "factors_validated": True})
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
