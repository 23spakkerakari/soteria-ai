"""The assistant: red-flag check + protocol cards + similar case + question pool + local model.

Each conversation ("incident") runs in three phases:

  triage      first reply: what it could be and the one or two most urgent actions,
              then the app asks its first questions
  assessment  a few rounds: a short reaction to the answers, then the next questions
  plan        the full plan, using everything recorded; later messages are follow-ups

The app chooses and asks the questions (questions.py) and records every question
and answer (incident.py). The record is fed back to the model on every turn. The
model only decides what to say about the answers; the cards and the red-flag rules
decide what is safe to recommend.

Cards that come in families (grizzly / black bear / bear in a tent) are resolved
by rules in the card data (knowledge.resolve), not by the word search, and a card
can ask its own question first ("what kind of bear?") when the answer changes the
first step.
"""
import asyncio
import re
import sqlite3
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from . import config, incident, knowledge, llm, questions, triage, wildmed

SYSTEM_PROMPT = """You are Bonsai Mate, an offline first-aid assistant for people far from medical help. The user may be frightened, hurt, or helping someone else. There is no internet. This is not certified medical advice and the user knows that: your job is to help them act sensibly and stay calm.

Rules:
1. Plain text only: short sentences and short numbered steps, no markdown. Be calm and direct. Your reply may be read aloud.
2. Where your steps come from, in this order. First the PROTOCOL CARDS. If no card covers the situation, the SIMILAR CASE: give the steps the trained responders took, in words an untrained person can follow; those steps are in responder shorthand, so leave out any you are not sure about or that need medical equipment, drugs or training. If there is neither a card nor a case, use your own general first-aid knowledge: give practical, cautious steps that make the person safer and more comfortable.
3. Whatever the source: never recommend anything listed under "Do not" or "Avoid", never give a drug dose, never suggest cutting or any other invasive procedure, and never tell someone not to seek help.
4. Do not ask questions. The app asks the questions after your reply and records the answers. Everything it has learned is under WHAT WE KNOW SO FAR: use all of it, and never contradict it.
5. Compare what the user has told you with "Get help urgently if", "Warning signs" and "When to get help". If any of those signs is present or might be present, do not just repeat the list: write a line starting "Get emergency help now:" that names the sign you noticed and says to call for rescue or evacuate by any means available (phone, satellite messenger, locator beacon, radio, or sending someone).
6. You are not a doctor and cannot diagnose. Say what it could be, not what it is.
7. A similar case is a teaching case about a different patient. Never describe that patient, and never assume the user's situation has details that only the case had.
8. The user's latest message ends with THIS REPLY. Follow it exactly, including its length limit."""

NO_CARDS = "(No protocol card covers this.)"

# Relevance limits (bm25: negative, lower is better; calibrated on the 9-card index).
# The prompt is kept short on purpose: a small machine reads only ~50 tokens a second,
# so the model gets the best card, a second one only if it matches at least half as
# well, and the single best case. A weak match is left out rather than risk steering
# a small model toward the wrong condition.
CARD_MIN_SCORE = -4.0
CARD_RUNNER_UP = 0.5

QUESTIONS_PER_TURN = {"triage": 2, "assessment": 3}
WANTS_PLAN = re.compile(r"give me the plan|just tell me what to do|skip the questions|what (do|should) (i|we) do", re.I)

_FIRST_LINE = 'Line 1: "This could be <condition>."'
_HELP_LINE = 'the "Get emergency help now:" line if a warning sign is present'
INSTRUCTIONS = {
    "triage": f"{_FIRST_LINE} Then, as numbered steps, the two most urgent physical actions to take right now: the first "
    'two steps of the card\'s "Do first" list when there is a card. Then '
    f"{_HELP_LINE}. Stop there: no full plan yet. Under 60 words.",
    "triage_urgent": f"This is life-threatening. {_FIRST_LINE} Then every step from the \"{{card}}\" card, in order, as short "
    'numbered steps, and the "Get emergency help now:" line. Under 110 words.',
    "assessment": "The user has just answered the app's questions. In one or two sentences, say what their answers mean "
    "for what to do. Add a step only if an answer changes what to do right now. Do not repeat steps, or the emergency "
    "help line, that you already gave. Under 45 words.",
    "plan": f"Give the full plan now. {_FIRST_LINE} Then numbered steps in the order to do them. The card's steps come "
    "first: they are what protects the person until help arrives. Fit each step to WHAT WE KNOW SO FAR (their supplies, "
    "shelter, who is there). Then one line on what to watch for. Last, "
    f"{_HELP_LINE}, and make it fit how they said they can or cannot reach help. Under 150 words.",
    "follow_up": "The plan has been given. Answer the user's latest message. Change the plan only where the new "
    "information changes it, and do not repeat the rest. Under 80 words.",
}


