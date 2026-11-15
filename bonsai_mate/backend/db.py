"""SQLite storage. The JSON files under data/ are the source of truth; the
card and scenario tables are rebuilt from them. Evaluation runs are kept."""
import json
import sqlite3
from pathlib import Path

from . import config
from .schemas import Card, Scenario

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    card_id TEXT PRIMARY KEY,
    clinical_review TEXT NOT NULL,
    json TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
    card_id UNINDEXED, title, keywords, body, tokenize = 'porter'
);
CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id TEXT PRIMARY KEY,
    variation_of TEXT,
    organization TEXT NOT NULL,
    source_type TEXT NOT NULL,
    license_verified INTEGER NOT NULL,
    clinical_review TEXT NOT NULL,
    json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL DEFAULT (datetime('now')),
    scenario_id TEXT NOT NULL,
    model TEXT,
    passed INTEGER NOT NULL,
    checks TEXT NOT NULL,
    response TEXT NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _read_records(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


def load_cards() -> list[Card]:
    return [
        Card.model_validate(rec)
        for path in sorted(config.CARDS_DIR.glob("*.json"))
        for rec in _read_records(path)
    ]


def load_scenarios() -> list[Scenario]:
    scenarios = [
        Scenario.model_validate(rec)
        for folder in config.SCENARIO_FOLDERS
        for path in sorted((config.SCENARIOS_DIR / folder).glob("*.json"))
        for rec in _read_records(path)
    ]
    ids = [s.scenario_id for s in scenarios]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"Duplicate scenario ids: {sorted(dupes)}")
    return scenarios


def rebuild(conn: sqlite3.Connection) -> tuple[int, int]:
    """Validate every JSON record and reload the card and scenario tables."""
    cards, scenarios = load_cards(), load_scenarios()
    with conn:
        conn.execute("DELETE FROM cards")
        conn.execute("DELETE FROM cards_fts")
        conn.execute("DELETE FROM scenarios")
        for c in cards:
            conn.execute(
                "INSERT INTO cards VALUES (?, ?, ?)",
                (c.card_id, c.clinical_review, c.model_dump_json()),
            )
            conn.execute(
                "INSERT INTO cards_fts VALUES (?, ?, ?, ?)",
                (c.card_id, c.title, " ".join(c.keywords), " ".join(c.recognize)),
            )
        for s in scenarios:
            conn.execute(
                "INSERT INTO scenarios VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    s.scenario_id,
                    s.variation_of,
                    s.source.organization,
                    s.source.source_type,
                    int(s.source.license_verified),
                    s.evaluation.clinical_review,
                    s.model_dump_json(),
                ),
            )
    return len(cards), len(scenarios)


def all_cards(conn: sqlite3.Connection) -> list[dict]:
    return [json.loads(r["json"]) for r in conn.execute("SELECT json FROM cards ORDER BY card_id")]


def all_scenarios(conn: sqlite3.Connection) -> list[Scenario]:
    rows = conn.execute("SELECT json FROM scenarios ORDER BY scenario_id")
    return [Scenario.model_validate_json(r["json"]) for r in rows]
