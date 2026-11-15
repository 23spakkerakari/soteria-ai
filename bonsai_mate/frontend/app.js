const $ = (id) => document.getElementById(id);

const QUICK = [
  ["Heavy bleeding", "Someone is bleeding heavily from a wound."],
  ["Not breathing / collapsed", "Someone has collapsed and is unresponsive. I'm not sure they are breathing."],
  ["Very cold", "Someone is very cold, shivering and stumbling."],
  ["Overheated", "Someone is overheated in the sun, dizzy with a headache."],
  ["Hit their head", "Someone took a hard blow to the head."],
  ["Injured arm or leg", "Someone injured their ankle or leg and it is swollen and painful."],
  ["Allergic reaction", "Someone is having an allergic reaction with swelling after a sting."],
  ["Burn", "Someone has been burned."],
  ["Snakebite", "Someone was bitten by a snake."],
];

let messages = [];
let busy = false;
// One id per incident. The server keeps every question and answer under it.
const incidentId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
let shownContext = ""; // which cards and case are already on screen
const shownFlags = new Set(); // warnings already shown (the panel keeps the full list)
let health = { voice: { listen: "loading", speak: "browser" } };

// ---------- small DOM helpers (textContent only: model output is never treated as HTML) ----------
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}
function list(tag, items) {
  const node = el(tag);
  for (const item of items) node.append(el("li", null, item));
  return node;
}
function scrollDown() { $("log").scrollTop = $("log").scrollHeight; }

function foldout(title, tag, tagClass, sections, open = false) {
  const details = el("details", "card");
  details.open = open;
  const summary = el("summary", null, title);
  summary.append(el("span", `tag ${tagClass}`, tag));
  const body = el("div", "body");
  for (const [cls, heading, listTag, items] of sections) {
    if (items && items.length) body.append(el("h4", cls, heading), list(listTag, items));
  }
  details.append(summary, body);
  return details;
}

function renderCard(card, open = false) {
  const approved = card.clinical_review === "approved";
  return foldout(card.title, approved ? "clinically reviewed" : "unreviewed draft", approved ? "approved" : "", [
    ["", "Signs", "ul", card.recognize],
    ["do", "Do first", "ol", card.do_first],
    ["dont", "Do not", "ul", card.do_not],
    ["urgent", "Get help urgently if", "ul", card.escalate_when],
  ], open);
}

function renderCase(c) {
  const node = foldout(`Similar case: ${c.title}`, "unreviewed case study", "", [
    ["", "Questions worth asking", "ul", c.ask],
    ["urgent", "Warning signs", "ul", c.red_flags],
    ["do", "What trained responders did", "ol", c.plan],
    ["dont", "Avoid", "ul", c.avoid],
    ["urgent", "When to get help", "ul", c.escalate ? [c.escalate] : []],
  ]);
  node.querySelector(".body").append(el("p", "used", `Source: ${c.source}. A teaching case about a different patient.`));
  return node;
}

// ---------- "What I know so far" panel ----------
const PHASES = {
  triage: "Step 1 of 3 · Urgent action",
  assessment: "Step 2 of 3 · Questions",
  plan: "Step 3 of 3 · Plan",
  follow_up: "Step 3 of 3 · Plan given",
};
const GROUNDING = {
  card: "",
  case: "From a similar case study. No protocol card covers this.",
  general: "General guidance from the model's own knowledge. No protocol card or case study covers this.",
};

function renderKnown(meta) {
  const known = meta.known;
  const body = $("known-body");
  const section = (title, node) => { if (node) body.append(el("h4", null, title), node); };
  body.replaceChildren(el("p", "phase", PHASES[meta.phase]));
  section("You reported", el("p", null, known.reported));
  const couldBe = [...meta.cards.map((c) => c.title), ...meta.cases.map((c) => `${c.condition} (similar case)`)];
  section("Could be", couldBe.length ? list("ul", couldBe) : el("p", "muted", "Nothing in the protocols or case studies matches."));
  if (meta.red_flags.length) section("Warning signs", list("ul", meta.red_flags));
  if (known.already_told.length) section("You already told me about", list("ul", known.already_told));
  for (const round of known.rounds) {
    const block = el("div", `round ${round.status}`);
    block.append(list("ul", round.questions));
    block.append(el("p", "said", round.status === "answered" ? `You said: “${round.answer}”`
      : round.status === "skipped" ? "Skipped" : "Waiting for your answer"));
    body.append(block);
  }
  if (meta.remaining > 0 && (meta.phase === "triage" || meta.phase === "assessment")) {
    body.append(el("p", "muted", `${meta.remaining} more question${meta.remaining === 1 ? "" : "s"} I may ask.`));
  }
  if ($("known").hidden) {
    $("known").hidden = false;
    $("known-details").open = window.matchMedia("(min-width: 900px)").matches; // closed on a phone
  }
}

