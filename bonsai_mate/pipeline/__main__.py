"""Scenario pipeline.  Run from bonsai_mate/:  python -m pipeline <command>

  stats                       validate all JSON and show what the benchmark contains
  ingest <url-or-file>        fetch a page or PDF and save its text to data/raw/
  structure <raw.txt>         local model turns raw text into a draft scenario
  vary                        generate synthetic variations of every base scenario
  evaluate [--limit N]        run scenarios through the assistant and record results
"""
import argparse
from collections import Counter
from pathlib import Path

from backend import db


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stats")
    p = sub.add_parser("ingest")
    p.add_argument("target")
    p.add_argument("--org", required=True, help='e.g. "NOLS Wilderness Medicine"')
    p.add_argument("--type", default="educational_case", dest="source_type")
    p = sub.add_parser("structure")
    p.add_argument("raw", type=Path)
    sub.add_parser("vary")
    p = sub.add_parser("evaluate")
    p.add_argument("--limit", type=int)
    p.add_argument("--base-only", action="store_true", help="skip variations")
    args = parser.parse_args()

    if args.command == "ingest":
        from .ingest import ingest

        out = ingest(args.target, args.org, args.source_type)
        print(f"Saved {out}\nCheck the source's license, then run: python -m pipeline structure {out}")
    elif args.command == "structure":
        from .structure import structure

        out = structure(args.raw)
        print(f"Draft written to {out}\nReview it, fill in the evaluation block, then move it to data/scenarios/sourced/.")
    elif args.command == "vary":
        from .variations import generate

        print(f"Wrote {generate()} variations to data/scenarios/variations/")
    else:
        conn = db.connect()
        cards, total = db.rebuild(conn)
        scenarios = db.all_scenarios(conn)
        if args.command == "stats":
            base = [s for s in scenarios if s.variation_of is None]
            print(f"Protocol cards: {cards}")
            print(f"Scenarios: {total} ({len(base)} base, {total - len(base)} variations)")
            for label, counts in (
                ("By source", Counter(s.source.organization for s in scenarios)),
                ("License verified", Counter(s.source.license_verified for s in scenarios)),
                ("Clinical review", Counter(s.evaluation.clinical_review for s in scenarios)),
            ):
                print(f"{label}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        else:
            from .evaluate import evaluate

            if args.base_only:
                scenarios = [s for s in scenarios if s.variation_of is None]
            scenarios = scenarios[: args.limit]
            passed = evaluate(conn, scenarios)
            print(f"\n{passed}/{len(scenarios)} passed. Full responses are in the eval_runs table of data/mate.db.")


if __name__ == "__main__":
    main()
