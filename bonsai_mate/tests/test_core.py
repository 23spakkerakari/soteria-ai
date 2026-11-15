import json

import pytest

from backend import assistant, config, db, knowledge, questions, triage, wildmed
from backend.schemas import Scenario
from pipeline.evaluate import check
from pipeline.variations import variations_for


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    conn = db.connect(tmp_path_factory.mktemp("db") / "test.db")
    db.rebuild(conn)  # also validates every card and scenario file
    yield conn
    conn.close()


@pytest.mark.parametrize(
    "text, card_id",
    [
        ("my friend can't stop shaking and she's really cold", "KB_HYPOTHERMIA"),
        ("he's stumbling around in the snow", "KB_HYPOTHERMIA"),
        ("there's blood everywhere from a cut on his leg", "KB_BLEEDING_SEVERE"),
        ("a rattlesnake bit my brother", "KB_SNAKEBITE"),
        ("she got stung and her lips are swelling", "KB_ANAPHYLAXIS"),
        ("he collapsed and won't wake up", "KB_UNRESPONSIVE_CPR"),
        ("I rolled my ankle and it's swollen", "KB_LIMB_INJURY"),
    ],
)
def test_lay_language_finds_the_right_card(conn, text, card_id):
    assert knowledge.search(conn, text)[0]["card_id"] == card_id


def test_every_base_scenario_retrieves_a_card(conn):
    for s in db.all_scenarios(conn):
        assert knowledge.search(conn, s.opening_message()), s.scenario_id


def test_red_flags():
    assert triage.red_flags("he's not breathing")
    assert triage.red_flags("the blood won't stop")
    assert triage.red_flags("she's acting weird and confused")
    assert not triage.red_flags("I have a small blister on my heel")


def say(*texts):
    """A conversation so far: user messages with placeholder assistant replies between them."""
    out = []
    for t in texts:
        out += [{"role": "user", "content": t}, {"role": "assistant", "content": "(reply)"}]
    return out[:-1]


def test_prompt_contains_cards_and_flags(conn):
    turn = assistant.prepare(conn, say("A snake bit him and he's not breathing"))
    system = turn.prompt[0]["content"]
    assert turn.prompt[0]["role"] == "system" and "Snakebite" in system
    assert turn.flags and turn.flags[0] in system
    assert turn.cards[0]["card_id"] == "KB_UNRESPONSIVE_CPR"  # tied to the red flag
    assert "life-threatening" in turn.prompt[-1]["content"]  # urgent first reply: all the steps now
    if config.WILDMED_CASES.exists():
        assert turn.cases and "Questions worth asking" in system


@pytest.mark.parametrize(
    "text, card_id, flagged",
    [
        # The bear cards share nearly all their words: the user's own words, not the word
        # search, must decide which one applies (knowledge.resolve).
        ("a grizzly is attacking my friend", "KB_BEAR_ATTACK_GRIZZLY", True),
        ("a black bear is on top of my brother and biting him", "KB_BEAR_ATTACK_BLACK", True),
        ("a bear came into our tent and is biting my friend", "KB_BEAR_ATTACK_PREDATORY", True),
        ("a black bear got into our tent and is attacking", "KB_BEAR_ATTACK_PREDATORY", True),  # tent beats species
        ("a bear is attacking us", "KB_BEAR_ATTACK", True),
        ("bears are attacking us", "KB_BEAR_ATTACK", True),
        ("a bear is attacking us, it's not a grizzly", "KB_BEAR_ATTACK", True),  # a denial routes nowhere
        ("there is a bear near our camp and it won't leave", "KB_BEAR_ENCOUNTER", False),
        ("a grizzly with cubs is standing up and huffing at us", "KB_BEAR_ENCOUNTER", False),  # no contact yet
        ("a bear is biting into our food bag at camp", "KB_BEAR_ENCOUNTER", False),  # contact with food, not a person
    ],
)
def test_bear_cards_are_chosen_by_what_the_user_said(conn, text, card_id, flagged):
    turn = assistant.prepare(conn, say(text))
    assert [c["card_id"] for c in turn.cards] == [card_id]  # the family collapses to one card
    assert bool(turn.flags) == flagged
    card = turn.cards[0]
    assert card["source"]["license"] == "public_domain" and card["source"]["license_verified"]
    assert "Applies to:" in turn.prompt[0]["content"]


