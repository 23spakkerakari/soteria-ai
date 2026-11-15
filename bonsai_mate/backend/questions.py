"""The question pool: what the app asks to understand the situation.

The app picks the questions, not the model. That makes them predictable, never
repeated, and exactly recordable. The order is "what changes what we tell the
person to do right now" first:

  0. card questions      what the matched card must know before its first step
                         (which kind of bear?); from the card's ask_first list
  1. life threats        awake? breathing? heavy bleeding?
  2. case questions      the questions the matched wildmed case says matter most
  3. circumstances       is it changing, when, what supplies and shelter, can help be reached
  4. basic information   conditions, medication and allergies, pain, age, who is there, food and drink

A question is skipped when the user has already covered its topic in their own words.
"""
import re
from dataclasses import dataclass

from .knowledge import said

CARD, LIFE_THREAT, CASE, CIRCUMSTANCES, BASIC = "card", "life_threat", "case", "circumstances", "basic"
MAX_CASE_QUESTIONS = 4


@dataclass(frozen=True)
class Question:
    id: str
    group: str
    label: str  # short name for the "What I know" panel
    ask: str  # asked about someone else
    ask_self: str | None = None  # asked when the user is the patient (None = same wording)
    topic: str | None = None  # if the user's words match this, they have already told us
    same_as: str | None = None  # a case question matching this would duplicate this one

    def text(self, about_self: bool) -> str:
        return self.ask_self if about_self and self.ask_self else self.ask


POOL: list[Question] = [
    # ---- 1. life threats ----
    Question(
        "responsive", LIFE_THREAT, "Awake and responding",
        "Is the person awake and able to talk to you?",
        topic=r"awake|conscious|unresponsive|respond|won'?t wake|passed out|knocked out|blacked out|talking|alert|collapsed",
        same_as=r"\b(awake|alert|responsive|conscious) (now|right now)\b|\bare (they|you) (awake|alert|conscious)\b",
    ),
    Question(
        "breathing", LIFE_THREAT, "Breathing",
        "Is their breathing normal, or is it fast, noisy or a struggle?",
        "Is your breathing normal, or is it fast, noisy or a struggle?",
        topic=r"breath|gasp|wheez|chok",
        same_as=r"\bare (they|you) breathing\b|\bbreathing (now|normally|ok)\b",
    ),
    Question(
        "bleeding", LIFE_THREAT, "Bleeding",
        "Is there any heavy bleeding?",
        topic=r"bleed|blood",
        same_as=r"\b(any|heavy|much) bleeding\b",
    ),
    # ---- 3. circumstances (case questions are slotted in before these) ----
    Question(
        "changing", CIRCUMSTANCES, "Getting better or worse",
        "Is it getting better, getting worse, or staying the same?",
        topic=r"getting (better|worse)|\bworse\b|worsening|improv|staying the same",
    ),
    Question(
        "when", CIRCUMSTANCES, "When it started",
        "How long ago did this start or happen?",
        topic=r"\b(ago|minutes?|hours?|days?|since|yesterday|last night|this morning|all day|just (now|happened|fell|got|dropped|collapsed))\b",
    ),
    Question(
        "supplies", CIRCUMSTANCES, "Supplies",
        "What do you have with you: a first-aid kit, warm layers, water, food?",
        topic=r"first.?aid kit|sleeping bag|blankets?|supplies|\bkit\b|epipen",
    ),
    Question(
        "shelter", CIRCUMSTANCES, "Weather and shelter",
        "What is the weather like, and do you have any shelter?",
        topic=r"\b(rain|raining|snow|snowing|wind|windy|storm|sunny|sun|hot|cold|freezing|degrees|tent|shelter|cabin|car|hut)\b",
    ),
    Question(
        "help", CIRCUMSTANCES, "Reaching help",
        "Do you have any way to call for help, and how far are you from a road or other people?",
        topic=r"\b(signal|phone|satellite|beacon|radio|inreach|trailhead|no service|no reception)\b|\b(miles?|hours?|minutes?|km|kilometers?) (from|to|away)\b",
    ),
    # ---- 4. basic information ----
    Question(
        "conditions", BASIC, "Medical conditions",
        "Do they have any medical conditions, such as heart problems, diabetes, asthma or epilepsy?",
        "Do you have any medical conditions, such as heart problems, diabetes, asthma or epilepsy?",
        topic=r"diabet|asthma|epilep|heart (condition|problem|disease|attack)|medical (condition|history)|pregnan",
    ),
    Question(
        "medication", BASIC, "Medication and allergies",
        "Do they take any medication, or have any allergies?",
        "Do you take any medication, or have any allergies?",
        topic=r"medic|\bpills?\b|allerg|inhaler|insulin|epipen",
    ),
    Question(
        "pain", BASIC, "Pain",
        "Where does it hurt most, and how bad is it from 1 to 10?",
        topic=r"\b(\d|ten|one|two|three|four|five|six|seven|eight|nine) out of (10|ten)\b|\d\s*/\s*10",
    ),
    Question(
        "age", BASIC, "Age",
        "How old is the person?",
        "How old are you?",
        topic=r"\b\d{1,3}[- ]?(years?[- ]old|year[- ]old|yo)\b|\b(child|kid|toddler|baby|infant|elderly|teenager|teen)\b|\baged? \d",
    ),
    Question(
        "group", BASIC, "Who is with you",
        "How many people are with you who can help?",
        topic=r"\b(alone|by myself|on my own|just (me|us|the two of us)|group of|\d+ of us|nobody else|no one else)\b",
    ),
    Question(
        "intake", BASIC, "Food and drink",
        "When did they last eat and drink?",
        "When did you last eat and drink?",
        topic=r"\b(ate|eaten|eating|drank|drunk|drinking)\b",
    ),
]

