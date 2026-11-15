#!/usr/bin/env bash
# Start Bonsai Mate on macOS / Linux.
#   ./start.sh            # model server (4B) + app on http://localhost:8000
#   ./start.sh 2B         # 2B | 4B | 9B
#   NO_MODEL=1 ./start.sh # app only: protocol cards still work without the model
#   NO_VOICE=1 ./start.sh # skip the voice packages (typing only)
#   ARGMAX_SHIM_PATH=~/code/argmax-windows-shim ./start.sh
# The first run needs internet (Python packages, llama.cpp runtime, language and speech models).
# After that it runs offline.
set -euo pipefail
cd "$(dirname "$0")"
SIZE="${1:-4B}"
PORT="${PORT:-8000}"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi

if [ -z "${NO_VOICE:-}" ] && ! .venv/bin/python -c "import argmax_shim, faster_whisper, kokoro" 2>/dev/null; then
  # Voice runs on the Argmax SDK API, through the development shim on non-Apple machines.
  SHIM="${ARGMAX_SHIM_PATH:-}"
  for candidate in ../../argmax-windows-shim "$HOME/argmax-windows-shim"; do
    [ -z "$SHIM" ] && [ -d "$candidate" ] && SHIM="$candidate"
  done
  if [ -n "$SHIM" ]; then
    echo "Installing voice packages from $SHIM (large download the first time)..."
    .venv/bin/python -m pip install --quiet -e "$SHIM[whisper,tts-kokoro]"
  else
    echo "argmax-windows-shim not found: voice is off. Set ARGMAX_SHIM_PATH to enable it."
  fi
fi

if [ -z "${NO_MODEL:-}" ]; then
  # Reuse the Bonsai sample's launcher (OpenAI-compatible API on port 8080).
  bash ../bonsai-models/gguf/start.sh "$SIZE" &
  trap 'kill $! 2>/dev/null || true' EXIT
fi

echo; echo "Open http://localhost:$PORT in your browser."; echo
.venv/bin/python -m uvicorn backend.main:app --port "$PORT"
