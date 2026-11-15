"""Step 3: expand each base scenario into synthetic test variations.

Variations change how the situation is described and the surrounding
conditions. They are test cases, not extra real patients: the expected
behaviour is copied from the parent and stays clinical_review "pending".
"""
import json

from backend import config, db
from backend.schemas import Scenario, Turn

# (tag, extra question, extra answer, environment overrides)
MODIFIERS: list[tuple[str, str, str, dict]] = [
    ("alone", "Is anyone else with you?", "No. It's just the two of us and nobody knows exactly where we are.", {}),
    ("no_supplies", "What supplies do you have?", "We don't have a first-aid kit. Just our day packs, water and a couple of jackets.", {}),
    ("has_supplies", "What supplies do you have?", "We have a full first-aid kit, a tarp, sleeping bags and a stove.", {}),
    ("bad_weather", "What is the weather doing?", "It's raining hard and the wind is picking up.", {"weather": "rain and wind"}),
    ("high_altitude", "Where are you?", "We're camped high up, above 11,000 feet.", {"type": "high altitude"}),
    ("denies_problem", "How does the person say they feel?", "They keep saying they're fine and that I should stop fussing.", {}),
    ("worsening", "Has anything changed?", "It's been getting worse over the last few minutes.", {}),
]


def variations_for(parent: Scenario) -> list[Scenario]:
    out: list[Scenario] = []

    def add(tag: str, conversation: list[Turn], env: dict | None = None) -> None:
        out.append(
            parent.model_copy(
                deep=True,
                update={
                    "scenario_id": f"{parent.scenario_id}_V{len(out) + 1:02d}",
                    "variation_of": parent.scenario_id,
                    "tags": [*parent.tags, f"var:{tag}"],
                    "source": parent.source.model_copy(update={"source_type": "synthetic"}),
                    "environment": parent.environment.model_copy(update=env or {}),
                    "conversation": conversation,
                },
            )
        )

    # Same situation, described the way different people would say it.
    for text in parent.presentation.lay_descriptions:
        add("phrasing", [Turn(question="What happened?", answer=text)])
    # Same description, different circumstances.
    for tag, question, answer, env in MODIFIERS:
        add(tag, [*parent.conversation, Turn(question=question, answer=answer)], env)
    return out


def generate() -> int:
    """Write variations for every base scenario in seed/ and sourced/."""
    parents = [s for s in db.load_scenarios() if s.variation_of is None]
    out_dir = config.SCENARIOS_DIR / "variations"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.json"):
        old.unlink()
    total = 0
    for parent in parents:
        variations = variations_for(parent)
        (out_dir / f"{parent.scenario_id}.json").write_text(
            json.dumps([v.model_dump() for v in variations], indent=2), encoding="utf-8"
        )
        total += len(variations)
    return total
