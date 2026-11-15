"""Deterministic red-flag check. Runs before the model and independently of
it, so a life-threat warning never depends on what a small LLM decides to say."""
import re

# (pattern, warning shown to the user, protocol card that must be given to the model)
RED_FLAGS: list[tuple[str, str, str | None]] = [
    (
        r"not breathing|isn'?t breathing|stopped breathing|no pulse|unresponsive|unconscious"
        r"|won'?t wake|passed out|collapsed|not responding|(don'?t|do not) think (he|she|they)( is|'s| are|'re) breathing",
        "Unresponsive or not breathing: check breathing now. If they are not breathing normally, start CPR.",
        "KB_UNRESPONSIVE_CPR",
    ),
    (
        r"can'?t breathe|cannot breathe|trouble breathing|hard to breathe|struggling to breathe"
        r"|throat .{0,20}(closing|swelling|tight)|chok(ing|ed)|lips .{0,12}blue",
        "Breathing difficulty: this can become life-threatening in minutes.",
        None,
    ),
    (
        r"(blood|bleeding) .{0,30}(won'?t stop|everywhere|spurting|pouring|soaked|a lot)"
        r"|spurting|gushing|won'?t stop bleeding|bleeding (heavily|badly|profusely|a lot)|(losing|lost) a lot of blood"
        r"|(blood|bleeding) .{0,30}(not stopping|isn'?t stopping|doesn'?t stop|hasn'?t stopped|can'?t stop)",
        "Severe bleeding: press hard directly on the wound right now and do not let go.",
        "KB_BLEEDING_SEVERE",
    ),
    (
        r"seizure|convuls|fitting|shaking uncontrollably and not responding",
        "Seizure: protect their head, do not hold them down, put nothing in their mouth.",
        None,
    ),
    (
        r"chest pain|chest (is )?(tight|crushing)|pressure in (my|his|her|their) chest",
        "Chest pain: stop all activity and rest. This may be a heart problem.",
        None,
    ),
    (
        r"confus|not making sense|talking nonsense|incoherent|delirious|slurr|acting (weird|strange|drunk)|doesn'?t know where"
        r"|stopped shivering",
        "Change in alertness or behaviour: this is a serious sign in cold, heat, altitude or head injury.",
        None,
    ),
    (
        r"bone .{0,20}(sticking|through|showing|out)|can'?t feel (my|his|her|their) (leg|legs|arm|arms|feet|hands)"
        r"|neck .{0,20}(hurt|pain|injur)|fell .{0,30}(feet|meters|metres|cliff|ledge)",
        "Possible serious fracture or spinal injury: keep them still.",
        "KB_LIMB_INJURY",
    ),
    (
        # Contact with a person. "Biting into our food bag" is an encounter, not an attack.
        r"(bears?|grizzl(y|ies)) .{0,30}(attacking|mauling|(biting|clawing|on top of) "
        r"(him|her|me|us|them|my|his|their|someone|the (man|woman|guy|girl|kid|child|boy|person|hiker|climber)))"
        r"|(being|getting) (attacked|mauled|bitten) by a (bear|grizzly)",
        "Bear attack: keep your pack on and protect your head and neck. What to do next depends on the kind of bear.",
        "KB_BEAR_ATTACK",
    ),
]

REACH_HELP = (
    "If there is any way to reach emergency services (phone, satellite messenger, "
    "personal locator beacon, radio, or sending someone), use it now."
)


def red_flags(text: str) -> list[str]:
    lowered = text.lower()
    return [label for pattern, label, _ in RED_FLAGS if re.search(pattern, lowered)]


def flagged_cards(text: str) -> list[str]:
    """Card ids tied to the red flags in this text. "He's not breathing" must bring
    up the CPR card whether or not word search happens to rank it first."""
    lowered = text.lower()
    return [card for pattern, _, card in RED_FLAGS if card and re.search(pattern, lowered)]
