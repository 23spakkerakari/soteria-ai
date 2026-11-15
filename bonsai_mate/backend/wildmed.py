"""Similar-case lookup over wildmed-kb (the sourced wilderness case studies).

The cases teach the assistant which questions matter and what trained
responders did in a comparable situation. They are educational case studies,
not protocols: every record is clinical_review "pending", and most come from
all-rights-reserved material (see wildmed-kb/README.md). ALLOWED_REUSE decides
which records may be used at all.
"""
import json
import sqlite3

from . import config
from .knowledge import _terms

# Narrow this before shipping anything, e.g. {"redistributable_attribution"}.
ALLOWED_REUSE = {"internal_reference_only", "noncommercial_only", "redistributable_attribution"}
RUNNER_UP_RATIO = 0.75


def available() -> bool:
    return config.WILDMED_DB.exists()


def search(text: str, k: int = 2) -> list[dict]:
    """Best-matching cases for what the user described, as compact summaries."""
    terms = _terms(text)
    if not terms or not available():
        return []
    conn = sqlite3.connect(f"file:{config.WILDMED_DB.as_posix()}?mode=ro", uri=True)
    try:
        # Match on the presentation chunk: symptoms, findings and lay phrasings.
        rows = conn.execute(
            f"""
            SELECT s.record_json, bm25(chunks_fts) FROM chunks_fts f JOIN scenarios s ON s.id = f.scenario_id
            WHERE chunks_fts MATCH ? AND f.chunk_type = 'presentation'
              AND s.clinical_review != 'rejected'
              AND s.reuse_status IN ({",".join("?" * len(ALLOWED_REUSE))})
            ORDER BY bm25(chunks_fts) LIMIT ?
            """,
            (" OR ".join(f'"{t}"' for t in terms), *ALLOWED_REUSE, k),
        ).fetchall()
    finally:
        conn.close()
    # Word search always returns something. Keep a runner-up only if it matches
    # nearly as well as the best case (bm25 scores are negative; lower is better),
    # so a loosely related case does not steer a small model.
    return [_summary(json.loads(record)) for record, score in rows if score <= rows[0][1] * RUNNER_UP_RATIO]


def _summary(rec: dict) -> dict:
    management, ev = rec["source_management"], rec["eval"]
    return {
        "id": rec["id"],
        "title": rec["title"],
        "condition": rec["classification"]["primary_condition"].replace("_", " "),
        "acuity": rec["classification"]["acuity"].replace("_", " "),
        "ask": ev["must_ask"],
        "red_flags": rec["retrieval"]["red_flags"],
        "plan": management["plan"],
        "teaching_points": management["teaching_points"],
        "avoid": ev["must_not_recommend"],
        "escalate": ev["must_escalate"],
        "source": rec["source"]["organization"],
        "source_url": rec["source"]["url"],
        "clinical_review": ev["clinical_review"],
    }


def format_case(case: dict) -> str:
    def block(label: str, items: list[str], limit: int) -> str:
        return f"{label}:\n" + "\n".join(f"- {i}" for i in items[:limit]) if items else ""

    parts = [
        f"### Case: {case['title']} (possible {case['condition']}; {case['acuity']})",
        block("Questions worth asking", case["ask"], 6),
        block("Warning signs", case["red_flags"], 5),
        block("What trained responders did", case["plan"], 7),
        block("Key points", case["teaching_points"], 3),
        block("Avoid", case["avoid"], 4),
        f"When to get help: {case['escalate']}" if case["escalate"] else "",
    ]
    return "\n".join(p for p in parts if p)