def test_unknown_bear_is_asked_about_first_and_the_answer_routes(conn):
    incident_id = "test-bear-route"
    first = "a bear is attacking us"
    turn = assistant.prepare(conn, say(first), incident_id)
    assert turn.cards[0]["card_id"] == "KB_BEAR_ATTACK"
    assert turn.asking[0].startswith("What kind of bear")  # before the life-threat questions
    assert "in its own words" in turn.prompt[-1]["content"]  # verbatim_allowed: the model keeps the NPS wording
    second = assistant.prepare(conn, say(first, "It has a big hump on its shoulders"), incident_id)
    assert second.cards[0]["card_id"] == "KB_BEAR_ATTACK_GRIZZLY"
    asked = [q for round_ in second.known["rounds"] for q in round_["questions"]]  # includes this turn's
    assert len(asked) == len(set(asked))  # the tent question is not asked twice across the family
    assert second.asking and all(not q.startswith(("What kind of bear", "Did the bear")) for q in second.asking)


@pytest.mark.parametrize(
    "answer, card_id",
    [
        ("It's black with no hump, we were hiking", "KB_BEAR_ATTACK_BLACK"),  # "no hump" is an answer, not a negation
        ("It's not a grizzly, it's a black bear", "KB_BEAR_ATTACK_BLACK"),
        ("I don't think it has a hump", "KB_BEAR_ATTACK"),  # a denial routes nowhere
        ("No idea what kind, it isn't in a tent", "KB_BEAR_ATTACK"),
    ],
)
def test_answers_route_with_negation_understood(conn, answer, card_id):
    first = "a bear is attacking us"
    turn = assistant.prepare(conn, say(first, answer))
    assert turn.cards[0]["card_id"] == card_id
    assert knowledge.said("grizz", "not a grizzly, a black bear") is False
    assert knowledge.said("no hump", "it's black with no hump") is True
    # Known limit: a negation reaches five words, so in unpunctuated speech "we were not in a
    # tent it followed us" does not count as "followed". Wrong in the safe direction (no route).
    assert knowledge.said("follow", "we were not in a tent, it followed us")  # with the clause break it does


def test_a_denial_does_not_count_as_an_answer(conn):
    turn = assistant.prepare(conn, say("a bear is attacking us, it's not a grizzly"))
    assert turn.cards[0]["card_id"] == "KB_BEAR_ATTACK"
    assert "Kind of bear" not in turn.known["already_told"]
    assert turn.asking[0].startswith("What kind of bear")  # still asked: the routes and the record agree


def test_card_references_must_exist(tmp_path, monkeypatch):
    card = db.load_cards()[0].model_dump(mode="json")
    card["routes"] = [{"when": "x", "card": "KB_NOT_A_CARD"}]
    (tmp_path / "cards.json").write_text(json.dumps([card]), encoding="utf-8")
    monkeypatch.setattr(config, "CARDS_DIR", tmp_path)
    with pytest.raises(ValueError, match="KB_NOT_A_CARD"):
        db.load_cards()


def test_nothing_matching_falls_back_to_general_knowledge(conn):
    turn = assistant.prepare(conn, say("what should I pack for a day hike"))
    assert not turn.cards and not turn.cases and not turn.flags
    assert turn.grounding == "general"
    assert assistant.NO_CARDS in turn.prompt[0]["content"]


def test_first_reply_asks_life_threats_first(conn):
    turn = assistant.prepare(conn, say("My friend fell off a ledge and hurt his leg"))
    assert turn.phase == "triage"
    assert turn.asking == ["Is the person awake and able to talk to you?", "Is their breathing normal, or is it fast, noisy or a struggle?"]
    assert turn.meta()["remaining"] == len(turn.upcoming) - 2


def test_questions_already_answered_are_not_asked(conn):
    turn = assistant.prepare(conn, say("My friend is awake but breathing fast and there is blood everywhere from his leg"))
    assert set(turn.known["already_told"]) >= {"Awake and responding", "Breathing", "Bleeding"}
    assert not any("breathing" in q.lower() or "bleeding" in q.lower() for q in turn.upcoming)


def test_self_report_is_asked_about_you(conn):
    turn = assistant.prepare(conn, say("I twisted my ankle and it is swelling"))
    assert "Is anything else wrong: trouble breathing, heavy bleeding, or feeling faint or confused?" in turn.upcoming
    assert not any("the person" in q or "they" in q.split() for q in turn.upcoming[:4])


LIFE_THREAT_QUESTIONS = {
    "Is the person awake and able to talk to you?",
    "Is their breathing normal, or is it fast, noisy or a struggle?",
    "Is your breathing normal, or is it fast, noisy or a struggle?",
    "Is there any heavy bleeding?",
}


