"""The assistant: red-flag check + protocol card lookup + local model.

The model only phrases and sequences what the cards say. The cards and the
red-flag rules are the parts that decide what is safe to recommend.
"""
import sqlite3
from collections.abc import AsyncIterator

from . import knowledge, llm, triage, wildmed

SYSTEM_PROMPT = """You are Bonsai Mate, an offline first-aid assistant for people far from medical help. The user may be frightened, hurt, or helping someone else. There is no internet.

Rules:
1. First line: "This could be <condition>." using the title of the card that fits. Then the most important things to do right now as short numbered steps in plain text. No markdown, no long explanations.
2. Base your guidance only on the PROTOCOL CARDS below. If no card fits the situation, say you have no protocol for it, give only basic safety advice (keep the person still, warm and watched), and do not invent treatments, doses or medications.
3. Never recommend anything listed under a card's "Do not".
4. End with one or two short questions that would change what to do. Choose them from "Questions worth asking" in the SIMILAR CASES when one fits, reworded in plain language, and never ask something the user has already told you.
5. Compare what the user told you with the card's "Get help urgently if" list. If any of those signs is present or might be present, do not just repeat the list: write a line starting "Get emergency help now:" that names the sign you noticed and says to call for rescue or evacuate by any means available (phone, satellite messenger, locator beacon, radio, or sending someone).
6. You are not a doctor and cannot diagnose. Say what it could be, not what it is.
7. SIMILAR CASES are teaching cases about other patients, written for trained responders. Use them to recognise what this might be, what to ask and what to watch for. If no protocol card covers the situation, you may give the steps the responders took in a matching case, in words an untrained person can follow. Never describe the case patient, and never assume the user's situation has details that only the case had.
8. Your reply may be read aloud. Keep it under 120 words."""

NO_CARDS = "(No protocol card matched this message.)"


def prepare(conn: sqlite3.Connection, messages: list[dict]) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """Return (messages for the model, matched cards, similar cases, red flags)."""
    user_text = " ".join(m["content"] for m in messages if m["role"] == "user")
    latest = messages[-1]["content"]
    flags = triage.red_flags(user_text)
    # The latest message matters most; fall back to the whole conversation for short follow-ups.
    cards = knowledge.search(conn, latest) or knowledge.search(conn, user_text)
    # Cases are matched on everything said so far: each answer narrows the match.
    cases = wildmed.search(user_text)
    card_text = "\n\n".join(knowledge.format_card(c) for c in cards) or NO_CARDS
    system = f"{SYSTEM_PROMPT}\n\nPROTOCOL CARDS\n\n{card_text}"
    if cases:
        system += "\n\nSIMILAR CASES (reference only, not clinically reviewed)\n\n" + "\n\n".join(
            wildmed.format_case(c) for c in cases
        )
    if flags:
        system += "\n\nRED FLAGS ALREADY DETECTED (treat as an emergency):\n" + "\n".join(f"- {f}" for f in flags)
    return [{"role": "system", "content": system}, *messages], cards, cases, flags


async def stream_reply(conn: sqlite3.Connection, messages: list[dict], thinking: bool = False) -> AsyncIterator[dict]:
    """Events for the UI. "meta" always comes first, so cards and warnings show
    even if the model is slow or not running."""
    prompt, cards, cases, flags = prepare(conn, messages)
    yield {
        "type": "meta",
        "red_flags": flags,
        "reach_help": triage.REACH_HELP if flags else None,
        "cards": cards,
        "cases": cases,
    }
    try:
        async for kind, text in llm.stream_chat(prompt, thinking):
            yield {"type": kind, "text": text}
    except llm.LLMUnavailable:
        yield {"type": "offline"}
    except Exception as err:  # keep the stream well-formed so the UI can show the cards
        yield {"type": "error", "message": str(err)}
    yield {"type": "done"}


async def reply(conn: sqlite3.Connection, messages: list[dict]) -> str:
    prompt, *_ = prepare(conn, messages)
    return await llm.complete(prompt)
