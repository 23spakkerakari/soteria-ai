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
  ["Bear", "There is a bear close to us and it is not leaving."],
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
  const src = card.source || {};
  const sourced = src.license_verified && src.source_url;
  const tag = approved ? "clinically reviewed" : sourced ? "sourced, not clinically reviewed" : "unreviewed draft";
  const node = foldout(card.title, tag, approved ? "approved" : "", [
    ["", "Signs", "ul", card.recognize],
    ["do", "Do first", "ol", card.do_first],
    ["dont", "Do not", "ul", card.do_not],
    ["urgent", "Get help urgently if", "ul", card.escalate_when],
  ], open);
  const body = node.querySelector(".body");
  const applies = card.applies_when || {};
  const scope = [];
  if (applies.species && applies.species.length) scope.push(`Applies to: ${applies.species.join(", ")}`);
  if (applies.situation) scope.push(applies.situation.replace(/_/g, " "));
  if (applies.regions && applies.regions.length) scope.push(applies.regions.join("; "));
  if (scope.length) body.prepend(el("p", "used", scope.join(" · ")));
  const license = src.license === "public_domain" ? " (public domain)" : "";
  body.append(el("p", "used", `Source: ${src.organization || "unknown"}${license}`));
  return node;
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
  if (!mic.active) {
    $("mic").disabled = listen !== "ready";
    $("mic").title = listen === "ready" ? "Tap, speak, tap again to send"
      : listen === "loading" ? "Voice input is loading" : `Voice input unavailable (${listen})`;
  }
  if (listen === "loading" || health.voice.speak === "loading") setTimeout(refreshStatus, 4000);
}

// ---------- reading replies aloud ----------
// Each line is fetched as audio from the server (Argmax TTSKit) as soon as it is
// complete, and played in order. If the server has no TTS, the browser's voice is used.
const speaker = {
  generation: 0,
  chain: Promise.resolve(),
  audio: null,
  get enabled() { return $("speak-toggle").checked; },
  say(text) {
    text = text.trim().replace(/^(\d+)[.)]\s+/, "Step $1. ").replace(/^[-•]\s+/, "");
    if (!this.enabled || !/[a-z0-9]/i.test(text)) return;
    if (health.voice.speak !== "ready") {
      if (window.speechSynthesis) speechSynthesis.speak(new SpeechSynthesisUtterance(text));
      return;
    }
    const generation = this.generation;
    const clip = fetch("/api/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.slice(0, 600) }),
    }).then((res) => (res.ok ? res.blob() : null)).catch(() => null);
    this.chain = this.chain.then(async () => {
      const blob = await clip;
      if (!blob || generation !== this.generation) return;
      const url = URL.createObjectURL(blob);
      this.audio = new Audio(url);
      await new Promise((resolve) => {
        this.audio.onended = this.audio.onerror = this.audio.onpause = resolve;
        this.audio.play().catch(resolve);
      });
      URL.revokeObjectURL(url);
    });
  },
  stop() {
    this.generation++;
    this.audio?.pause();
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
// Microphone audio is streamed to /api/listen (Argmax real-time transcription on the
// server). Text appears in the box while you talk; tapping again sends it.
const WORKLET = `registerProcessor("pcm", class extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0][0]; if (ch) this.port.postMessage(ch.slice()); return true; }
});`;
const mic = { active: false, stream: null, context: null, socket: null, pending: [], pendingLength: 0 };

function micState(label, pressed) {
  $("mic-label").textContent = label;
  $("mic").setAttribute("aria-pressed", String(pressed));
  $("mic").classList.toggle("live", pressed);
}

function flushAudio() {
  if (!mic.pendingLength || mic.socket?.readyState !== WebSocket.OPEN) return;
  const merged = new Float32Array(mic.pendingLength);
  let offset = 0;
  for (const chunk of mic.pending) { merged.set(chunk, offset); offset += chunk.length; }
  mic.socket.send(merged.buffer);
  mic.pending = [];
  mic.pendingLength = 0;
}

function releaseMic() {
  mic.stream?.getTracks().forEach((t) => t.stop());
  mic.context?.close().catch(() => {});
  mic.stream = mic.context = null;
}

async function startListening() {
  speaker.stop();
  if (!$("speak-toggle").checked) { $("speak-toggle").checked = true; $("speak-toggle").dispatchEvent(new Event("change")); }
  try {
    mic.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  } catch (err) {
    $("log").append(el("div", "alert", `Microphone not available: ${err.message}`));
    return;
  }
  mic.active = true;
  micState("Connecting…", true);
  // 16 kHz is what the speech model wants; if the browser refuses, the server resamples.
  try { mic.context = new AudioContext({ sampleRate: 16000 }); } catch { mic.context = new AudioContext(); }
  await mic.context.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" })));
  const node = new AudioWorkletNode(mic.context, "pcm");
  node.port.onmessage = (e) => {
    mic.pending.push(e.data);
    mic.pendingLength += e.data.length;
    if (mic.pendingLength >= mic.context?.sampleRate / 5) flushAudio(); // about 5 messages a second
  };
  mic.context.createMediaStreamSource(mic.stream).connect(node);

  const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/listen`);
  socket.binaryType = "arraybuffer";
  mic.socket = socket;
  socket.onopen = () => {
    socket.send(JSON.stringify({ sample_rate: mic.context.sampleRate }));
    micState("Listening… tap to send", true);
  };
  socket.onmessage = (e) => {
    const update = JSON.parse(e.data);
    if (update.error) {
      $("log").append(el("div", "alert", update.error));
    } else if (update.final != null) {
      $("input").value = "";
      if (update.final) submit(update.final);
      else $("log").append(el("div", "used", "I didn't catch anything. Tap Speak and try again."));
    } else {
      $("input").value = update.preview; // rough live text; the final text replaces it
    }
  };
  socket.onclose = () => {
    releaseMic();
    mic.active = false;
    mic.pending = [];
    mic.pendingLength = 0;
    micState("Speak", false);
    refreshStatus();
  };
}

function stopListening() {
  flushAudio();
  releaseMic();
  micState("Transcribing…", true);
  if (mic.socket?.readyState === WebSocket.OPEN) mic.socket.send(JSON.stringify({ stop: true }));
  else mic.socket?.close();
}

$("mic").addEventListener("click", () => {
  if (!mic.active) startListening();
  else if (mic.stream) stopListening(); // ignore taps while the last words are being transcribed
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
