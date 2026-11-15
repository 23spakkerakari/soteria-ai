"""Step 2: use the local model to turn extracted case text into a draft scenario.

The model extracts only what the source says happened (the facts). It is not
asked what the right treatment is: the evaluation block is left empty with
clinical_review "pending" for a person to fill in from approved guidelines.
Drafts land in data/scenarios/drafts/ and are not part of the benchmark until
someone checks them and moves them to data/scenarios/sourced/.
"""
import asyncio
import json
import re
from pathlib import Path

from backend import config, llm
from backend.schemas import Scenario

MAX_CHARS = 12000

PROMPT = """Below is the text of an educational emergency-medicine case. Extract the situation into JSON with exactly these keys:

{
  "title": "short title",
  "tags": ["lowercase_topic", "..."],
  "environment": {"type": "...", "weather": "...", "connectivity": "none", "distance_to_help": "..."},
  "patient": {"age": number or null, "sex": "...", "medical_history": "..."},
  "presentation": {
    "chief_complaint": "...",
    "symptoms": ["snake_case_symptom", "..."],
    "lay_descriptions": ["how a bystander with no medical training might describe this in one or two sentences", "a second phrasing"]
  },
  "conversation": [{"question": "What happened?", "answer": "first-person description from the patient or bystander"}]
}

Use only facts stated in the text. Use "unknown" where the text does not say. Do not include any treatment, diagnosis discussion, or advice. Reply with the JSON object only.

CASE TEXT:
"""


def _next_id() -> str:
    used = [
        int(m.group(1))
        for folder in ("drafts", "sourced")
        for p in (config.SCENARIOS_DIR / folder).glob("SRC_*.json")
        if (m := re.match(r"SRC_(\d{4})", p.stem))
    ]
    return f"SRC_{max(used, default=0) + 1:04d}"


def structure(raw_path: Path) -> Path:
    meta = json.loads(raw_path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    text = raw_path.read_text(encoding="utf-8")[:MAX_CHARS]
    reply = asyncio.run(llm.complete([{"role": "user", "content": PROMPT + text}], json_mode=True, max_tokens=2048))
    extracted = json.loads(reply)
    extracted.pop("evaluation", None)  # never trust model-written expected answers

    scenario = Scenario.model_validate(
        {
            **extracted,
            "scenario_id": _next_id(),
            "source": {k: meta[k] for k in ("organization", "source_type", "source_url", "license_verified")},
        }
    )
    config.DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    out = config.DRAFTS_DIR / f"{scenario.scenario_id}.json"
    out.write_text(scenario.model_dump_json(indent=2), encoding="utf-8")
    return out