_THIRD_PERSON = re.compile(
    r"\b(he|she|they|him|her|his|their|them|friend|buddy|partner|son|daughter|wife|husband|brother|sister|"
    r"dad|father|mom|mother|man|woman|guy|girl|boy|kid|child|someone|somebody|person|hiker|climber|patient)\b",
    re.I,
)
_FIRST_PERSON = re.compile(r"\b(i|i'm|im|i've|my|me)\b", re.I)


def about_self(first_report: str) -> bool:
    """True when the user is describing their own problem ("I cut my leg")."""
    return bool(_FIRST_PERSON.search(first_report)) and not _THIRD_PERSON.search(first_report)


# The case studies ask about their own patient ("did she hit her head?").
_PRONOUNS_OTHER = [
    (r"\b(is|was) (he|she)\b", lambda m: ("are" if m.group(1).lower() == "is" else "were") + " they"),
    (r"\bhas (he|she)\b", "have they"),
    (r"\bdoes (he|she)\b", "do they"),
    (r"\b(he|she) (is|was)\b", lambda m: "they " + ("are" if m.group(2).lower() == "is" else "were")),
    (r"\b(he|she) has\b", "they have"),
    (r"\b(he|she)\b", "they"),
    (r"\bhim\b", "them"),
    (r"\b(his|her)\b", "their"),
]


def neutral(question: str, to_self: bool) -> str:
    """Reword a case question for "they" (or "you" when the user is the patient)."""
    text = question.strip()
    for pattern, replacement in _PRONOUNS_OTHER:
        text = re.sub(pattern, replacement, text, flags=re.I)
    if to_self:
        text = re.sub(r"\bthey\b", "you", text, flags=re.I)
        text = re.sub(r"\bthem\b", "you", text, flags=re.I)
        text = re.sub(r"\btheir\b", "your", text, flags=re.I)
    return text[:1].upper() + text[1:]


def build_pool(cards: list[dict], cases: list[dict], to_self: bool) -> list[Question]:
    """Every question that could be asked for this incident, in priority order."""
    life = [q for q in POOL if q.group == LIFE_THREAT]
    rest = [q for q in POOL if q.group != LIFE_THREAT]
    # A card's ask_first questions. The id comes from the label, so "Kind of bear" asked
    # for the general bear card is not asked again when the answer routes to a specific one.
    from_cards: list[Question] = []
    for card in cards:
        for ask in card.get("ask_first", []):
            qid = "card:" + re.sub(r"[^a-z0-9]+", "_", ask["label"].lower()).strip("_")
            if all(q.id != qid for q in from_cards):
                from_cards.append(Question(qid, CARD, ask["label"], ask["question"], topic=ask.get("topic")))
    from_cases: list[Question] = []
    for case in cases:
        for i, raw in enumerate(case["ask"]):
            text = neutral(raw, to_self)
            if any(q.same_as and re.search(q.same_as, text, re.I) for q in POOL):
                continue  # the general question already covers it
            from_cases.append(Question(f"case:{case['id']}:{i}", CASE, text.rstrip("?"), text))
    return from_cards + life + from_cases[:MAX_CASE_QUESTIONS] + rest


_FILLER = set(
    "a an and any are at be been can could did do does for from had has have he her his how in is it long many much "
    "of on or she so that the their them there they to was were what when where which who will with you your now".split()
)


def _stems(text: str) -> set[str]:
    """Content words, cut to five letters so "slurred" and "slurring" count as the same word."""
    return {w[:5] for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2 and w not in _FILLER}


def already_covered(question: Question, user_text: str, to_self: bool) -> bool:
    if question.id == "responsive" and to_self:
        return True  # they are typing or talking to us
    if question.group == CARD:
        # "Not a grizzly" has not answered "what kind of bear?": the same negation-aware
        # check as the card routes, so a question is never both unanswered and skipped.
        return bool(question.topic) and said(question.topic, user_text)
    if question.group == CASE:
        # "Are they shivering?" is pointless after "she can't stop shivering": skip a case
        # question when the user has already used most of its words.
        asked = _stems(question.ask)
        return bool(asked) and len(asked & _stems(user_text)) / len(asked) >= 0.6
    return bool(question.topic and re.search(question.topic, user_text, re.I))
