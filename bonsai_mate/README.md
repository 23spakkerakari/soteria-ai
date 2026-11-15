# Soteria: offline emergency-support assistant

A first-aid assistant for people in the wilderness or anywhere without a connection. You talk to it or type. It tells you the most urgent thing to do, asks questions to understand the situation, and then gives a plan fitted to your answers. Everything runs on your own machine:

- **Language model:** [Bonsai 2 Small](../bonsai-models/README.md) through a local llama.cpp server
- **Voice in:** the [Argmax Pro SDK](../argmax-pro-sdk/README.md) using the `tiny` model (file-based transcription via a local Swift binary), read-aloud via the browser's built-in speech synthesis
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

Then open **http://localhost:8000/app/** for the app. The landing page is at **http://localhost:8000/**.

The first run needs internet for Python packages, the llama.cpp runtime and the language model (through [`../bonsai-models/gguf`](../bonsai-models/gguf/README.md)), and the Argmax `tiny` speech model. After that everything works offline.

Voice transcription runs through the native Argmax Pro SDK. Set your key and build the `tiny` transcription binary once:

```bash
export ARGMAX_API_KEY=ax_...            # from https://app.argmaxinc.com (starts with ax_)
cd ../argmax-pro-sdk/native
swift package-registry set --global --scope argmaxinc https://api.argmaxinc.com/v1/sdk
swift package-registry login https://api.argmaxinc.com/v1/sdk/login --token "$ARGMAX_SECRET_TOKEN"
swift build -c release --product soteria-transcribe
```

`ARGMAX_API_KEY` must stay exported in the shell that runs `./start.sh`. The Argmax license is cached in the Keychain after first use, so transcription keeps working with Wi-Fi off once the `tiny` model is cached. Without the binary or the key, the app runs with typing only; `start.sh` prints a note when the binary is missing.

#### Stop the "login password" Keychain prompt

macOS may ask for your login password on every transcription. Argmax stores its offline license in the Keychain, and the access decision is tied to the binary's signing identity — a `swift build` binary is only ad-hoc signed, so macOS can't pin "Always Allow" and re-prompts. Two fixes:

1. **Quick:** the next time the dialog appears, click **Always Allow** and enter your password once (not just "Allow").
2. **Permanent:** sign the binary with a stable identity. Create a self-signed certificate once (Keychain Access → Certificate Assistant → Create a Certificate → Name "Soteria Dev", Identity Type "Self Signed Root", Certificate Type "Code Signing"), then run `./sign.sh` from `../argmax-pro-sdk/native` after every `swift build`. After the next "Always Allow", the prompt stops — and offline transcription keeps working.

> Do **not** set Argmax's `keychainPersistence = false` to silence the prompt: that stops caching the offline license, so transcription would need to reach Argmax's server on every run and fail with Wi-Fi off.

| Setting | Default | Meaning |
|---|---|---|
| `MATE_LLM_URL` | `http://localhost:8080` | Any OpenAI-compatible local server |
| `MATE_ASSESSMENT_ROUNDS` | `3` | Rounds of questions between the first reply and the plan |
| `MATE_TRANSCRIBE_BIN` | `../argmax-pro-sdk/native/.build/release/soteria-transcribe` | Compiled Argmax transcription binary |
| `ARGMAX_API_KEY` | *(required)* | Argmax Pro license key (starts with `ax_`). Never commit it. |
| `MATE_TRANSCRIBE_TIMEOUT` | `300` | Seconds before a transcription attempt is abandoned |
| `MATE_MAX_AUDIO_BYTES` | `26214400` | Largest accepted recording (25 MiB) |

## How it works

```
 microphone ──> /api/transcribe ──> Argmax Pro tiny (Swift) ──┐
 keyboard ───────────────────────────────────────────────────────┤
                                                                 v
                    /api/chat   1. red-flag rules        triage.py     (no model)
                                2. protocol cards        knowledge.py  (data/knowledge, SQLite FTS5)
                                3. similar cases         wildmed.py    (wildmed-kb, SQLite FTS5)
                                4. Bonsai                llm.py        (llama-server :8080)
                                                                 │
 speaker <── browser speech synthesis <── reply, line by line┘
```