@pytest.mark.skipif(not config.WILDMED_CASES.exists(), reason="wildmed-kb not built")
def test_first_questions_are_about_the_problem_not_a_fixed_list(conn):
    openers = ["Someone was bitten by a snake.", "Someone has been burned.", "I twisted my ankle and it is swelling"]
    first = [assistant.prepare(conn, say(o)).asking for o in openers]
    assert len({tuple(q) for q in first}) == len(openers)  # three problems, three different pairs of questions
    assert not any(set(q) & LIFE_THREAT_QUESTIONS for q in first)
    assert first[2][0] == "Can you put weight on it?"


@pytest.mark.parametrize(
    "opener",
    [
        "My friend fell off a ledge and hurt his leg",  # serious mechanism
        "Someone is bleeding heavily from a wound.",  # red flag
        "The bleeding is not stopping",  # red flag, phrased as a negative
    ],
)
def test_life_threat_questions_still_lead_when_it_looks_serious(conn, opener):
    turn = assistant.prepare(conn, say(opener))
    assert turn.asking[0] == "Is the person awake and able to talk to you?"
    assert questions.OTHER_THREATS.ask not in turn.upcoming  # not asked twice in two forms


def test_safety_check_is_not_repeated_after_the_separate_questions(conn):
    incident_id = "test-safety-once"
    first = "My friend fell off a ledge and hurt his leg"
    assistant.prepare(conn, say(first), incident_id)
    later = assistant.prepare(conn, say(first, "He is awake, breathing is okay"), incident_id)
    assert questions.OTHER_THREATS.ask not in later.upcoming


def test_a_bear_nearby_is_not_asked_patient_questions(conn):
    turn = assistant.prepare(conn, say("There is a bear close to us and it is not leaving."))
    assert turn.cards[0]["card_id"] == "KB_BEAR_ENCOUNTER" and not turn.flags
    asked = set(turn.upcoming)
    assert not asked & LIFE_THREAT_QUESTIONS and questions.OTHER_THREATS.ask not in asked
    assert "Is it getting better, getting worse, or staying the same?" not in asked
    assert "What do you have with you: a first-aid kit, warm layers, water, food?" in asked


@pytest.mark.parametrize(
    "text, card_id",
    [
        ("burn", "KB_BURNS"),  # one word cannot reach the bar a description can
        ("Someone has been burned.", "KB_BURNS"),
        ("I burned my hand", "KB_BURNS"),
        ("snakebite", "KB_SNAKEBITE"),
        ("My dad is burning up and talking nonsense. We've been out in the sun all day.", "KB_HEAT_ILLNESS"),
        # Long descriptions of things no card covers: a few stray words must not pick a card.
        ("We climbed to 13,000 feet yesterday and now my partner has a wet cough and gets out of breath just sitting in the tent.", None),
        ("lightning hit close to our tent and my friend was knocked down", None),
        ("I got something in my eye while chopping wood and it really hurts", None),
        ("he has a bad stomach ache and fever since last night", None),
    ],
)
def test_card_matching_depends_on_how_much_was_said(conn, text, card_id):
    cards = knowledge.match(conn, text)
    assert (cards[0]["card_id"] if cards else None) == card_id


def test_what_the_user_ruled_out_is_not_matched(conn):
    opener = "My friend is awake and breathing fine with no bleeding but had a seizure"
    turn = assistant.prepare(conn, say(opener))
    assert "KB_BLEEDING_SEVERE" not in [c["card_id"] for c in turn.cards]
    if config.WILDMED_CASES.exists():
        assert turn.cases and turn.cases[0]["condition"] == "seizure"
        assert turn.asking[0] == "How long did the seizure last?"


def test_a_denial_that_is_the_problem_is_kept():
    kept = assistant.without_denials("The bleeding is not stopping, there is no pulse and he is not breathing")
    assert "not stopping" in kept and "no pulse" in kept and "not breathing" in kept
    removed = assistant.without_denials("A good friend of mine is not swollen, no heavy bleeding, and his breathing looks normal")
    assert "good friend" in removed
    assert not any(word in removed for word in ("swollen", "bleeding", "breathing"))


def test_new_urgent_phrasings_raise_a_warning():
    assert triage.red_flags("Someone is bleeding heavily from a wound.")
    assert triage.red_flags("the bleeding is not stopping")
    assert triage.red_flags("he is burning up and talking nonsense")
    assert not triage.red_flags("No heavy bleeding.")  # an answer to the app's own question


