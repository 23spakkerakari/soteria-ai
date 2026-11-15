#!/usr/bin/env python3
"""
Validate raw scenario records and build retrieval artifacts.

Inputs
  data/source_registry.json      per-source org/license defaults
  data/raw_records/*.json        lists of raw records ({"source": {"ref": ...}} shorthand)

Outputs (data/build/)
  scenarios.jsonl                one fully-expanded, validated ScenarioRecord per line
  chunks.jsonl                   embedding-ready chunks (one record -> several typed chunks)
  scenarios.sqlite               SQLite: scenarios, conditions, vitals, chunks, chunks_fts (FTS5)
  scenario.schema.json           JSON Schema export
  report.json                    counts + validation errors

Run:  python3 scripts/build.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from schema.scenario import ScenarioRecord, SCHEMA_VERSION  # noqa: E402

RAW = ROOT / "data" / "raw_records"
OUT = ROOT / "data" / "build"
REGISTRY = json.loads((ROOT / "data" / "source_registry.json").read_text())
EXTRACTED_ON = "2026-11-14"


def expand(raw: dict) -> dict:
    r = json.loads(json.dumps(raw))
    src = r["source"]
    ref = src.pop("ref")
    reg = REGISTRY[ref]
    r["source"] = {
        "organization": reg["organization"],
        "source_type": src.pop("source_type", reg["source_type"]),
        "license": src.pop("license", reg["license"]),
        "reuse_status": src.pop("reuse_status", reg["reuse_status"]),
        "retrieved": EXTRACTED_ON,
        **src,
    }
    r.setdefault("provenance", {"extracted_on": EXTRACTED_ON})
    r["provenance"].setdefault("extracted_on", EXTRACTED_ON)
    if r["source"]["reuse_status"] == "internal_reference_only":
        r["provenance"].setdefault(
            "notes", "Paraphrased facts only. Source is all-rights-reserved; get permission before commercial redistribution.")
    return r


# --------------------------------------------------------------- chunking
def _vitals_line(v: dict) -> str:
    parts = []
    for k, label in [("time", "t"), ("loc", "LOR"), ("hr", "HR"), ("rr", "RR"), ("skin", "skin"),
                     ("pupils", "pupils"), ("temp_c", "T(C)"), ("spo2", "SpO2"), ("pain_0_10", "pain")]:
        if v.get(k) is not None:
            parts.append(f"{label} {v[k]}")
    if v.get("bp_systolic"):
        parts.append(f"BP {v['bp_systolic']}/{v.get('bp_diastolic')}")
    elif v.get("bp_note"):
        parts.append(f"BP {v['bp_note']}")
    if v.get("note"):
        parts.append(f"({v['note']})")
    return ", ".join(parts)


def make_chunks(rec: dict) -> list[dict]:
    """Typed chunks so the retriever can pull just what a stage of the conversation needs."""
    c, ctx, p, pr, sm = rec["classification"], rec["context"], rec["patient"], rec["presentation"], rec["source_management"]
    head = f"{rec['title']}. Setting: {ctx['setting']}" + (f", {ctx['activity']}" if ctx.get("activity") else "") + "."
    meta = {
        "scenario_id": rec["id"], "setting": ctx["setting"], "category": c["category"],
        "primary_condition": c["primary_condition"], "conditions": c["conditions"], "acuity": c["acuity"],
        "age_group": p["age_group"], "reuse_status": rec["source"]["reuse_status"],
        "clinical_review": rec["eval"]["clinical_review"], "source_url": rec["source"]["url"],
    }
    who = f"{p.get('age_years') or ''} {p['age_group']} {p['sex'] if p['sex'] != 'unspecified' else ''}".split()
    chunks = [
        ("presentation",
         f"{head} Patient: {' '.join(who)}. {pr['summary']} Chief complaint: {pr['chief_complaint']}. "
         f"Symptoms: {'; '.join(pr['symptoms']) or 'none reported'}. Findings: {'; '.join(pr['exam_findings'])}. "
         f"Pertinent negatives: {'; '.join(pr['pertinent_negatives']) or 'none'}. "
         f"Lay descriptions: {' | '.join(rec['retrieval']['lay_queries'])}"),
        ("context",
         f"{head} Region: {ctx.get('region') or 'n/a'}. Terrain: {ctx.get('terrain') or 'n/a'}. Weather: {ctx.get('weather') or 'n/a'}. "
         f"Distance to care: {ctx.get('distance_to_care') or 'n/a'}. Connectivity: {ctx['connectivity']}. "
         f"Responder: {ctx.get('responder_training') or 'n/a'}. Resources: {', '.join(ctx.get('resources_available') or []) or 'n/a'}. "
         f"Mechanism: {c.get('mechanism') or 'n/a'}."),
        ("assessment_plan",
         f"{head} [SOURCE ANSWER KEY, UNVALIDATED] Assessment: {'; '.join(sm['assessment'])}. Plan: {'; '.join(sm['plan'])}. "
         f"Anticipated problems: {'; '.join(sm['anticipated_problems']) or 'n/a'}. "
         f"Red flags: {'; '.join(rec['retrieval']['red_flags']) or 'n/a'}. Acuity: {c['acuity']} ({c['acuity_basis']})"),
    ]
    if sm.get("teaching_points"):
        chunks.append(("teaching", f"{head} [SOURCE TEACHING, UNVALIDATED] " + " ".join(sm["teaching_points"])))
    if sm.get("decision_points"):
        dp = " ".join(f"Q: {d['question']} Source decision: {d['source_decision']} because {', '.join(d['basis'])}."
                      for d in sm["decision_points"])
        chunks.append(("decision_points", f"{head} [SOURCE DECISIONS, UNVALIDATED] {dp}"))
    if rec["vitals"]:
        chunks.append(("vitals_trend", f"{head} Vital sign trend: " + " -> ".join(_vitals_line(v) for v in rec["vitals"])))
    o = rec["outcome"]
    if o.get("summary"):
        chunks.append(("outcome", f"{head} Outcome: {o['summary']} Evacuation: {o['evacuation_mode']}. "
                                  f"Final diagnosis: {o.get('final_diagnosis') or 'n/a'}."))
    return [{"chunk_id": f"{rec['id']}#{kind}", "chunk_type": kind, "text": text, **meta} for kind, text in chunks]


# ---------------------------------------------------------------- sqlite
DDL = """
PRAGMA journal_mode=WAL;
CREATE TABLE scenarios(
  id TEXT PRIMARY KEY, title TEXT, category TEXT, primary_condition TEXT, acuity TEXT,
  setting TEXT, activity TEXT, age_group TEXT, age_years REAL, sex TEXT,
  source_org TEXT, source_url TEXT, license TEXT, reuse_status TEXT,
  clinical_review TEXT, schema_version TEXT, record_json TEXT NOT NULL);