1. **Red flags.** Pattern rules look for life threats ("not breathing", "blood won't stop", "confused"). The warning shows and is spoken immediately, and does not depend on the model. A red flag also forces its protocol card into the prompt: "he's not breathing" always brings the CPR card.
2. **Protocol cards.** Everything the user has said is matched against `data/knowledge/`. The cards say what to do.
3. **Similar case.** The same text is matched against the case studies from `wildmed-kb/`. The best case gives the model the questions worth asking, the warning signs, and what trained responders did. Where no card exists (altitude illness, seizures, lightning and most other conditions), the case is the only source of steps.
4. **Bonsai** turns that into a short spoken-length reply. It does not ask the questions: the app does (see below).

When neither a card nor a case matches, the model answers from its own general first-aid knowledge, and the page says so ("General guidance from the model's own knowledge"). Three limits apply whatever the source: no drug doses, no cutting or other invasive procedures, and never telling someone not to seek help.

### The conversation: three phases

| Phase | What the reply contains | Then |
|---|---|---|
| 1. Urgent action | What it could be, the two most urgent actions, whether to get help. If a red flag fired, every step of that card straight away. | The app asks 2 questions |
| 2. Questions | A short reaction to the answers, and a new step only if an answer changes what to do. | The app asks 3 more, for up to 3 rounds (`MATE_ASSESSMENT_ROUNDS`) |
| 3. Plan | The full plan, fitted to the answers: supplies, shelter, who is there, how far help is. | Later messages are follow-ups |

"Skip the questions: give me the plan now" jumps to phase 3 at any point, and so does saying "what should I do".

**The question pool** ([`backend/questions.py`](backend/questions.py)). The app chooses the questions, so they are never repeated and can be recorded exactly. They are asked in the order that most affects what to do right now:

0. The matched card's own `ask_first` questions, when it has any: what it must know before its first step ("What kind of bear is it?").
1. Up to four questions from the matched wildmed case's `must_ask` list, reworded to "they" or "you". These are about the actual problem: "Can you put weight on it?" for an ankle, "How big is the burn?" for a burn.
2. One combined safety check: "Is anything else wrong: trouble breathing, heavy bleeding, or are they drowsy or confused?"
3. Circumstances: getting better or worse, when it started, supplies, weather and shelter, reaching help.
4. Basic information: medical conditions, medication and allergies, pain, age, who is there, food and drink.

Two situations change that order:

- **It looks serious.** If a red flag fired, or the mechanism is serious (a fall, a collapse, lightning), the safety check becomes three separate life-threat questions (awake, breathing, heavy bleeding) and they move ahead of the case questions.
- **Nobody is hurt.** When the matched card is about wildlife (a bear nearby) and no red flag fired, the app asks only what you have, where you can shelter, who is with you and whether you can reach help.

A question is skipped when the user has already covered it in their own words ("Are they shivering?" is not asked after "she can't stop shivering").

**The record** ([`backend/incident.py`](backend/incident.py)). Every question asked and every answer is stored in SQLite under the incident's id, and the whole record is put in front of the model on every turn under "WHAT WE KNOW SO FAR". An answer is the user's whole next message, stored against the questions of that round; the app does not try to split one spoken reply across three questions. `GET /api/incidents/<id>` returns the record.

**The panel.** "What I know so far" sits beside the chat (above it on a phone) and updates on every turn: what was reported, what it could be, warning signs, each round of questions with the answer, and how many questions remain.

Matching uses everything the user has said, minus what they have ruled out. "No bleeding", "not swollen" and "breathing is fine" are removed before matching, in the first message as well as in later answers, so "awake, no bleeding, but he had a seizure" matches seizure material. A denial that is itself the problem ("the bleeding is not stopping", "no pulse", "not breathing") is kept. Red flags are checked on every word, because a false alarm is the safer mistake.

