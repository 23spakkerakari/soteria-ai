"""Voice in and out, built on the Argmax SDK API.

  speech -> text:  WhisperKitPro real-time stream session (confirmed + hypothesis text)
  text -> speech:  TTSKit

On Windows and Linux this runs on argmax_shim, the development stand-in for the
Argmax Pro SDK (same call names, open-source models, slower). The real SDK is
Swift/Kotlin only, so a shipped Apple or Android build would make these same
calls natively: makeStreamSession(options:), start(audioInputStream:), results,
TTSKit.generate(text:). See ../argmax-pro-sdk for the Swift twin of each call.

Everything loads in a background thread. If argmax_shim is not installed the
app still runs, with typing only.
"""
import io
import os
import queue
import threading
import time

import numpy as np

from . import config

SAMPLE_RATE = 16_000
# In "auto" mode TTSKit is used only if it synthesizes at least this many times
# faster than real time. Slower than that, spoken replies stall between lines,
# and the browser's built-in voice (instant, more robotic) is the better choice.
MIN_TTS_SPEED = 1.2

# listen: loading | ready | unavailable: <why>        speak: loading | ready | browser
status = {"listen": "loading", "speak": "browser" if config.TTS_ENGINE == "browser" else "loading"}
_whisper_kit_pro = None
_tts_kit = None
_tts_lock = threading.Lock()
# One microphone session at a time: the model is a single shared instance.
listen_lock = threading.Lock()


