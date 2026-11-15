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

# Sourced case studies, built by wildmed-kb/scripts/build.py.
WILDMED_DB = ROOT / "wildmed-kb" / "data" / "build" / "scenarios.sqlite"

# Voice (Argmax). Whisper model name as the Argmax SDK spells it. base.en keeps up
# with speech on a laptop CPU through the Windows shim; on Apple Silicon with the
# real SDK use large-v3-v20240930_626MB.
ASR_MODEL = os.environ.get("MATE_ASR_MODEL", "base.en")
# "kokoro" = spoken replies from TTSKit on the server. "browser" = the browser's built-in voice.
TTS_ENGINE = os.environ.get("MATE_TTS", "kokoro")

DB_PATH =Path(os.environ.get("MATE_DB", DATA / "mate.db"))
# Any OpenAI-compatible local server (llama-server from ../bonsai-models/gguf by default).
LLM_URL = os.environ.get("MATE_LLM_URL", "http://localhost:8080").rstrip("/")