Weak matches are left out. Word search always finds something, so a match has to earn its place (`match()` in `knowledge.py`, `MIN_SCORE` in `wildmed.py`). The bar depends on the message, because the score grows with the number of words that hit: a one- or two-word message ("burn") needs its one good word to hit, and a longer one needs either a strong score or a fair score with a clear lead over the next card. The thresholds are heuristics tuned on about twenty example messages (see `test_card_matching_depends_on_how_much_was_said`), not a learned model.

The page shows the matched card, opened, under every answer. The model rewords the card and sometimes gets a step wrong; the card's own text is the reference.

If the language model is not running, steps 1 to 3 still work and the page shows the cards and case directly. The **Protocols** tab browses every card.

### Speed

Measured on a laptop with Intel Iris Xe graphics and Bonsai 2 4B: the model reads about 50 tokens a second and writes about 13. A reply starts 10 to 25 seconds after the question and takes another 10 to finish; the warning and the cards appear at once. That is why the prompt carries only the best card and the best case, and why the fixed rules are cached (`id_slot` in `llm.py`). `-Size 2B` is faster; a discrete GPU is much faster.

### Voice

Tap **Speak**, talk, and tap again to stop. The browser records your speech as 16 kHz mono audio, encodes it to WAV, and posts it to `/api/transcribe`. A local Swift binary ([`argmax-pro-sdk/native/Sources/SoteriaTranscribe`](../argmax-pro-sdk/native/Sources/SoteriaTranscribe)) transcribes the file with the real Argmax Pro SDK using the `tiny` model and returns `{"text": "..."}`. The transcript fills the input box and is submitted through the normal chat pipeline, so the question pool, safety checks, and Bonsai behave exactly as they do for typed messages.

[`backend/transcribe.py`](backend/transcribe.py) saves the recording to a temporary file, runs the Swift binary in a worker thread (never the event loop), and cleans up afterwards. Transcription is file-based rather than streamed, which is the reliable path for the `tiny` model.

State shown on the button: **Speak** → **Recording… tap to stop** → **Transcribing…** → **Generating response…**, then back to **Speak** (with an alert if anything fails). Taps are ignored while a transcription or reply is in flight, so a recording can't be submitted twice.

Replies are read aloud by the browser's built-in speech synthesis ("Read aloud" turns this off), so no server TTS or extra models are needed.

The microphone only works on `http://localhost` or over HTTPS. Opening the page from a phone at `http://<laptop-ip>:8000` will load, but the browser will refuse the microphone.

## Data

| Layer | Folder | Role |
|---|---|---|
| Protocol cards | `data/knowledge/` | What to do. 9 placeholder cards written from general first-aid knowledge, labelled "unsourced draft", plus 5 bear cards taken from the US National Park Service (public domain, source and retrieval date recorded). |
| Case studies | `wildmed-kb/` | 56 sourced cases: questions to ask, warning signs, what responders did. Rebuild with `python wildmed-kb/scripts/build.py`; the app re-indexes them on every start. |
| Benchmark | `data/scenarios/` | 8 original synthetic scenarios and 80 variations, used only to test the assistant. |

### Card schema

A card ([`backend/schemas.py`](backend/schemas.py), `Card`) says what to do: `recognize`, `do_first`, `do_not`, `escalate_when`, and `keywords` in the words frightened people use. Its `source` records `organization`, `source_url`, `license`, whether someone verified that license on the publisher's site (`license_verified`), `retrieved_on`, and `verbatim_allowed`: for authoritative public-domain text the model is told to keep the card's own wording and change only who it is addressed to.

Three optional fields handle advice that depends on facts the user may not have given yet. The bear cards are the example: for a grizzly defending itself you play dead, for a black bear you fight back, and for any bear that came into your tent you fight back. Word search cannot tell those cards apart, because they share nearly all their words, so the choice is made by rules in the card data (`knowledge.resolve`), deterministically, like the red flags:

| Field | Meaning | Bear example |
|---|---|---|
| `applies_when` | `species`, `regions` and `situation` the card is for, plus `requires`: a pattern the user must have said, otherwise the `fallback` card is used | The grizzly card requires `grizz\|brown bear\|hump\|dish`; without it, the general "Bear attack" card is used |
| `routes` | Once the user has said enough (`when`), use a more specific card | "Bear attack" routes `tent\|stalk` to the tent card, then `grizz` and `black bear` to the species cards; the tent rule comes first because it overrides species |
| `ask_first` | Questions the app asks before anything else, because the answer changes the first step. `topic` says when the user has already answered | "What kind of bear is it?" and "Did it come into your tent?" |