class MicAudio:
    """The session's audio input stream (Swift: an AsyncStream<[Float]>), fed from
    the browser. Each read returns everything received since the last one as a
    single chunk, so on a slow CPU the session falls behind by one pass at most
    instead of running one pass per network packet. The first read waits for a
    second of audio, because Whisper invents words when given a fraction of one."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()

    def yield_(self, samples: np.ndarray) -> None:
        self._queue.put(samples)

    def finish(self) -> None:
        self._queue.put(None)

    def __iter__(self):
        wanted = SAMPLE_RATE
        while True:
            audio, received, finished = [], 0, False
            while not finished and (received < wanted or not self._queue.empty()):
                chunk = self._queue.get()
                if chunk is None:
                    finished = True
                else:
                    audio.append(chunk)
                    received += len(chunk)
            if audio:
                yield np.concatenate(audio)
            if finished:
                return
            wanted = 1


def _load() -> None:
    global _whisper_kit_pro, _tts_kit
    # Once every speech model has loaded successfully, later starts skip the
    # Hugging Face network checks entirely.
    wanted = f"{config.ASR_MODEL}|{config.TTS_ENGINE}"
    remembered = config.VOICE_READY.read_text().split("\n") if config.VOICE_READY.exists() else []
    if remembered[:1] == [wanted]:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    # "auto" already measured TTSKit as too slow here: don't load it again on every start.
    skip_tts = config.TTS_ENGINE == "auto" and remembered == [wanted, "browser"]

    try:
        from argmax_shim.argmax import ArgmaxConfig, ArgmaxSDK, WhisperKitPro, WhisperKitProConfig

        # Swift: await ArgmaxSDK.with(ArgmaxConfig(apiKey: key)). The shim accepts any key.
        ArgmaxSDK.with_(ArgmaxConfig(api_key=os.environ.get("ARGMAX_API_KEY", "ax_dev_placeholder")))
        _whisper_kit_pro = WhisperKitPro(WhisperKitProConfig(model=config.ASR_MODEL))
    except Exception as err:
        status["listen"] = f"unavailable: {err}"
        print(f"Voice input unavailable: {err}")

    warm_up_clip = np.random.default_rng(0).normal(0, 0.05, SAMPLE_RATE * 2).astype(np.float32)
    tts_ok = config.TTS_ENGINE == "browser" or skip_tts
    if skip_tts:
        status["speak"] = "browser"
    if not tts_ok:
        try:
            from argmax_shim.ttskit import TTSKit, TTSKitConfig, TTSModelVariant

            # Kokoro is the shim's laptop-CPU engine. Native TTSKit would use .qwen3TTS_0_6b.
            _tts_kit = TTSKit(TTSKitConfig(model=TTSModelVariant.KOKORO_82M))
            _synthesize("Ready.")  # loads the model; downloads it and the voice on the first run
            started = time.perf_counter()
            audio, rate = _synthesize("Keep the person warm, still, and off the cold ground.")
            speed = (len(audio) / rate) / (time.perf_counter() - started)
            warm_up_clip = to_16k(audio, rate)
            tts_ok = True
            if config.TTS_ENGINE == "auto" and speed < MIN_TTS_SPEED:
                status["speak"] = "browser"
                print(f"TTSKit runs at {speed:.1f}x real time on this machine: using the browser voice instead. "
                      "Set MATE_TTS=kokoro to force TTSKit.")
            else:
                status["speak"] = "ready"
        except Exception as err:
            status["speak"] = "browser"
            print(f"Server text-to-speech unavailable, using the browser voice: {err}")

    if _whisper_kit_pro is not None:
        try:
            # Run a real utterance through a stream session so the user's first words are not slowed by warm-up.
            mic_audio, session = open_session()
            mic_audio.yield_(warm_up_clip)
            mic_audio.finish()
            for _ in session.results:
                pass
            status["listen"] = "ready"
            if tts_ok:
                config.VOICE_READY.write_text(f"{wanted}\n{status['speak']}")
        except Exception as err:
            status["listen"] = f"unavailable: {err}"
            print(f"Voice input unavailable: {err}")


def start_loading() -> None:
    threading.Thread(target=_load, daemon=True, name="voice-load").start()


def open_session():
    """Start a real-time transcription session. Returns (mic_audio, session):
    push 16 kHz mono float32 audio with mic_audio.yield_(), end with
    mic_audio.finish(), and read updates from session.results."""
    from argmax_shim.argmax import DecodingOptions, DecodingOptionsPro, StreamTranscriptionMode

    options = DecodingOptionsPro(
        base=DecodingOptions(language="en"),
        stream_transcription_mode=StreamTranscriptionMode.voice_triggered(min_process_interval=0.5),
    )
    mic_audio = MicAudio()
    session = _whisper_kit_pro.make_stream_session(options=options)
    session.start(mic_audio)
    return mic_audio, session


def transcribe(audio: np.ndarray) -> str:
    """One pass over a whole utterance (Swift: transcribe(audioArray:decodeOptions:)).

    This is the text that gets sent to the assistant. The shim's stream session
    can drop or mangle words when the CPU falls behind, so its output is shown
    only as a live preview. On the real SDK, whose streaming accuracy matches
    file transcription, the session's confirmed text could be used directly."""
    from argmax_shim.argmax import DecodingOptions, WhisperKitProUtils

    if len(audio) < SAMPLE_RATE * 0.3 or float(np.sqrt(np.mean(audio**2))) < 0.003:
        return ""  # nothing said; Whisper would invent something
    results = _whisper_kit_pro.transcribe(audio_array=audio, decode_options=DecodingOptions(language="en"))
    return WhisperKitProUtils.merge_transcription_results(results).text.strip()


def to_16k(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    from argmax_shim.core.audio import resample

    return resample(samples, sample_rate, SAMPLE_RATE)


def _synthesize(text: str) -> tuple[np.ndarray, int]:
    from argmax_shim.ttskit import Qwen3Speaker

    with _tts_lock:
        result = _tts_kit.generate(text, speaker=Qwen3Speaker.SERENA)
    return result.audio, result.sample_rate


def speak(text: str) -> bytes:
    """Synthesize text and return a WAV file."""
    import soundfile as sf

    audio, rate = _synthesize(text)
    out = io.BytesIO()
    sf.write(out, audio, rate, format="WAV", subtype="PCM_16")
    return out.getvalue()