CREATE TABLE scenario_conditions(scenario_id TEXT REFERENCES scenarios(id), condition TEXT, is_primary INTEGER);
CREATE INDEX ix_cond ON scenario_conditions(condition);
CREATE TABLE vitals(scenario_id TEXT REFERENCES scenarios(id), seq INTEGER, time TEXT, loc TEXT, hr INTEGER,
  rr INTEGER, bp_systolic INTEGER, bp_diastolic INTEGER, temp_c REAL, spo2 INTEGER, skin TEXT, note TEXT);
CREATE TABLE red_flags(scenario_id TEXT REFERENCES scenarios(id), flag TEXT);
CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY, scenario_id TEXT REFERENCES scenarios(id), chunk_type TEXT,
  text TEXT, setting TEXT, primary_condition TEXT, acuity TEXT, age_group TEXT, embedding BLOB);
CREATE VIRTUAL TABLE chunks_fts USING fts5(text, chunk_id UNINDEXED, scenario_id UNINDEXED, chunk_type UNINDEXED,
  tokenize='porter unicode61');
CREATE TABLE sources(ref TEXT PRIMARY KEY, organization TEXT, license TEXT, reuse_status TEXT,
  index_url TEXT, ingested INTEGER, notes TEXT);
"""


def write_sqlite(path: Path, records: list[dict], chunks: list[dict]):
    if path.exists():
        path.unlink()
    db = sqlite3.connect(path)
    db.executescript(DDL)
    for ref, s in REGISTRY.items():
        db.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?)",
                   (ref, s["organization"], s["license"], s["reuse_status"], s["index_url"], int(s["ingested"]), s["notes"]))
    for r in records:
        c, ctx, p, s = r["classification"], r["context"], r["patient"], r["source"]
        db.execute("INSERT INTO scenarios VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (r["id"], r["title"], c["category"], c["primary_condition"], c["acuity"], ctx["setting"],
                    ctx.get("activity"), p["age_group"], p.get("age_years"), p["sex"], s["organization"], s["url"],
                    s["license"], s["reuse_status"], r["eval"]["clinical_review"], r["schema_version"],
                    json.dumps(r, ensure_ascii=False)))
        for cond in c["conditions"]:
            db.execute("INSERT INTO scenario_conditions VALUES (?,?,?)", (r["id"], cond, int(cond == c["primary_condition"])))
        for i, v in enumerate(r["vitals"]):
            db.execute("INSERT INTO vitals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (r["id"], i, v.get("time"), v.get("loc"), v.get("hr"), v.get("rr"), v.get("bp_systolic"),
                        v.get("bp_diastolic"), v.get("temp_c"), v.get("spo2"), v.get("skin"), v.get("note")))
        for f in r["retrieval"]["red_flags"]:
            db.execute("INSERT INTO red_flags VALUES (?,?)", (r["id"], f))
    for ch in chunks:
        db.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,NULL)",
                   (ch["chunk_id"], ch["scenario_id"], ch["chunk_type"], ch["text"], ch["setting"],
                    ch["primary_condition"], ch["acuity"], ch["age_group"]))
        db.execute("INSERT INTO chunks_fts(text, chunk_id, scenario_id, chunk_type) VALUES (?,?,?,?)",
                   (ch["text"], ch["chunk_id"], ch["scenario_id"], ch["chunk_type"]))
    db.commit()
    db.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    records, errors, seen = [], [], set()
    for f in sorted(RAW.glob("*.json")):
        for raw in json.loads(f.read_text()):
            rid = raw.get("id", "?")
            try:
                rec = ScenarioRecord.model_validate(expand(raw)).model_dump(mode="json")
            except Exception as e:  # noqa: BLE001
                errors.append({"file": f.name, "id": rid, "error": str(e)})
                continue
            if rid in seen:
                errors.append({"file": f.name, "id": rid, "error": "duplicate id"})
                continue
            seen.add(rid)
            records.append(rec)

    chunks = [ch for r in records for ch in make_chunks(r)]
    with open(OUT / "scenarios.jsonl", "w") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(OUT / "chunks.jsonl", "w") as fh:
        for ch in chunks:
            fh.write(json.dumps(ch, ensure_ascii=False) + "\n")
    (OUT / "scenario.schema.json").write_text(json.dumps(ScenarioRecord.model_json_schema(), indent=2))
    write_sqlite(OUT / "scenarios.sqlite", records, chunks)

    report = {
        "schema_version": SCHEMA_VERSION, "records": len(records), "chunks": len(chunks), "errors": errors,
        "by_source": Counter(r["source"]["organization"] for r in records),
        "by_category": Counter(r["classification"]["category"] for r in records),
        "by_acuity": Counter(r["classification"]["acuity"] for r in records),
        "by_setting": Counter(r["context"]["setting"] for r in records),
        "by_primary_condition": Counter(r["classification"]["primary_condition"] for r in records),
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "by_primary_condition"}, indent=2))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
