import pytest

from backend import assistant, config, db, knowledge, triage, wildmed
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
    assert "Awake and responding" in turn.known["already_told"]  # they are talking to us
    assert turn.asking[0].startswith("Is your breathing")


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