_CLAUSE = re.compile(r"[.,;!?]+|\b(?:but|and)\b", re.I)
_NOT_A_SYMPTOM = re.compile(
    r"\b(no|not|none|never|without|isn't|isnt|aren't|arent|wasn't|doesn't|doesnt|don't|dont|didn't|hasn't|haven't|"
    r"fine|normal|normally|ok|okay|good|alright)\b",
    re.I,
)


def search_text(said: list[str]) -> str:
    """What cards and cases are matched on: the first report in full, plus the parts
    of later messages that describe a problem. Later messages are mostly answers to
    the app's questions, and "no bleeding" or "breathing is fine" must not pull in
    the bleeding or breathing material."""
    later = [
        clause
        for message in said[1:]
        for clause in _CLAUSE.split(message)
        if clause.strip() and not _NOT_A_SYMPTOM.search(clause)
    ]
    return " ".join([said[0], *later])


@dataclass
class Turn:
    """Everything decided for one reply before the model is called."""

    incident_id: str
    prompt: list[dict]
    cards: list[dict]
    cases: list[dict]
    flags: list[str]
    grounding: str  # card | case | general: where the steps will come from
    phase: str  # triage | assessment | plan | follow_up
    asking: list[str]  # questions the app asks after this reply
    upcoming: list[str]  # every question still open, in priority order
    known: dict  # the incident record so far

    def meta(self) -> dict:
        return {
            "type": "meta",
            "incident_id": self.incident_id,
            "red_flags": self.flags,
            "reach_help": triage.REACH_HELP if self.flags else None,
            "cards": self.cards,
            "cases": self.cases,
            "grounding": self.grounding,
            "phase": self.phase,
            "asking": self.asking,
            "remaining": len(self.upcoming) - len(self.asking),
            "known": self.known,
        }