def test_every_question_and_answer_is_recorded_and_fed_back(conn):
    incident_id = "test-record"
    first = assistant.prepare(conn, say("My friend fell off a ledge and hurt his leg"), incident_id)
    answer = "Yes he is awake and talking, breathing seems normal"
    second = assistant.prepare(conn, say("My friend fell off a ledge and hurt his leg", answer), incident_id)

    assert second.phase == "assessment" and len(second.asking) == 3
    assert not set(first.asking) & set(second.asking)  # never asked twice
    answered = second.known["rounds"][0]
    assert answered["questions"] == first.asking and answered["status"] == "answered" and answered["answer"] == answer
    fed_back = second.prompt[-1]["content"]
    assert "WHAT WE KNOW SO FAR" in fed_back and first.asking[0] in fed_back and answer in fed_back
    assert second.prompt[-1]["content"].startswith(answer)  # the user's own words still come first


def test_three_phases_then_follow_up(conn):
    incident_id = "test-phases"
    texts = ["My friend fell off a ledge and hurt his leg"]
    phases, asked = [], []
    for _ in range(config.ASSESSMENT_ROUNDS + 3):
        turn = assistant.prepare(conn, say(*texts), incident_id)
        phases.append(turn.phase)
        asked += turn.asking
        texts.append("ok")
    rounds = config.ASSESSMENT_ROUNDS
    assert phases == ["triage"] + ["assessment"] * rounds + ["plan", "follow_up"]
    assert len(asked) == len(set(asked)) == 2 + 3 * rounds
    assert not any(r["status"] == "asked" for r in turn.known["rounds"])  # nothing left hanging after the plan


def test_reassuring_answers_do_not_change_what_is_matched(conn):
    first = "I got something in my eye while chopping wood and it really hurts"
    incident_id = "test-negation"
    assistant.prepare(conn, say(first), incident_id)
    turn = assistant.prepare(conn, say(first, "My breathing is fine, no bleeding. It happened ten minutes ago."), incident_id)
    assert not turn.cards and not turn.cases and turn.grounding == "general"
    assert not any("tourniquet" in q.lower() or "bleeding" in q.lower() for q in turn.upcoming)
    assert assistant.search_text([first, "No bleeding and her clothes are soaked"]).endswith("her clothes are soaked")


def test_plan_now_skips_the_open_questions(conn):
    incident_id = "test-plan-now"
    assistant.prepare(conn, say("My friend fell off a ledge and hurt his leg"), incident_id)
    turn = assistant.prepare(conn, say("My friend fell off a ledge and hurt his leg", "Give me the plan now."), incident_id, plan_now=True)
    assert turn.phase == "plan" and not turn.asking
    assert turn.known["rounds"][0]["status"] == "skipped"
    assert "did not answer" in turn.prompt[-1]["content"]


def test_case_questions_come_right_after_life_threats(conn):
    if not config.WILDMED_CASES.exists():
        pytest.skip("wildmed-kb not built")
    turn = assistant.prepare(conn, say("My buddy is awake and breathing fine with no bleeding after a seizure in camp, he was shaking all over"))
    assert turn.cases and turn.cases[0]["condition"] == "seizure"
    assert turn.asking[0] == "How long did the seizure last?"
    assert all("she" not in q.lower().split() and "he" not in q.lower().split() for q in turn.upcoming)


@pytest.mark.skipif(not config.WILDMED_CASES.exists(), reason="wildmed-kb not built")
@pytest.mark.parametrize(
    "text, condition",
    [
        # Held-out phrasings: not the lay_queries stored in the index.
        ("a snake bit my buddy on the hand while he was gathering firewood", "envenomation snake"),
        ("we climbed to 13,000 feet and now he has a wet cough and gets winded just sitting", "hape"),
        ("lightning hit close to our tent and my friend was knocked down", "lightning injury"),
    ],
)
def test_similar_cases_match_unseen_phrasing(conn, text, condition):
    cases = wildmed.search(conn, text, k=3)
    assert condition in [c["condition"] for c in cases]
    assert all(c["clinical_review"] == "pending" and c["source"] for c in cases)


def test_variations_are_valid_and_linked(conn):
    parent = next(s for s in db.all_scenarios(conn) if s.variation_of is None)
    variations = variations_for(parent)
    assert len(variations) == len(parent.presentation.lay_descriptions) + 7
    for v in variations:
        Scenario.model_validate(v.model_dump())
        assert v.variation_of == parent.scenario_id
        assert v.evaluation.clinical_review == "pending"


def test_check_handles_warnings_against_forbidden_actions(conn):
    snake = next(s for s in db.all_scenarios(conn) if s.scenario_id == "WILD_0006")
    good = "This is a snakebite. Keep him still. Do not cut the bite or apply a tourniquet. Evacuate to a hospital."
    bad = "This is a snakebite. Apply a tourniquet above the bite, then evacuate to a hospital."
    assert not any(check(snake, good).values())
    assert check(snake, bad)["must_not_recommend"] == ["tourniquet"]
