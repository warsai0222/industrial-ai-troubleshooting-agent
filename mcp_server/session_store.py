"""SQLite-backed audit log for investigations.

One investigation = one directory under investigations/<investigation_id>/ holding an
audit.db (WAL mode). Tool-call steps and reasoning steps share a single monotonic
step_number sequence so a reasoning entry's evidence_steps can cite either kind
unambiguously by number. See CLAUDE.md for the investigation workflow this supports.
"""
import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from mcp_server.config import INVESTIGATIONS_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS steps (
    step_number INTEGER PRIMARY KEY,
    step_kind TEXT NOT NULL CHECK (step_kind IN ('tool_call', 'reasoning')),
    timestamp TEXT NOT NULL,
    tool_name TEXT,
    tool_params TEXT,
    tool_result_summary TEXT,
    reasoning_type TEXT,
    reasoning_text TEXT,
    confidence REAL,
    evidence_steps TEXT
);
CREATE TABLE IF NOT EXISTS session_meta (
    investigation_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    initial_symptom TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    final_status TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slugify(title: str, max_len: int = 40) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text or "investigation")[:max_len]


def _db_path(investigation_id: str) -> Path:
    return INVESTIGATIONS_DIR / investigation_id / "audit.db"


def investigation_exists(investigation_id: str) -> bool:
    return _db_path(investigation_id).exists()


@contextmanager
def _connect(investigation_id: str):
    db_path = _db_path(investigation_id)
    if not db_path.exists():
        raise ValueError(
            f"Unknown investigation_id {investigation_id!r} -- no audit.db at {db_path}. "
            "Call audit_start_investigation first."
        )
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def start_investigation(title: str, initial_symptom: str) -> str:
    timestamp = datetime.now(timezone.utc)
    investigation_id = f"{timestamp.strftime('%Y%m%d_%H%M%S')}_{_slugify(title)}"
    inv_dir = INVESTIGATIONS_DIR / investigation_id
    inv_dir.mkdir(parents=True, exist_ok=False)

    conn = sqlite3.connect(inv_dir / "audit.db", timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO session_meta (investigation_id, title, initial_symptom, started_at) "
        "VALUES (?, ?, ?, ?)",
        (investigation_id, title, initial_symptom, _now()),
    )
    conn.commit()
    conn.close()
    return investigation_id


def end_investigation(investigation_id: str, final_status: str) -> int:
    with _connect(investigation_id) as conn:
        conn.execute(
            "UPDATE session_meta SET ended_at = ?, final_status = ? WHERE investigation_id = ?",
            (_now(), final_status, investigation_id),
        )
        (total_steps,) = conn.execute("SELECT COUNT(*) FROM steps").fetchone()
    return total_steps


def _next_step_number(conn: sqlite3.Connection) -> int:
    conn.execute("BEGIN IMMEDIATE")
    (current_max,) = conn.execute("SELECT COALESCE(MAX(step_number), 0) FROM steps").fetchone()
    return current_max + 1


def log_tool_step(investigation_id: str, tool_name: str, params: dict, result_summary: dict) -> int:
    """Called internally by every data-retrieval tool when it's given an
    investigation_id -- not a separate MCP tool the assistant calls directly.
    """
    with _connect(investigation_id) as conn:
        step_number = _next_step_number(conn)
        conn.execute(
            "INSERT INTO steps (step_number, step_kind, timestamp, tool_name, tool_params, "
            "tool_result_summary) VALUES (?, 'tool_call', ?, ?, ?, ?)",
            (step_number, _now(), tool_name, json.dumps(params, default=str), json.dumps(result_summary, default=str)),
        )
    return step_number


def log_reasoning(
    investigation_id: str,
    reasoning_type: str,
    text: str,
    evidence_steps: list[int] | None,
    confidence: float | None,
) -> int:
    evidence_steps = evidence_steps or []
    with _connect(investigation_id) as conn:
        existing = {row[0] for row in conn.execute("SELECT step_number FROM steps")}
        bad_refs = [s for s in evidence_steps if s not in existing]
        if bad_refs:
            raise ValueError(
                f"evidence_steps {bad_refs} do not refer to any existing step in "
                f"investigation {investigation_id!r}"
            )
        step_number = _next_step_number(conn)
        conn.execute(
            "INSERT INTO steps (step_number, step_kind, timestamp, reasoning_type, "
            "reasoning_text, confidence, evidence_steps) VALUES (?, 'reasoning', ?, ?, ?, ?, ?)",
            (step_number, _now(), reasoning_type, text, confidence, json.dumps(evidence_steps)),
        )
    return step_number


def get_all_steps(investigation_id: str) -> list[dict]:
    with _connect(investigation_id) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM steps ORDER BY step_number").fetchall()
        return [dict(r) for r in rows]


def get_session_meta(investigation_id: str) -> dict:
    with _connect(investigation_id) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM session_meta WHERE investigation_id = ?", (investigation_id,)
        ).fetchone()
        return dict(row) if row else {}