def prepare(conn: sqlite3.Connection, messages: list[dict], incident_id: str | None = None, plan_now: bool = False) -> Turn:
    """Record the user's answer, choose the context, the phase and the next questions,
    and build the prompt. `plan_now` skips straight to the plan."""
    incident_id = incident_id or uuid.uuid4().hex
    said = [m["content"] for m in messages if m["role"] == "user"]
    first_report, latest, user_text = said[0], said[-1], " ".join(said)

    state = incident.start_turn(conn, incident_id, first_report)
    turn_no = state["turns"]
    # The new message answers whatever the app asked last time.
    incident.close_pending(conn, incident_id, latest, skipped=plan_now)

    # ---- context ----
    # Red flags are checked on every word the user has said (a false alarm is the safer mistake).
    flags = triage.red_flags(user_text)
    matching = search_text(said)
    # Every card goes through knowledge.resolve: within a family (the bear cards) the
    # user's own words, not the word search, decide which card applies. It reads every
    # word, not `matching`: "no hump" is an answer, and resolve() handles negation itself.
    cards: list[dict] = []

    def add(card: dict | None) -> None:
        if card is not None:
            card = knowledge.resolve(conn, card, user_text)
            if card["card_id"] not in [c["card_id"] for c in cards]:
                cards.append(card)

    for card_id in triage.flagged_cards(user_text):
        add(knowledge.get(conn, card_id))  # cards tied to a red flag always come first
    flagged = list(cards)
    for card in knowledge.search(conn, matching, k=2, runner_up_ratio=CARD_RUNNER_UP, min_score=CARD_MIN_SCORE):
        add(card)
    cards = cards[:2]
    cases = wildmed.search(conn, matching, k=1)
    grounding = "card" if cards else "case" if cases else "general"

    # ---- questions still open, in priority order ----
    to_self = questions.about_self(first_report)
    seen = {r["question_id"] for r in incident.rows(conn, incident_id)}
    case_budget = questions.MAX_CASE_QUESTIONS - sum(q.startswith("case:") for q in seen)
    open_questions: list[questions.Question] = []
    for q in questions.build_pool(cards, cases, to_self):
        if q.id in seen:
            continue
        if q.group == questions.CASE:
            if case_budget <= 0:
                continue
            case_budget -= 1
        if questions.already_covered(q, user_text, to_self):
            incident.add(conn, incident_id, turn_no, q, q.text(to_self), "covered")
        else:
            open_questions.append(q)

    # ---- phase ----
    if plan_now or (turn_no > 1 and not state["plan_given"] and WANTS_PLAN.search(latest)):
        phase = "plan"
    elif state["plan_given"]:
        phase = "follow_up"
    elif turn_no == 1:
        phase = "triage"
    elif open_questions and turn_no - 1 <= config.ASSESSMENT_ROUNDS:
        phase = "assessment"
    else:
        phase = "plan"
    if phase == "plan":
        incident.mark_plan_given(conn, incident_id)

    asking = open_questions[: QUESTIONS_PER_TURN.get(phase, 0)]
    for q in asking:
        incident.add(conn, incident_id, turn_no, q, q.text(to_self), "asked")
    known = incident.record(conn, incident_id)

    # ---- prompt ----
    # Fixed rules first, then cards and case (usually the same from turn to turn), then the
    # conversation, and only then what changes every turn. llama-server re-reads a prompt
    # only from the first token that differs, so this order keeps each turn fast.
    card_text = "\n\n".join(knowledge.format_card(c) for c in cards) or NO_CARDS
    system = f"{SYSTEM_PROMPT}\n\nPROTOCOL CARDS\n\n{card_text}"
    if cases:
        system += "\n\nSIMILAR CASE (teaching case, not clinically reviewed)\n\n" + "\n\n".join(
            wildmed.format_case(c) for c in cases
        )
    if flags:
        system += "\n\nRED FLAGS ALREADY DETECTED (treat as an emergency):\n" + "\n".join(f"- {f}" for f in flags)

    instruction = INSTRUCTIONS[phase]
    if phase == "triage" and flagged:
        instruction = INSTRUCTIONS["triage_urgent"].format(card=flagged[0]["title"])
    if phase in ("triage", "plan") and cards and cards[0]["source"].get("verbatim_allowed"):
        instruction += (
            f' The "{cards[0]["title"]}" card comes from an authoritative source: give its steps in its own words, '
            "changing only who they are addressed to."
        )
    if asking:
        instruction += " The app will ask next, so do not ask it yourself: " + " ".join(q.text(to_self) for q in asking)
    final = f"{latest}\n\n---\nWHAT WE KNOW SO FAR\n{incident.for_model(known)}\n\nTHIS REPLY\n{instruction}"
    prompt = [{"role": "system", "content": system}, *messages[:-1], {"role": "user", "content": final}]

    return Turn(
        incident_id=incident_id,
        prompt=prompt,
        cards=cards,
        cases=cases,
        flags=flags,
        grounding=grounding,
        phase=phase,
        asking=[q.text(to_self) for q in asking],
        upcoming=[q.text(to_self) for q in open_questions],
        known=known,
    )


async def stream_reply(
    conn: sqlite3.Connection,
    messages: list[dict],
    thinking: bool = False,
    incident_id: str | None = None,
    plan_now: bool = False,
) -> AsyncIterator[dict]:
    """Events for the UI. "meta" always comes first, so warnings, cards, the questions
    and the record show even if the model is slow or not running."""
    turn = prepare(conn, messages, incident_id, plan_now)
    yield turn.meta()
    try:
        async for kind, text in llm.stream_chat(turn.prompt, thinking):
            yield {"type": kind, "text": text}
    except llm.LLMUnavailable:
        yield {"type": "offline"}
    except Exception as err:  # keep the stream well-formed so the UI can show the cards
        yield {"type": "error", "message": str(err)}
    yield {"type": "done"}


async def warm_up(attempts: int = 60) -> None:
    """Have the model read the fixed rules once, as soon as it is up, so the first
    real question only has to wait for the cards and the case to be read."""
    for _ in range(attempts):
        try:
            await llm.complete(
                [{"role": "system", "content": f"{SYSTEM_PROMPT}\n\nPROTOCOL CARDS\n\n"}, {"role": "user", "content": "Ready?"}],
                max_tokens=1,
            )
            return
        except Exception:
            await asyncio.sleep(5)  # model server still starting (or not started at all)


async def reply(conn: sqlite3.Connection, messages: list[dict], plan_now: bool = False) -> tuple[str, Turn]:
    """One full reply without streaming. Used by the pipeline."""
    turn = prepare(conn, messages, plan_now=plan_now)
    return await llm.complete(turn.prompt), turn
