# Bonsai Mate: offline emergency-support assistant

A first-aid assistant for people in the wilderness or anywhere without a connection. You talk to it or type, and it answers in short steps and reads them aloud. Everything runs on your own machine:

- **Language model:** [Bonsai 2 Small](../bonsai-models/README.md) through a local llama.cpp server
- **Voice in and out:** the [Argmax SDK](../argmax-pro-sdk/README.md) API (real-time transcription and TTSKit)
- **Knowledge:** protocol cards plus the sourced case studies in [`wildmed-kb/`](wildmed-kb/README.md), searched with SQLite
- **App:** FastAPI backend and a web page with no external assets

> **Prototype, not medical advice.** Every protocol card, case study and expected answer is marked `clinical_review: "pending"`. Nothing here has been checked by a clinician. Do not rely on it in a real emergency.

## Run

**Windows (PowerShell):**
```powershell
.\start.ps1              # model server + app + voice
.\start.ps1 -Size 2B     # 2B | 4B | 9B
.\start.ps1 -NoModel     # no language model: warnings and protocol cards only
.\start.ps1 -NoVoice     # typing only
```
**macOS / Linux:** `./start.sh` (`NO_MODEL=1`, `NO_VOICE=1`)

Then open **http://localhost:8000**.

The first run needs internet for Python packages, the llama.cpp runtime and the language model (through [`../bonsai-models/gguf`](../bonsai-models/gguf/README.md)), and the speech models. After that everything works offline, and the Windows script sets `HF_HUB_OFFLINE=1` once the speech models are cached.

Voice needs your copy of `argmax-windows-shim`. The start scripts look for it next to this repo and in your home folder; otherwise pass `-ShimPath` or set `ARGMAX_SHIM_PATH`. Without it the app runs with typing only.

| Setting | Default | Meaning |
|---|---|---|
| `MATE_LLM_URL` | `http://localhost:8080` | Any OpenAI-compatible local server |
| `MATE_ASR_MODEL` | `base.en` | Speech model, by its Argmax name. `base.en` keeps up with speech on a laptop CPU. |
| `MATE_TTS` | `kokoro` | `kokoro` = TTSKit on the server, `browser` = the browser's built-in voice (instant, more robotic) |

## How it works

```
 microphone ──> /api/listen ──> Argmax real-time transcription ──┐
 keyboard ───────────────────────────────────────────────────────┤
                                                                 v
                    /api/chat   1. red-flag rules        triage.py     (no model)
                                2. protocol cards        knowledge.py  (data/knowledge, SQLite FTS5)
                                3. similar cases         wildmed.py    (wildmed-kb, SQLite FTS5)
                                4. Bonsai                llm.py        (llama-server :8080)
                                                                 │
 speaker <── /api/speak <── Argmax TTSKit <── reply, line by line┘
```

1. **Red flags.** Pattern rules look for life threats ("not breathing", "blood won't stop", "confused"). The warning shows and is spoken immediately, and does not depend on the model.
2. **Protocol cards.** The user's words are matched against `data/knowledge/`. The cards say what to do.
3. **Similar cases.** The whole conversation so far is matched against the case studies in `wildmed-kb/`. The best one or two give the model the questions worth asking, the warning signs, and what trained responders did. A runner-up is dropped unless it matches nearly as well as the best case.
4. **Bonsai** turns that into a short spoken-length reply: what it could be, numbered steps, whether to get help, and one or two questions.

If the language model is not running, steps 1 to 3 still work and the page shows the cards and cases directly. The **Protocols** tab browses every card.

### Voice

Tap **Speak**, talk, and tap again to send. Your words appear in the text box while you talk. Replies are read aloud line by line as they are written ("Read aloud" turns this off).

[`backend/voice.py`](backend/voice.py) uses the same calls as the Argmax samples in this repo: `ArgmaxSDK.with`, `WhisperKitPro`, `makeStreamSession(options:)`, `start(audioInputStream:)`, `results`, and `TTSKit.generate`. The browser streams microphone audio to the server over a WebSocket, and the server feeds it into the stream session.

On Windows and Linux those calls run on `argmax_shim`, the development stand-in for the Pro SDK: open-source models on the CPU, slower and less accurate than the real thing, and not meant to ship. The real SDK is Swift and Kotlin only, so a shipped phone app would make the same calls natively and keep the rest of this design.

The microphone only works on `http://localhost` or over HTTPS. Opening the page from a phone at `http://<laptop-ip>:8000` will load, but the browser will refuse the microphone.

## Data

| Layer | Folder | Role |
|---|---|---|
| Protocol cards | `data/knowledge/` | What to do. 9 placeholder cards written from general first-aid knowledge, labelled "unsourced draft". |
| Case studies | `wildmed-kb/` | 56 sourced cases: questions to ask, warning signs, what responders did. Rebuild with `python wildmed-kb/scripts/build.py`. |
| Benchmark | `data/scenarios/` | 8 original synthetic scenarios and 80 variations, used only to test the assistant. |

Things to know:

- **Licensing.** 52 of the 56 case studies come from all-rights-reserved NOLS material, paraphrased. The app uses them all by default, which is fine for a hackathon demo on your own machine and not for distribution. `ALLOWED_REUSE` in [`backend/wildmed.py`](backend/wildmed.py) restricts which records are used.
- **Cases are written for trained responders.** The model is told to reword them for an untrained person and not to copy details from the case patient, but a small model can still do both. The page shows which cases were used, with their source.
- **Search is by words, not meaning.** "Rattler" does not find the snakebite case; "snake" does. Adding embeddings to `wildmed-kb` (its `chunks.embedding` column and `rrf()` are ready for this) is the fix.
- **Keep the benchmark separate.** The synthetic scenarios test the assistant precisely because they are not in the case-study index. Evaluating on the wildmed cases themselves would be circular.

## Scenario pipeline

Run from this folder with the venv's Python (`.venv\Scripts\python.exe` or `.venv/bin/python`):

```
python -m pipeline stats                         validate all JSON, show counts by source and review status
python -m pipeline ingest <url-or-file> --org "..."   fetch one page or PDF into data/raw/ (gitignored)
python -m pipeline structure data/raw/<file>.txt local model extracts case facts into data/scenarios/drafts/
python -m pipeline vary                          regenerate synthetic variations of every base scenario
python -m pipeline evaluate [--base-only] [--limit N]
                                                 run scenarios through the assistant, store results in eval_runs
```

`evaluate` checks each reply for keywords: did it name the condition, ask the key question, say to get help, and avoid forbidden treatments. It catches obvious misses only, so read the stored replies (`eval_runs` table in `data/mate.db`) before drawing conclusions.

## Tests

```
python -m pytest
```
Covers data validation, card and case search with everyday language, red-flag rules, variation generation, and the evaluation checks. No models needed.

## Next steps

- Replace the placeholder cards with content from authoritative guidelines, with sources recorded.
- Get permission for, or replace, the all-rights-reserved case studies before sharing the app.
- Clinical review, and only then set `clinical_review` to `approved`.
- Embeddings for case search; multi-turn evaluation.
- A native iOS or Android shell with the real Argmax SDK for on-device speech.