The answer to an `ask_first` question is matched on the next turn like any other message, so "it has a hump" routes to the grizzly card. The same `label` across a family means the same question, so it is never asked twice. `regions` is recorded but not yet used for filtering: it is there for regional packs.

Things to know:

- **Licensing.** 52 of the 56 case studies come from all-rights-reserved NOLS material, paraphrased. The app uses them all by default, which is fine for a hackathon demo on your own machine and not for distribution. `ALLOWED_REUSE` in [`backend/wildmed.py`](backend/wildmed.py) restricts which records are used.
- **Cases are written for trained responders.** The model is told to reword them for an untrained person, to skip steps that need equipment or training, and not to copy details from the case patient. A small model still slips: in testing it turned "protect airway, recovery position" into "cover his airway with a towel", and once told a bystander to give oxygen at a flow rate. The rules were tightened after each, but this class of error is not solved. The page shows which case was used, with its source.
- **Search is by words, not meaning.** The app builds its own case index in `data/mate.db` from `wildmed-kb/data/build/scenarios.jsonl`, weighted toward titles, keywords and everyday phrasings (the wildmed index does not include the keywords). It still misses synonyms: "rattler" does not find the snakebite case, "snake" does. Embeddings are the fix.
- **The model's output varies from run to run.** The same hypothermia description produced the "get emergency help now" line in one run and not in the next. The built-in warning does not vary.
- **Keep the benchmark separate.** The synthetic scenarios test the assistant precisely because they are not in the case-study index. Evaluating on the wildmed cases themselves would be circular.

## Landing page and app design

| Address | What | Files |
|---|---|---|
| `/` | Landing page for the product, under the name **Soteria** | `frontend/index.html`, `landing.css`, `landing.js` |
| `/app/` | The app | `frontend/app/` (`index.html`, `style.css`, `app.js`) |

Both are plain HTML, CSS and a small script, and share one design language: charcoal, slate and off-white with forest green, the Instrument Sans typeface (self-hosted in `frontend/assets/fonts/`), and thin rules instead of boxes. The app is dark by default and switches to a light theme when the system asks for one, which is easier to read in full sun. A rust tone is used only for warnings. Photo and font sources are in [`frontend/CREDITS.md`](frontend/CREDITS.md).

The landing page's "Get the App" and "Download" buttons open the app at `/app/`; there is no store listing yet. The phone on that page is a drawn preview, not a screenshot. Two lines of its copy ("grounded in established medical resources", "built around established medical and first-aid resources") describe the goal, not the current data: see **Data** above.

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

`evaluate` scores the full plan given straight from the first report, with no question rounds, and checks it for keywords: did it name the condition, say to get help, and avoid forbidden treatments. "Asked the key question" is checked against the questions the app would ask. It catches obvious misses only, and it does not yet test a whole conversation, so read the stored replies (`eval_runs` table in `data/mate.db`) before drawing conclusions.

## Tests

```
python -m pytest
```
Covers data validation, card and case search with everyday language, red-flag rules, variation generation, and the evaluation checks. No models needed.

## Next steps

- Replace the remaining placeholder cards with content from authoritative guidelines, with sources recorded (the bear cards show the pattern; snakebite from CDC and hypothermia, frostbite and heat from the Joint Trauma System guidelines are next).
- Bear spray: the NPS has a separate page on it; the bear cards do not cover it yet.
- Get permission for, or replace, the all-rights-reserved case studies before sharing the app.
- Clinical review, and only then set `clinical_review` to `approved`.
- Embeddings for case search.
- Multi-turn evaluation: a scripted user answering from the scenario, scored on the final plan.
- Hands-free mode: reopen the microphone after each spoken reply.
- A native iOS or Android shell with the real Argmax SDK for on-device speech.
