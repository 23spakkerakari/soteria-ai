"""Voice in and out, built on the Argmax SDK API.

  speech -> text:  WhisperKitPro real-time stream session (confirmed + hypothesis text)
  text -> speech:  TTSKit

On Windows and Linux this runs on argmax_shim, the development stand-in for the
Argmax Pro SDK (same call names, open-source models, slower). The real SDK is
Swift/Kotlin only, so a shipped Apple or Android build would make these same
calls natively: makeStreamSession(options:), start(audioInputStream:), results,
TTSKit.generate(text:). See ../argmax-pro-sdk for the Swift twin of each call.

Everything loads lazily in a background thread. If argmax_shim is not
installed the app still runs, with typing only.
"""
import io
import os
import threading

import numpy as np

from . import config

SAMPLE_RATE = 16_000

status = {"listen": "loading", "speak": "loading" if config.TTS_ENGINE == "kokoro" else "browser"}
_whisper_kit_pro = None
_tts_kit = None
_tts_lock = threading.Lock()
# One microphone session at a time: the model is a single shared instance.
listen_lock = threading.Lock()


def _load() -> None:
    global _whisper_kit_pro, _tts_kit
    try:
        from argmax_shim.argmax import ArgmaxConfig, ArgmaxSDK, WhisperKitPro, WhisperKitProConfig

        # Swift: await ArgmaxSDK.with(ArgmaxConfig(apiKey: key)). The shim accepts any key.
        ArgmaxSDK.with_(ArgmaxConfig(api_key=os.environ.get("ARGMAX_API_KEY", "ax_dev_placeholder")))
        _whisper_kit_pro = WhisperKitPro(WhisperKitProConfig(model=config.ASR_MODEL))
        # Run one silent pass so the first real utterance is not slowed by warm-up.
        _whisper_kit_pro.transcribe(audio_array=np.zeros(SAMPLE_RATE, np.float32))
        status["listen"] = "ready"
    except Exception as err:
        status["listen"] = f"unavailable: {err}"
        print(f"Voice input unavailable: {err}")

    if config.TTS_ENGINE != "kokoro":
        return
    try:
        from argmax_shim.ttskit import TTSKit, TTSKitConfig, TTSModelVariant

        # Kokoro is the shim's laptop-CPU engine. Native TTSKit would use .qwen3TTS_0_6b.
        _tts_kit = TTSKit(TTSKitConfig(model=TTSModelVariant.KOKORO_82M))
        _tts_kit.generate("Ready.")
        status["speak"] = "ready"
    except Exception as err:
        status["speak"] = "browser"  # fall back to the browser's built-in voice
        print(f"Server text-to-speech unavailable, using the browser voice: {err}")


def start_loading() -> None:
    threading.Thread(target=_load, daemon=True, name="voice-load").start()


def open_session():
    """Start a real-time transcription session. Returns (continuation, session):
    push 16 kHz mono float32 audio with continuation.yield_(), end with
    continuation.finish(), and read updates from session.results."""
    from argmax_shim.argmax import DecodingOptions, DecodingOptionsPro, StreamTranscriptionMode
    from argmax_shim.core.audio import make_audio_stream

    options = DecodingOptionsPro(
        base=DecodingOptions(language="en"),
        stream_transcription_mode=StreamTranscriptionMode.voice_triggered(min_process_interval=0.5),
    )
    audio_stream, continuation = make_audio_stream()
    session = _whisper_kit_pro.make_stream_session(options=options)
    session.start(audio_stream)
    return continuation, session


def to_16k(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    from argmax_shim.core.audio import resample

    return resample(samples, sample_rate, SAMPLE_RATE)


def speak(text: str) -> bytes:
    """Synthesize text and return a WAV file."""
    import soundfile as sf

    with _tts_lock:
        result = _tts_kit.generate(text)
    out = io.BytesIO()
    sf.write(out, result.audio, result.sample_rate, format="WAV", subtype="PCM_16")
    return out.getvalue()
