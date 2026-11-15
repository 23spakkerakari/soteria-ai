"""Protocol card lookup. Plain SQLite full-text search: no embeddings, no
network, and it keeps working when the language model is not running."""
import json
import re
import sqlite3

STOPWORDS = set(
    "a an and are as at be been but by can cant could did do does dont for from get got had "
    "has have he her him his how i if im in is isnt it its just me my not of on or our she so "
    "some that the their them there they this to up was we were what when who will with wont "
    "would you your really very much think feel feels feeling keep keeps help need now".split()
)


def _terms(text: str) -> list[str]:
    words = re.findall(r"[a-z]+", text.lower().replace("'", ""))
    return list(dict.fromkeys(w for w in words if len(w) > 2 and w not in STOPWORDS))


def search(conn: sqlite3.Connection, text: str, k: int = 3) -> list[dict]:
    terms = _terms(text)
    if not terms:
        return []
    query = " OR ".join(f'"{t}"' for t in terms)
    rows = conn.execute(
        """
        SELECT c.json FROM cards_fts f JOIN cards c ON c.card_id = f.card_id
        WHERE cards_fts MATCH ? AND c.clinical_review != 'rejected'
        ORDER BY bm25(cards_fts, 0.0, 5.0, 3.0, 1.0) LIMIT ?
        """,
        (query, k),
    )
    return [json.loads(r["json"]) for r in rows]


def format_card(card: dict) -> str:
    def block(label: str, items: list[str]) -> str:
        return f"{label}:\n" + "\n".join(f"- {i}" for i in items) if items else ""

    parts = [
        f"### {card['title']}",
        block("Signs", card["recognize"]),
        block("Do first", card["do_first"]),
        block("Do not", card["do_not"]),
        block("Get help urgently if", card["escalate_when"]),
    ]
    return "\n".join(p for p in parts if p)
