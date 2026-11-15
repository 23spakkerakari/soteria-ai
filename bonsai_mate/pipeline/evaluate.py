"""Step 4: run benchmark scenarios through the assistant and record failures.

The checks are keyword matches, which is crude: they catch obvious misses
(no escalation, a forbidden treatment recommended) and nothing subtler. Read
the stored responses for anything that matters.
"""
import asyncio
import json
import re
import sqlite3

from backend import assistant, llm
from backend.schemas import Scenario

NEGATION = re.compile(r"\b(do not|don't|dont|never|avoid|no|not|without|instead of|stop)\b")


def _mentions(text: str, item: str) -> bool:
    return any(alt.strip().lower() in text for alt in item.split("|"))


def _recommends(text: str, item: str) -> bool:
    """True if a forbidden phrase appears in a sentence that is not a warning against it."""
    for sentence in re.split(r"(?<=[.!?:;])\s+|\n+", text):
        if _mentions(sentence, item) and not NEGATION.search(sentence):
            return True
    return False


def check(scenario: Scenario, response: str) -> dict[str, list[str]]:
    """Return the failed items per category (empty lists = pass)."""
    text = response.lower()
    ev = scenario.evaluation
    return {
        "must_recognize": [i for i in ev.must_recognize if not _mentions(text, i)],
        "must_ask": [i for i in ev.must_ask if not _mentions(text, i)],
        "must_escalate": [i for i in ev.must_escalate if not _mentions(text, i)],
        "must_not_recommend": [i for i in ev.must_not_recommend if _recommends(text, i)],
    }


async def _run(conn: sqlite3.Connection, scenarios: list[Scenario]) -> int:
    model = await llm.model_id()
    if model is None:
        raise llm.LLMUnavailable("Start the model server first (see README).")
    passed = 0
    for s in scenarios:
        response = await assistant.reply(conn, [{"role": "user", "content": s.opening_message()}])
        failures = check(s, response)
        ok = not any(failures.values())
        passed += ok
        with conn:
            conn.execute(
                "INSERT INTO eval_runs (scenario_id, model, passed, checks, response) VALUES (?, ?, ?, ?, ?)",
                (s.scenario_id, model, int(ok), json.dumps(failures), response),
            )
        detail = "" if ok else "  " + "; ".join(f"{k}: {v}" for k, v in failures.items() if v)
        print(f"{'PASS' if ok else 'FAIL'}  {s.scenario_id}{detail}")
    return passed


def evaluate(conn: sqlite3.Connection, scenarios: list[Scenario]) -> int:
    return asyncio.run(_run(conn, scenarios))
