#!/usr/bin/env bash
# Start Bonsai Mate on macOS / Linux.
#   ./start.sh            # model server (4B) + app on http://localhost:8000
#   ./start.sh 2B         # 2B | 4B | 9B
#   NO_MODEL=1 ./start.sh # app only: protocol cards still work without the model
#   NO_VOICE=1 ./start.sh # skip the transcription-binary notice (typing only)
# The first run needs internet (Python packages, llama.cpp runtime, language and
# speech models). After that it runs offline.
#
# Voice transcription uses the native Argmax Pro Tiny Swift binary. Build it once:
#   cd ../argmax-pro-sdk/native && swift build -c release --product soteria-transcribe
# and keep ARGMAX_API_KEY exported in the shell that runs this script.
set -euo pipefail
cd "$(dirname "$0")"
SIZE="${1:-4B}"
PORT="${PORT:-8000}"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi

TRANSCRIBE_BIN="../argmax-pro-sdk/native/.build/release/soteria-transcribe"
if [ -z "${NO_VOICE:-}" ] && [ ! -x "$TRANSCRIBE_BIN" ]; then
  echo "Voice transcription binary not found at $TRANSCRIBE_BIN."
  echo "Build it:  cd ../argmax-pro-sdk/native && swift build -c release --product soteria-transcribe"
  echo "The app will run with typing only until then."
  echo
fi

if [ -z "${NO_MODEL:-}" ]; then
  # Reuse the Bonsai sample's launcher (OpenAI-compatible API on port 8080).
  bash ../bonsai-models/gguf/start.sh "$SIZE" &
  trap 'kill $! 2>/dev/null || true' EXIT
fi

echo; echo "Open http://localhost:$PORT in your browser."; echo
.venv/bin/python -m uvicorn backend.main:app --port "$PORT"
