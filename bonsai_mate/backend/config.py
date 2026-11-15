import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FRONTEND = ROOT / "frontend"

CARDS_DIR = DATA / "knowledge"
SCENARIOS_DIR = DATA / "scenarios"
# Only these folders are loaded into the benchmark. drafts/ holds unchecked
# LLM output and stays out until a person moves a file into sourced/.
SCENARIO_FOLDERS = ("seed", "sourced", "variations")
DRAFTS_DIR = SCENARIOS_DIR / "drafts"
RAW_DIR = DATA / "raw"

# Sourced case studies, built by wildmed-kb/scripts/build.py. Indexed into mate.db on start.
WILDMED_CASES = ROOT / "wildmed-kb" / "data" / "build" / "scenarios.jsonl"

# Voice: file-based transcription via the native Argmax Pro Tiny (Swift) CLI.
# Path to the compiled binary (built from ../argmax-pro-sdk/native).
TRANSCRIBE_BIN = os.environ.get(
    "MATE_TRANSCRIBE_BIN",
    str(ROOT.parent / "argmax-pro-sdk" / "native" / ".build" / "release" / "soteria-transcribe"),
)
# Seconds before a transcription attempt is abandoned.
TRANSCRIBE_TIMEOUT = int(os.environ.get("MATE_TRANSCRIBE_TIMEOUT", "300"))
# Maximum accepted recording size in bytes.
MAX_AUDIO_BYTES = int(os.environ.get("MATE_MAX_AUDIO_BYTES", str(25 * 1024 * 1024)))

# Rounds of questions between the first reply and the full plan (3 questions a round).
ASSESSMENT_ROUNDS = int(os.environ.get("MATE_ASSESSMENT_ROUNDS", "3"))

DB_PATH = Path(os.environ.get("MATE_DB", DATA / "mate.db"))
# Any OpenAI-compatible local server (llama-server from ../bonsai-models/gguf by default).
LLM_URL = os.environ.get("MATE_LLM_URL", "http://localhost:8080").rstrip("/")