// ---------- status ----------
async function refreshStatus() {
  const status = $("status");
  try {
    health = await (await fetch("/api/health")).json();
    const voiceNote = health.voice.listen === "ready" ? "voice ready"
      : health.voice.listen === "loading" ? "voice loading…" : "no voice input";
    if (health.model) {
      status.textContent = `Model ready: ${health.model.split(/[\\/]/).pop()} · ${voiceNote}`;
      status.className = "status ready";
    } else {
      status.textContent = `Model not running: protocol cards only · ${voiceNote}`;
      status.className = "status offline";
    }
  } catch {
    status.textContent = "App server not reachable";
    status.className = "status offline";
  }
  const listen = health.voice.listen;
  if (!recorder.active) {
    $("mic").disabled = listen !== "ready";
    $("mic").title = listen === "ready" ? "Tap to record, tap again to transcribe"
      : listen === "loading" ? "Voice input is loading" : `Voice input unavailable (${listen})`;
  }
  if (listen === "loading") setTimeout(refreshStatus, 4000);
}

// ---------- reading replies aloud ----------
// Each line is fetched as audio from the server (Argmax TTSKit) as soon as it is
// complete, and played in order. If the server has no TTS, the browser's voice is used.
const speaker = {
  get enabled() { return $("speak-toggle").checked; },
  say(text) {
    text = text.trim().replace(/^(\d+)[.)]\s+/, "Step $1. ").replace(/^[-•]\s+/, "");
    if (!this.enabled || !/[a-z0-9]/i.test(text)) return;
    if (window.speechSynthesis) speechSynthesis.speak(new SpeechSynthesisUtterance(text));
  },
  stop() {
    window.speechSynthesis?.cancel();
  },
};
$("speak-toggle").addEventListener("change", (e) => {
  if (!e.target.checked) speaker.stop();
  try { localStorage.setItem("mate.speak", e.target.checked ? "1" : "0"); } catch {}
});
try { $("speak-toggle").checked = localStorage.getItem("mate.speak") === "1"; } catch {}

