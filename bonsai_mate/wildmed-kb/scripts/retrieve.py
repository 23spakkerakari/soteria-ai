#!/usr/bin/env python3
"""
Minimal offline retriever over data/build/scenarios.sqlite.

Hybrid-ready: BM25 (FTS5) + metadata filters today; if you populate chunks.embedding
(float32 bytes) you can add a vector pass and fuse with reciprocal-rank fusion (rrf()).

Usage:
  python3 scripts/retrieve.py "my friend is shivering and clumsy in the rain"
  python3 scripts/retrieve.py "can't breathe chest pain" --setting wilderness --acuity life_threat
  python3 scripts/retrieve.py "bear spray eyes" --types presentation,assessment_plan -k 3
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "build" / "scenarios.sqlite"
STOP = set("a an and are at be but by do for from has have he her his i in is it its me my of on or our she so that the "
           "their them they this to was we what when where who will with you your just really now".split())


def to_fts_query(text: str) -> str:
    """Turn free text into an OR query of prefix terms (robust to panicked phrasing)."""
    toks = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP and len(t) > 1]
    return " OR ".join(f'"{t}"*' for t in toks) or '""'


def search(q: str, k: int = 5, setting: str | None = None, acuity: str | None = None,
           types: list[str] | None = None, age_group: str | None = None) -> list[dict]:
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    where, params = ["chunks_fts MATCH ?"], [to_fts_query(q)]
    if setting:
        where.append("c.setting = ?"); params.append(setting)
    if acuity:
        where.append("c.acuity = ?"); params.append(acuity)
    if age_group:
        where.append("c.age_group = ?"); params.append(age_group)
    if types:
        where.append(f"c.chunk_type IN ({','.join('?' * len(types))})"); params += types
    sql = f"""
      SELECT c.chunk_id, c.scenario_id, c.chunk_type, c.acuity, c.primary_condition, s.title, s.source_url,
             bm25(chunks_fts) AS score, snippet(chunks_fts, 0, '[', ']', '...', 18) AS snip
      FROM chunks_fts JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
      JOIN scenarios s ON s.id = c.scenario_id
      WHERE {' AND '.join(where)}
      ORDER BY score LIMIT ?"""
    rows = [dict(r) for r in db.execute(sql, params + [k * 4])]
    # collapse to best chunk per scenario
    seen, out = set(), []
    for r in rows:
        if r["scenario_id"] in seen:
            continue
        seen.add(r["scenario_id"])
        out.append(r)
        if len(out) == k:
            break
    return out


def rrf(*ranked_lists: list[str], k: int = 60) -> list[str]:
    """Reciprocal-rank fusion for combining BM25 and vector result id lists."""
    scores: dict[str, float] = {}
    for lst in ranked_lists:
        for rank, cid in enumerate(lst):
            scores[cid] = scores.get(cid, 0) + 1 / (k + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--setting")
    ap.add_argument("--acuity")
    ap.add_argument("--age-group")
    ap.add_argument("--types", help="comma list of chunk types")
    a = ap.parse_args()
    for r in search(a.query, a.k, a.setting, a.acuity, a.types.split(",") if a.types else None, a.age_group):
        print(f"{r['score']:7.2f}  {r['acuity']:<16} {r['primary_condition']:<30} {r['title']}")
        print(f"         [{r['chunk_type']}] {r['snip']}")
