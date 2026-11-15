"""Voice input and output.

  speech -> text: file-based transcription through the native Argmax Pro Tiny
                  (Swift) CLI (see transcribe.py). The browser records audio,
                  posts it to /api/transcribe, and the transcriber returns text.
  text  -> speech: the browser's built-in speech synthesis (SpeechSynthesis).
                  No server-side TTS is used, so replies stay fully offline.

Transcription readiness is reported through `status["listen"]`, which the
frontend uses to enable the microphone button. There is no streaming session
here: recordings are transcribed whole-file, which is the reliable path for the
Argmax Pro `tiny` model.
"""
import threading

from . import config, transcribe

status = {"listen": "loading", "speak": "browser"}


def start_loading() -> None:
    threading.Thread(target=_check, daemon=True, name="voice-load").start()


def _check() -> None:
    try:
        if transcribe.binary_available():
            status["listen"] = "ready"
        else:
            status["listen"] = (
                f"unavailable: transcription binary not found at {config.TRANSCRIBE_BIN}"
            )
    except Exception as err:  # never take the app down over voice
        status["listen"] = f"unavailable: {err}"
