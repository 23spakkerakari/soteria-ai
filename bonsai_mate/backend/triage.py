"""Deterministic red-flag check. Runs before the model and independently of
it, so a life-threat warning never depends on what a small LLM decides to say."""
import re

RED_FLAGS: list[tuple[str, str]] = [
    (
        r"not breathing|isn'?t breathing|stopped breathing|no pulse|unresponsive|unconscious"
        r"|won'?t wake|passed out|collapsed|not responding",
        "Unresponsive or not breathing: check breathing now. If they are not breathing normally, start CPR.",
    ),
    (
        r"can'?t breathe|cannot breathe|trouble breathing|hard to breathe|struggling to breathe"
        r"|throat .{0,20}(closing|swelling|tight)|chok(ing|ed)|lips .{0,12}blue",
        "Breathing difficulty: this can become life-threatening in minutes.",
    ),
    (
        r"(blood|bleeding) .{0,30}(won'?t stop|everywhere|spurting|pouring|soaked|a lot)"
        r"|spurting|gushing|won'?t stop bleeding",
        "Severe bleeding: press hard directly on the wound right now and do not let go.",
    ),
    (
        r"seizure|convuls|fitting|shaking uncontrollably and not responding",
        "Seizure: protect their head, do not hold them down, put nothing in their mouth.",
    ),
    (
        r"chest pain|chest (is )?(tight|crushing)|pressure in (my|his|her|their) chest",
        "Chest pain: stop all activity and rest. This may be a heart problem.",
    ),
    (
        r"confus|not making sense|slurr|acting (weird|strange|drunk)|doesn'?t know where"
        r"|stopped shivering",
        "Change in alertness or behaviour: this is a serious sign in cold, heat, altitude or head injury.",
    ),
    (
        r"bone .{0,20}(sticking|through|showing|out)|can'?t feel (my|his|her|their) (leg|legs|arm|arms|feet|hands)"
        r"|neck .{0,20}(hurt|pain|injur)|fell .{0,30}(feet|meters|metres|cliff|ledge)",
        "Possible serious fracture or spinal injury: keep them still.",
    ),
]

REACH_HELP = (
    "If there is any way to reach emergency services (phone, satellite messenger, "
    "personal locator beacon, radio, or sending someone), use it now."
)


def red_flags(text: str) -> list[str]:
    lowered = text.lower()
    return [label for pattern, label in RED_FLAGS if re.search(pattern, lowered)]
