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


def search(
    conn: sqlite3.Connection, text: str, k: int = 3, runner_up_ratio: float = 0.0, min_score: float = 0.0
) -> list[dict]:
    """Best-matching cards. The assistant passes two limits that the Protocols tab
    does not: min_score drops weak matches entirely (word search always finds
    something), and runner_up_ratio keeps later cards only if they score at least
    that fraction of the best one. bm25 scores are negative; lower is better."""
    terms = _terms(text)
    if not terms:
        return []
    query = " OR ".join(f'"{t}"' for t in terms)
    rows = conn.execute(
        """
        SELECT c.json, bm25(cards_fts, 0.0, 5.0, 3.0, 1.0) AS score
        FROM cards_fts f JOIN cards c ON c.card_id = f.card_id
        WHERE cards_fts MATCH ? AND c.clinical_review != 'rejected'
        ORDER BY score LIMIT ?
        """,
        (query, k),
    ).fetchall()
    return [
        json.loads(r["json"])
        for r in rows
        if r["score"] <= min_score and r["score"] <= rows[0]["score"] * runner_up_ratio
    ]


def get(conn: sqlite3.Connection, card_id: str) -> dict | None:
    row = conn.execute("SELECT json FROM cards WHERE card_id = ?", (card_id,)).fetchone()
    return json.loads(row["json"]) if row else None


_CLAUSE = re.compile(r"[.,;!?]+|\b(?:but|and)\b", re.I)
_NEGATION = re.compile(
    r"\b(no|not|none|never|without|isn'?t|aren'?t|wasn'?t|doesn'?t|don'?t|didn'?t|hasn'?t|haven'?t)\b", re.I
)


_NEGATION_WINDOW = 5  # words; a negation further back than this does not reach the match (as in NegEx)


def said(pattern: str, text: str) -> bool:
    """Did the user say this, rather than deny it? Checked clause by clause: "not a
    grizzly" and "it doesn't have a hump" do not count as saying "grizzly" or "hump",
    while "no hump" does count for a pattern that itself starts with "no". A negation
    reaches five words, as in NegEx: far enough for "I don't think it has a hump", and
    the price is that unpunctuated speech such as "we were not in a tent it followed
    us" does not count as "followed". That error is in the safe direction: no route."""
    for clause in _CLAUSE.split(text):
        m = re.search(pattern, clause, re.I)
        if m and not _NEGATION.search(" ".join(clause[: m.start()].split()[-_NEGATION_WINDOW:])):
            return True
    return False


def resolve(conn: sqlite3.Connection, card: dict, text: str, _seen: set[str] | None = None) -> dict:
    """The card in this card's family that fits what the user has said.

    Word search picks a card by shared words, and the bear cards share nearly all
    of theirs: "a bear is attacking" scores the same for a grizzly, a black bear
    and a bear in a tent. Two rules in the card data settle it, and they are
    deterministic, like the red flags:

      applies_when.requires   the card needs a fact ("grizz|hump"); if the user has
                              not said it, use applies_when.fallback instead
      routes                  the user has said enough ("tent") for a more specific card

    `text` is everything the user has said; negated clauses do not count (see said()).
    Followed until a card stays put. A cycle stops at the first repeat."""
    seen = _seen if _seen is not None else {card["card_id"]}
    applies = card.get("applies_when") or {}
    target = None
    if applies.get("requires") and applies.get("fallback") and not said(applies["requires"], text):
        target = applies["fallback"]
    else:
        target = next((r["card"] for r in card.get("routes", []) if said(r["when"], text)), None)
    if target is None or target in seen:
        return card
    nxt = get(conn, target)
    if nxt is None:
        return card
    seen.add(target)
    return resolve(conn, nxt, text, seen)


def format_card(card: dict) -> str:
    def block(label: str, items: list[str]) -> str:
        return f"{label}:\n" + "\n".join(f"- {i}" for i in items) if items else ""

    applies = card.get("applies_when") or {}
    scope = ", ".join(applies.get("species") or [])
    if applies.get("situation"):
        scope = f"{scope}; {applies['situation'].replace('_', ' ')}" if scope else applies["situation"].replace("_", " ")
    parts = [
        f"### {card['title']}",
        f"Applies to: {scope}" if scope else "",
        block("Signs", card["recognize"]),
        block("Do first", card["do_first"]),
        block("Do not", card["do_not"]),
        block("Get help urgently if", card["escalate_when"]),
    ]
    return "\n".join(p for p in parts if p)
