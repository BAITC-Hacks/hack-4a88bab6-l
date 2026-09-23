"""Read the supplied dataset without extracting untrusted archive paths."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any


DATA_FILES = ("skills.json", "employees.json", "events.json", "activity_history.csv")
HISTORY_COLUMNS = (
    "record_id", "employee_id", "event_id", "date", "due_date", "status",
    "completion_pct", "score", "feedback_rating", "assigned_by",
)
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_INNER_ZIP_BYTES = 32 * 1024 * 1024
DATASET_PREFIXES = ("case_1/career_quest_dataset/", "career_quest_dataset/", "")


class DatasetError(ValueError):
    """The supplied source is missing data or violates its published format."""


def _read_limited(stream: Any, limit: int, label: str) -> bytes:
    content = stream.read(limit + 1)
    if len(content) > limit:
        raise DatasetError(f"{label}: file exceeds the {limit // (1024 * 1024)} MB limit")
    return content


def _read_zip(archive: zipfile.ZipFile) -> dict[str, bytes]:
    # Open only four fixed names. Never extract entries or interpret archive paths.
    names = archive.namelist()
    for prefix in DATASET_PREFIXES:
        wanted = {filename: prefix + filename for filename in DATA_FILES}
        if not all(names.count(member) == 1 for member in wanted.values()):
            continue
        result: dict[str, bytes] = {}
        for filename, member in wanted.items():
            info = archive.getinfo(member)
            if info.is_dir() or info.file_size > MAX_FILE_BYTES:
                raise DatasetError(f"{member}: invalid or oversized dataset member")
            with archive.open(info) as stream:
                result[filename] = _read_limited(stream, MAX_FILE_BYTES, member)
        return result

    # The Codex pack has the requested dataset ZIP as a fixed nested member.
    if names.count("career_quest_dataset.zip") == 1:
        info = archive.getinfo("career_quest_dataset.zip")
        if info.file_size > MAX_INNER_ZIP_BYTES:
            raise DatasetError("Nested dataset ZIP is too large")
        with archive.open(info) as stream:
            nested_bytes = _read_limited(stream, MAX_INNER_ZIP_BYTES, info.filename)
        with zipfile.ZipFile(io.BytesIO(nested_bytes)) as nested:
            return _read_zip(nested)
    raise DatasetError("Dataset archive must contain the four published data files")


def read_dataset_files(source: str | Path) -> dict[str, bytes]:
    """Return only the four expected file contents from a directory or ZIP."""
    path = Path(source)
    if path.is_dir():
        for prefix in (path, path / "career_quest_dataset", path / "case_1" / "career_quest_dataset"):
            if all((prefix / name).is_file() for name in DATA_FILES):
                result: dict[str, bytes] = {}
                for name in DATA_FILES:
                    target = prefix / name
                    if target.stat().st_size > MAX_FILE_BYTES:
                        raise DatasetError(f"{target}: dataset file is too large")
                    result[name] = target.read_bytes()
                return result
        raise DatasetError(f"{path}: four dataset files not found")
    if not path.is_file():
        raise DatasetError(f"Dataset source does not exist: {path}")
    if path.stat().st_size > MAX_INNER_ZIP_BYTES:
        raise DatasetError("Dataset ZIP is too large")
    try:
        with zipfile.ZipFile(path) as archive:
            return _read_zip(archive)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise DatasetError("Dataset source is not a readable ZIP") from exc


def parse_json_wrapper(content: bytes, label: str, array_key: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DatasetError(f"{label}: invalid UTF-8 JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("meta"), dict):
        raise DatasetError(f"{label}: expected an object with meta and {array_key}")
    if not isinstance(parsed.get(array_key), list):
        raise DatasetError(f"{label}.{array_key}: expected an array")
    return parsed


def parse_history_csv(content: bytes) -> list[dict[str, Any]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeError as exc:
        raise DatasetError("activity_history.csv: invalid UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != HISTORY_COLUMNS:
        raise DatasetError("activity_history.csv: unexpected columns")
    rows: list[dict[str, Any]] = []
    for line_number, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise DatasetError(f"activity_history.csv row {line_number}: wrong column count")
        normalized: dict[str, Any] = {key: value for key, value in row.items()}
        for key in ("due_date", "score", "feedback_rating"):
            normalized[key] = normalized[key] or None
        for key in ("completion_pct", "score", "feedback_rating"):
            if normalized[key] is None:
                continue
            try:
                normalized[key] = int(normalized[key])
            except ValueError as exc:
                raise DatasetError(f"activity_history.csv row {line_number}.{key}: expected integer") from exc
        rows.append(normalized)
    return rows


def load_dataset(source: str | Path) -> dict[str, Any]:
    files = read_dataset_files(source)
    skills = parse_json_wrapper(files["skills.json"], "skills.json", "skills")
    employees = parse_json_wrapper(files["employees.json"], "employees.json", "employees")
    events = parse_json_wrapper(files["events.json"], "events.json", "events")
    if not isinstance(skills.get("role_profiles"), list):
        raise DatasetError("skills.json.role_profiles: expected an array")
    if not isinstance(skills.get("proficiency_scale"), dict):
        raise DatasetError("skills.json.proficiency_scale: expected an object")
    dates = {str(part["meta"].get("as_of_date", "")) for part in (skills, employees, events)}
    if len(dates) != 1 or not next(iter(dates)):
        raise DatasetError("Dataset JSON files disagree on meta.as_of_date")
    return {
        "meta": employees["meta"],
        "proficiency_scale": skills["proficiency_scale"],
        "skills": skills["skills"],
        "role_profiles": skills["role_profiles"],
        "events": events["events"],
        "employees": employees["employees"],
        "history": parse_history_csv(files["activity_history.csv"]),
    }
