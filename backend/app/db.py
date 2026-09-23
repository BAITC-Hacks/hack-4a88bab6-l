from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()
ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "runtime" / "career_quest.sqlite")))
DATASET_SOURCE = os.getenv("DATASET_ZIP", str(ROOT / "career_quest_dataset.zip"))
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads(value: str | None, default=None):
    return json.loads(value) if value is not None else default


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=20000")
    return conn


@contextmanager
def connection():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def row_json(conn: sqlite3.Connection, table: str, key: str, value: str):
    if table not in {"employees", "events", "skills", "role_profiles", "history"}:
        raise ValueError("Unexpected table")
    row = conn.execute(f"SELECT data FROM {table} WHERE {key}=?", (value,)).fetchone()
    return loads(row["data"]) if row else None


def audit(conn: sqlite3.Connection, actor: str, action: str, entity_id: str, reason: str | None = None):
    conn.execute(
        "INSERT INTO audit_log(actor,action,entity_id,reason,created_at) VALUES(?,?,?,?,?)",
        (actor, action, entity_id, reason, utc_now()),
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 160_000)
    return f"pbkdf2_sha256$160000${salt.hex()}${digest.hex()}"


def check_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, digest = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def _demo_password() -> str:
    password = os.getenv("DEMO_PASSWORD", "")
    if not password:
        raise RuntimeError("DEMO_PASSWORD must be set in DEMO_MODE")
    return password


def provision_demo_employees(conn: sqlite3.Connection, employee_ids: list[str]) -> list[str]:
    """Create demo logins for new profiles without changing existing accounts."""
    if not DEMO_MODE or not employee_ids:
        return []
    encoded = hash_password(_demo_password())
    created: list[str] = []
    for employee_id in employee_ids:
        email = f"{employee_id.lower()}@careerquest.test"
        inserted = conn.execute(
            "INSERT OR IGNORE INTO users(email,password_hash,role,employee_id) VALUES(?,?,?,?)",
            (email, encoded, "employee", employee_id),
        )
        if inserted.rowcount:
            conn.execute("INSERT OR IGNORE INTO demo_users(email) VALUES(?)", (email,))
            created.append(email)
    return created


def _adopt_legacy_demo_users(conn: sqlite3.Connection) -> None:
    # Identify generated accounts in databases created before demo_users existed.
    conn.execute(
        """INSERT OR IGNORE INTO demo_users(email)
        SELECT email FROM users
        WHERE (email='hr@careerquest.test' AND role='hr' AND employee_id IS NULL)
           OR (role='employee' AND employee_id IS NOT NULL
               AND email=lower(employee_id)||'@careerquest.test')"""
    )


def _ensure_demo_users(conn: sqlite3.Connection) -> None:
    password = _demo_password()
    with transaction(conn):
        _adopt_legacy_demo_users(conn)
        first = conn.execute(
            "SELECT u.password_hash FROM users u JOIN demo_users d ON d.email=u.email ORDER BY u.email LIMIT 1"
        ).fetchone()
        if first and not check_password(password, first[0]):
            # A changed DEMO_PASSWORD invalidates every existing demo session.
            encoded = hash_password(password)
            conn.execute(
                "UPDATE users SET password_hash=? WHERE email IN (SELECT email FROM demo_users)",
                (encoded,),
            )
            conn.execute("DELETE FROM sessions WHERE email IN (SELECT email FROM demo_users)")
        hr = conn.execute(
            "INSERT OR IGNORE INTO users(email,password_hash,role,employee_id) VALUES(?,?,?,NULL)",
            ("hr@careerquest.test", hash_password(password), "hr"),
        )
        if hr.rowcount:
            conn.execute("INSERT OR IGNORE INTO demo_users(email) VALUES('hr@careerquest.test')")
        provision_demo_employees(conn, [row[0] for row in conn.execute("SELECT employee_id FROM employees")])


def init_db():
    from .imports import seed

    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS employees(employee_id TEXT PRIMARY KEY,data TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS employee_versions(employee_id TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(employee_id,version));
            CREATE TABLE IF NOT EXISTS skills(skill_id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS role_profiles(role TEXT NOT NULL,grade TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(role,grade));
            CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS history(record_id TEXT PRIMARY KEY,employee_id TEXT NOT NULL,event_id TEXT NOT NULL,date TEXT NOT NULL,status TEXT NOT NULL,data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_history_employee ON history(employee_id,date);
            CREATE TABLE IF NOT EXISTS goals(employee_id TEXT PRIMARY KEY,role TEXT NOT NULL,grade TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS app_participations(id TEXT PRIMARY KEY,employee_id TEXT NOT NULL,event_id TEXT NOT NULL,linked_record_id TEXT,session_date TEXT,status TEXT NOT NULL,completion_date TEXT,completed_at TEXT,covered_by_review_version INTEGER,is_simulated INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_participations_employee ON app_participations(employee_id,event_id);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_linked_participation ON app_participations(linked_record_id) WHERE linked_record_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS uq_ordinary_participation ON app_participations(employee_id,event_id) WHERE event_id!='EV_036' AND status IN ('planned','in_progress','completed');
            CREATE UNIQUE INDEX IF NOT EXISTS uq_club_session ON app_participations(employee_id,event_id,session_date) WHERE event_id='EV_036' AND status IN ('planned','in_progress','completed');
            CREATE TABLE IF NOT EXISTS recommendations(employee_id TEXT PRIMARY KEY,version TEXT NOT NULL,source TEXT,status TEXT NOT NULL,calculated_at TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recommendation_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id TEXT NOT NULL,version TEXT NOT NULL,source TEXT,status TEXT NOT NULL,calculated_at TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS skips(employee_id TEXT NOT NULL,event_id TEXT NOT NULL,reason TEXT,active INTEGER NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(employee_id,event_id));
            CREATE TABLE IF NOT EXISTS archives(employee_id TEXT NOT NULL,event_id TEXT NOT NULL,reason TEXT,actor TEXT NOT NULL,active INTEGER NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(employee_id,event_id));
            CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT NOT NULL,action TEXT NOT NULL,entity_id TEXT NOT NULL,reason TEXT,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS users(email TEXT PRIMARY KEY,password_hash TEXT NOT NULL,role TEXT NOT NULL,employee_id TEXT);
            CREATE TABLE IF NOT EXISTS demo_users(email TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,email TEXT NOT NULL,csrf TEXT NOT NULL,expires_at TEXT NOT NULL);
            """
        )
        seed(conn, DATASET_SOURCE)
        if DEMO_MODE:
            _ensure_demo_users(conn)
        else:
            with transaction(conn):
                _adopt_legacy_demo_users(conn)
                conn.execute("DELETE FROM sessions WHERE email IN (SELECT email FROM demo_users)")


def meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None
