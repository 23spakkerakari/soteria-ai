"""File-based speech-to-text via the native Argmax Pro Tiny (Swift) CLI.

The browser records audio and posts bytes to /api/transcribe. This module saves
them to a temporary file with an extension the transcriber can read, then runs
the compiled Swift binary (argmax-pro-sdk/native/.build/release/soteria-transcribe),
which transcribes with the real Argmax Pro SDK's `tiny` Whisper model and prints
a single JSON line {"text": "..."} to stdout.

Everything is local: the ARGMAX_API_KEY is read from the environment and passed
through to the subprocess (never committed or logged). Runs in a worker thread so
the FastAPI event loop is not blocked while the model loads and transcribes.
"""
import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

from . import config


class TranscriptionError(Exception):
    pass


# Files the transcriber (AVFoundation) can decode. The frontend sends 16 kHz
# mono PCM WAV, but we accept the other common formats for convenience.
_EXT_BY_CONTENT_TYPE = {
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mp4": ".m4a",
    "audio/m4a": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/aac": ".aac",
    "audio/aiff": ".aiff",
    "audio/x-aiff": ".aiff",
    "audio/x-caf": ".caf",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}


def binary_available() -> bool:
    return Path(config.TRANSCRIBE_BIN).is_file()


def _ext_for(content_type: str) -> str:
    mime = (content_type or "").split(";")[0].strip().lower()
    return _EXT_BY_CONTENT_TYPE.get(mime, ".wav")


async def transcribe_bytes(data: bytes, content_type: str = "audio/wav") -> str:
    """Validate, save, transcribe, and clean up audio bytes. Returns the text."""
    if not data:
        raise TranscriptionError("Received empty audio.")
    if len(data) > config.MAX_AUDIO_BYTES:
        raise TranscriptionError(
            f"Audio too large ({len(data)} bytes; limit is {config.MAX_AUDIO_BYTES})."
        )

    tmpdir = tempfile.mkdtemp(prefix="soteria-audio-")
    path = os.path.join(tmpdir, f"recording{_ext_for(content_type)}")
    try:
        with open(path, "wb") as fh:
            fh.write(data)
        return await asyncio.to_thread(_run, path)
    finally:
        for candidate in (path, tmpdir):
            try:
                if os.path.isdir(candidate):
                    os.rmdir(candidate)
                elif os.path.exists(candidate):
                    os.remove(candidate)
            except OSError:
                pass


def _run(path: str) -> str:
    if not binary_available():
        raise TranscriptionError(
            f"Transcription binary not found at {config.TRANSCRIBE_BIN}. "
            "Build it with: cd argmax-pro-sdk/native && swift build -c release --product soteria-transcribe"
        )
    if not os.environ.get("ARGMAX_API_KEY"):
        raise TranscriptionError("ARGMAX_API_KEY is not set in the server environment.")

    # Argument list, never shell interpolation.
    cmd = [config.TRANSCRIBE_BIN, path]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            timeout=config.TRANSCRIBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise TranscriptionError(f"Transcription timed out after {config.TRANSCRIBE_TIMEOUT}s.")
    except OSError as err:
        raise TranscriptionError(f"Could not run transcriber: {err}")

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or f"transcriber exited with code {proc.returncode}"
        raise TranscriptionError(detail)

    return _parse_stdout(proc.stdout)


def _parse_stdout(stdout: str) -> str:
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = obj.get("text")
            if isinstance(text, str):
                return text.strip()
    raise TranscriptionError("Transcriber produced no readable JSON output.")
