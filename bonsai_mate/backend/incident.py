"""The record of one incident: every question the app asked and what the user
answered, kept in SQLite. The record is fed back to the model on every turn and
shown in the "What I know so far" panel.

Status of a question:
  covered   the user had already told us, in their own words, before we asked
  asked     asked in the latest reply, waiting for the answer
  answered  the user's next message, stored as the answer
  skipped   the user asked for the plan instead of answering
"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    first_report TEXT NOT NULL,
    turns INTEGER NOT NULL DEFAULT 0,
    plan_given INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS incident_qa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
    turn INTEGER NOT NULL,
    question_id TEXT NOT NULL,
    grp TEXT NOT NULL,
    label TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    answer TEXT,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (incident_id, question_id)
);
"""


def start_turn(conn: sqlite3.Connection, incident_id: str, first_report: str) -> dict:
    """Create the incident if needed, count the turn, and return its state."""
    with conn:
        conn.execute("INSERT OR IGNORE INTO incidents (incident_id, first_report) VALUES (?, ?)", (incident_id, first_report))
        conn.execute("UPDATE incidents SET turns = turns + 1 WHERE incident_id = ?", (incident_id,))
    return dict(conn.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone())


def close_pending(conn: sqlite3.Connection, incident_id: str, answer: str, skipped: bool = False) -> None:
    """The user's new message answers the questions asked in the previous reply."""
    with conn:
        conn.execute(
            "UPDATE incident_qa SET status = ?, answer = ? WHERE incident_id = ? AND status = 'asked'",
            ("skipped" if skipped else "answered", None if skipped else answer, incident_id),
        )


def add(conn: sqlite3.Connection, incident_id: str, turn: int, question, text: str, status: str, answer: str | None = None) -> None:
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO incident_qa (incident_id, turn, question_id, grp, label, question, status, answer) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (incident_id, turn, question.id, question.group, question.label, text, status, answer),
        )


def mark_plan_given(conn: sqlite3.Connection, incident_id: str) -> None:
    with conn:
        conn.execute("UPDATE incidents SET plan_given = 1 WHERE incident_id = ?", (incident_id,))


def rows(conn: sqlite3.Connection, incident_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM incident_qa WHERE incident_id = ? ORDER BY id", (incident_id,))]


def record(conn: sqlite3.Connection, incident_id: str) -> dict | None:
    """Everything known about an incident, grouped the way the panel shows it."""
    incident = conn.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
    if incident is None:
        return None
    qa = rows(conn, incident_id)
    rounds: dict[int, dict] = {}
    for r in qa:
        if r["status"] == "covered":
            continue
        this = rounds.setdefault(r["turn"], {"turn": r["turn"], "questions": [], "status": r["status"], "answer": r["answer"]})
        this["questions"].append(r["question"])
    return {
        "incident_id": incident_id,
        "started_at": incident["started_at"],
        "reported": incident["first_report"],
        "already_told": [r["label"] for r in qa if r["status"] == "covered"],
        "rounds": list(rounds.values()),
    }


def for_model(known: dict) -> str:
    """The record as the model reads it."""
    lines = []
    if known["already_told"]:
        lines.append("Already clear from what the user said: " + "; ".join(known["already_told"]) + ".")
    for r in known["rounds"]:
        asked = " | ".join(r["questions"])
        if r["status"] == "answered":
            lines.append(f'The app asked: {asked}\nThe user answered: "{r["answer"]}"')
        elif r["status"] == "skipped":
            lines.append(f"The app asked: {asked}\nThe user did not answer (they asked for the plan).")
    return "\n".join(lines) or "Nothing beyond the user's messages yet."
