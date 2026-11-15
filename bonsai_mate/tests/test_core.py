import pytest

from backend import assistant, db, knowledge, triage, wildmed
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


def test_prompt_contains_cards_and_flags(conn):
    prompt, cards, cases, flags = assistant.prepare(conn, [{"role": "user", "content": "A snake bit him and he's not breathing"}])
    assert prompt[0]["role"] == "system" and "Snakebite" in prompt[0]["content"]
    assert flags and flags[0] in prompt[0]["content"]
    if wildmed.available():
        assert cases and "Questions worth asking" in prompt[0]["content"]


@pytest.mark.skipif(not wildmed.available(), reason="wildmed-kb index not built")
@pytest.mark.parametrize(
    "text, condition",
    [
        # Held-out phrasings: not the lay_queries stored in the index.
        ("a snake bit my buddy on the hand while he was gathering firewood", "envenomation snake"),
        ("we were up high and now he has a wet cough and gets winded just sitting", "hape"),
        ("lightning hit close to our tent and my friend was knocked down", "lightning injury"),
    ],
)
def test_similar_cases_match_unseen_phrasing(text, condition):
    cases = wildmed.search(text, k=3)
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