// ---------- chat ----------
async function send(text, planNow) {
  $("welcome")?.remove();
  speaker.stop();
  messages.push({ role: "user", content: text });
  $("log").append(el("div", "msg user", text));

  const bubble = el("div", "msg assistant");
  const answer = el("div", "answer");
  const thoughts = el("details", "thoughts");
  thoughts.append(el("summary", null, "Thinking…"), el("pre"));
  bubble.append(answer);
  $("log").append(bubble);
  scrollDown();

  let content = "";
  let spokenTo = 0;
  let cards = [];
  let cases = [];
  let meta = null;
  const speakFinishedLines = (all) => {
    const end = all ? content.length : content.lastIndexOf("\n") + 1;
    for (const line of content.slice(spokenTo, end).split("\n")) speaker.say(line);
    spokenTo = Math.max(spokenTo, end);
  };
  const handle = (event) => {
    if (event.type === "meta") {
      meta = event;
      ({ cards, cases } = event);
      renderKnown(event);
      document.querySelectorAll(".plan-now").forEach((b) => b.remove()); // only the newest reply offers it
      if (GROUNDING[event.grounding]) {
        bubble.prepend(el("p", "grounding", GROUNDING[event.grounding]));
        if (event.grounding === "general" && event.phase === "triage") speaker.say("This is general guidance, not from a protocol.");
      }
      const newFlags = event.red_flags.filter((flag) => !shownFlags.has(flag));
      if (newFlags.length) {
        newFlags.forEach((flag) => shownFlags.add(flag));
        const alert = el("div", "alert");
        alert.append(el("strong", null, "Possible emergency"), list("ul", newFlags), el("p", null, event.reach_help));
        bubble.prepend(alert);
        speaker.say(`Warning. ${newFlags[0]}`);
      }
    } else if (event.type === "reasoning") {
      if (!thoughts.isConnected) answer.before(thoughts);
      thoughts.querySelector("pre").textContent += event.text;
    } else if (event.type === "delta") {
      content += event.text;
      answer.textContent = content.trimStart();
      speakFinishedLines(false);
    } else if (event.type === "offline" || event.type === "error") {
      answer.classList.add("note");
      answer.textContent = !cards.length
        ? "The language model is not running and no protocol card matched. Try other words, or open the Protocols tab."
        : event.type === "offline"
          ? "The language model is not running. These are the closest protocol cards:"
          : `The model stopped (${event.message}). These are the closest protocol cards:`;
      speaker.say(answer.textContent);
      cards.forEach((card, i) => bubble.append(renderCard(card, i === 0)));
      cards = [];
      shownContext = "";
    } else if (event.type === "done") {
      speakFinishedLines(true);
      // The app asks the questions itself, so they are never repeated and always recorded.
      if (meta?.asking.length) {
        const ask = el("div", "questions");
        ask.append(el("strong", null, "Please tell me:"), list("ol", meta.asking));
        const planNow = el("button", "plan-now ghost", "Skip the questions: give me the plan now");
        planNow.type = "button";
        planNow.addEventListener("click", () => submit("Give me the plan now.", true));
        ask.append(planNow);
        bubble.append(ask);
        speaker.say("Please tell me.");
        for (const question of meta.asking) speaker.say(question);
      }
      // Show the cards and the case again only when they change.
      const context = [...cards.map((c) => c.card_id), ...cases.map((c) => c.id)].join("|");
      if (context !== shownContext) {
        shownContext = context;
        if (cards.length) {
          // The model rewords the card and can get a step wrong: keep the card's own text in view.
          bubble.append(el("p", "used", "Check the steps against the protocol card:"));
          cards.forEach((card, i) => bubble.append(renderCard(card, i === 0)));
        }
        for (const c of cases) bubble.append(renderCase(c));
      }
    }
    scrollDown();
  };

  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, incident_id: incidentId, plan_now: !!planNow }),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);

  // The server streams one JSON event per line.
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) if (line.trim()) handle(JSON.parse(line));
  }
  // History holds the model's words only. The questions and answers reach the model through
  // the server's record, so they are not duplicated here.
  messages.push({ role: "assistant", content: content.trim() || "(Protocol cards were shown; the model did not answer.)" });
}

async function submit(text, planNow = false) {
  text = text.trim();
  if (!text || busy) return;
  $("input").value = "";
  busy = true;
  $("send").disabled = true;
  try {
    await send(text, planNow);
  } catch (err) {
    if (messages.at(-1)?.role === "user") messages.pop();
    $("log").append(el("div", "alert", `Could not reach the app server: ${err.message}`));
  } finally {
    busy = false;
    $("send").disabled = false;
    refreshStatus();
  }
}

$("form").addEventListener("submit", (e) => { e.preventDefault(); submit($("input").value); });
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("form").requestSubmit(); }
});
$("reset").addEventListener("click", () => location.reload());
for (const [label, text] of QUICK) {
  const button = el("button", null, label);
  button.type = "button";
  button.addEventListener("click", () => submit(text));
  $("quick").append(button);
}

// ---------- voice input ----------
// Push-to-talk, file-based transcription. The browser captures 16 kHz mono PCM
// through an AudioWorklet, encodes it to a WAV Blob on Stop, and POSTs it to
// /api/transcribe (Argmax Pro `tiny`, via a local Swift binary). The returned
// text fills the input and is submitted through the normal chat pipeline.
const WORKLET = `registerProcessor("pcm", class extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0][0]; if (ch) this.port.postMessage(ch.slice()); return true; }
});`;

const recorder = {
  active: false,       // recording or transcribing/generating (voice flow in progress)
  recording: false,    // actually capturing audio right now
  stream: null,
  context: null,
  node: null,
  sampleRate: 16000,
  chunks: [],
  total: 0,
};

function micState(label, recording, disabled) {
  $("mic-label").textContent = label;
  $("mic").setAttribute("aria-pressed", String(recording));
  $("mic").classList.toggle("live", recording);
  if (disabled !== undefined) $("mic").disabled = disabled;
}

function releaseRecorder() {
  recorder.recording = false;
  recorder.node?.disconnect();
  recorder.node = null;
  recorder.stream?.getTracks().forEach((t) => t.stop());
  recorder.stream = null;
  recorder.context?.close().catch(() => {});
  recorder.context = null;
}

