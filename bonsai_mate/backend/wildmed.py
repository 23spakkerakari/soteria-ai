"""Similar-case lookup over wildmed-kb (the sourced wilderness case studies).

The cases teach the assistant which questions matter and what trained
responders did in a comparable situation. They are educational case studies,
not protocols: every record is clinical_review "pending", and most come from
all-rights-reserved material (see wildmed-kb/README.md). ALLOWED_REUSE decides
which records may be used at all.

The records are indexed into mate.db on start (db.rebuild), weighted toward
the title, keywords and everyday phrasings rather than the clinical write-up.
"""
import json
import re
import sqlite3

from .knowledge import _terms

# Narrow this before shipping anything, e.g. {"redistributable_attribution"}.
ALLOWED_REUSE = {"internal_reference_only", "noncommercial_only", "redistributable_attribution"}
# Word search always finds something. A case weaker than this is not shown to the
# model at all: a loosely related case steers a small model the wrong way.
# (bm25 scores are negative; lower is better. Calibrated on the 56-case index.)
MIN_SCORE = -9.0
RUNNER_UP_RATIO = 0.75

_ELEVATION = re.compile(r"\b\d{1,2},?\d{3}\s*(feet|foot|ft|meters|metres|m)\b", re.I)


def search(conn: sqlite3.Connection, text: str, k: int = 2) -> list[dict]:
    """Best-matching cases for what the user described, as compact summaries."""
    terms = _terms(text)
    if _ELEVATION.search(text):
        terms.append("altitude")  # "we're at 13,000 feet" never says the word
    if not terms:
        return []
    rows = conn.execute(
        f"""
        SELECT c.json, bm25(cases_fts, 0.0, 3.0, 5.0, 4.0, 1.0) AS score
        FROM cases_fts f JOIN cases c ON c.case_id = f.case_id
        WHERE cases_fts MATCH ? AND c.clinical_review != 'rejected'
          AND c.reuse_status IN ({",".join("?" * len(ALLOWED_REUSE))})
        ORDER BY score LIMIT ?
        """,
        (" OR ".join(f'"{t}"' for t in terms), *ALLOWED_REUSE, k),
    ).fetchall()
    return [
        _summary(json.loads(r["json"]))
        for r in rows
        if r["score"] <= MIN_SCORE and r["score"] <= rows[0]["score"] * RUNNER_UP_RATIO
    ]


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
        block("Questions worth asking", case["ask"], 5),
        block("Warning signs", case["red_flags"], 4),
        block("What trained responders did (their shorthand)", case["plan"], 5),
        block("Avoid", case["avoid"], 3),
        f"When to get help: {case['escalate']}" if case["escalate"] else "",
    ]
    return "\n".join(p for p in parts if p)