function mergeSamples() {
  const merged = new Float32Array(recorder.total);
  let offset = 0;
  for (const chunk of recorder.chunks) { merged.set(chunk, offset); offset += chunk.length; }
  recorder.chunks = [];
  recorder.total = 0;
  return merged;
}

// Encode mono Float32 samples as a 16-bit PCM WAV ArrayBuffer.
function encodeWav(samples, sampleRate) {
  const n = samples.length;
  const buffer = new ArrayBuffer(44 + n * 2);
  const view = new DataView(buffer);
  const str = (offset, s) => { for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i)); };
  str(0, "RIFF");
  view.setUint32(4, 36 + n * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  view.setUint32(16, 16, true);        // fmt chunk size
  view.setUint16(20, 1, true);         // PCM
  view.setUint16(22, 1, true);         // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);         // block align
  view.setUint16(34, 16, true);        // bits per sample
  str(36, "data");
  view.setUint32(40, n * 2, true);
  let offset = 44;
  for (let i = 0; i < n; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

async function startRecording() {
  if (recorder.active) return;
  recorder.active = true;
  speaker.stop();

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch (err) {
    recorder.active = false;
    $("log").append(el("div", "alert", `Microphone not available: ${err.message}`));
    return;
  }
  recorder.stream = stream;
  recorder.recording = true;
  recorder.chunks = [];
  recorder.total = 0;
  micState("Recording… tap to stop", true, false);

  // 16 kHz is what the speech model wants; fall back if the browser refuses.
  try { recorder.context = new AudioContext({ sampleRate: 16000 }); }
  catch { recorder.context = new AudioContext(); }
  recorder.sampleRate = recorder.context.sampleRate;
  try {
    await recorder.context.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" })));
  } catch (err) {
    releaseRecorder();
    recorder.active = false;
    micState("Speak", false);
    $("log").append(el("div", "alert", `Audio capture unavailable: ${err.message}`));
    return;
  }
  const node = new AudioWorkletNode(recorder.context, "pcm");
  node.port.onmessage = (e) => { recorder.chunks.push(e.data); recorder.total += e.data.length; };
  recorder.context.createMediaStreamSource(recorder.stream).connect(node);
  recorder.node = node;
}

function stopRecording() {
  if (!recorder.recording) return;
  const sampleRate = recorder.sampleRate;
  releaseRecorder();
  micState("Transcribing…", false, true);
  const wav = encodeWav(mergeSamples(), sampleRate);
  transcribeToList(wav);
}

// Transcribe the recording and leave the text in the input field for review.
// The user presses Send (or Enter) to submit it through the normal pipeline.
async function transcribeToList(wav) {
  try {
    const res = await fetch("/api/transcribe", {
      method: "POST",
      headers: { "Content-Type": "audio/wav" },
      body: wav,
    });
    if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 300)}`);
    const data = await res.json();
    const text = (data.text || "").trim();
    if (!text) throw new Error("No speech detected. Try again.");
    $("input").value = text;
    $("input").focus();
    $("send").disabled = false;
  } catch (err) {
    $("log").append(el("div", "alert", `Transcription failed: ${err.message}`));
  } finally {
    recorder.active = false;
    micState("Speak", false);
    refreshStatus();
  }
}

$("mic").addEventListener("click", () => {
  if (!recorder.active) startRecording();
  else if (recorder.recording) stopRecording(); // ignore taps while transcribing/generating
});

// ---------- protocols tab ----------
async function loadCards(query = "") {
  const cards = await (await fetch(`/api/cards?q=${encodeURIComponent(query)}`)).json();
  $("card-list").replaceChildren(...cards.map((c) => renderCard(c)));
  if (!cards.length) $("card-list").append(el("p", "used", "No protocol matched. Try simpler words."));
}
function showTab(name) {
  $("view-assist").hidden = name !== "assist";
  $("view-cards").hidden = name !== "cards";
  $("tab-assist").classList.toggle("active", name === "assist");
  $("tab-cards").classList.toggle("active", name === "cards");
  if (name === "cards") loadCards($("card-search").value);
}
$("tab-assist").addEventListener("click", () => showTab("assist"));
$("tab-cards").addEventListener("click", () => showTab("cards"));
let searchTimer;
$("card-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadCards(e.target.value), 150);
});

refreshStatus();
