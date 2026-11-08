"""Scorecard next actions and honest precision (P0-32)."""
import pytest

from mockinterview.report.scorecard import build_scorecard, level_band, render_scorecard, second_person


def question(score, gaps=None, hits=1, injection=False, **overrides):
    signals = [{"id": f"s{i}", "text": f"Signal {i} (e.g. worked example)", "hit": i <= hits, "quote": "some quote" if i <= hits else ""} for i in range(1, 5)]
    evaluation = {"score": score, "confidence": "medium", "signals": signals, "strengths": ["The candidate gave an example"], "gaps": gaps or [], "injection_attempt": injection}
    return {"question_id": "q", "type": "technical", "topic": "testing", "question": "A question?", "answers": ["An answer"], "followups": [],
            "skipped": False, "error": None, "evaluation": evaluation, **overrides}


def card(*questions):
    return build_scorecard({"questions": list(questions), "ended_early": False})


@pytest.mark.parametrize("score", [1, 2])
def test_every_weak_answer_gets_a_next_action(score):
    item = card(question(score))["questions"][0]
    assert item["practice_next"] == "Answer this again and make sure you cover: Signal 2."


def test_strong_answers_get_no_next_action():
    assert card(question(3, hits=3))["questions"][0]["practice_next"] is None


def test_next_action_prefers_the_graders_gap():
    item = card(question(2, gaps=[{"signal_id": "s2", "what_a_stronger_answer_adds": "Name the trade-offs."}]))["questions"][0]
    assert item["practice_next"] == "Name the trade-offs."


def test_next_action_exists_even_with_nothing_to_point_at():
    assert card(question(2, hits=4))["questions"][0]["practice_next"] == "Answer this again out loud and include one concrete example."


def test_rendered_card_shows_the_next_action():
    gap = [{"signal_id": "s2", "what_a_stronger_answer_adds": "Name the trade-offs."}]
    assert "Practice this next: Name the trade-offs." in render_scorecard(card(question(2, gaps=gap)))


@pytest.mark.parametrize("text,expected", [
    ("The candidate should name a trade-off.", "you should name a trade-off."),
    ("Describe the candidate's own work.", "Describe your own work."),
    ("Say what you did on their own.", "Say what you did on your own."),
    ("Hash maps degrade when they collide.", "Hash maps degrade when they collide."),
])
def test_second_person_only_rewrites_unambiguous_phrases(text, expected):
    assert second_person(text) == expected


def test_third_person_coaching_does_not_reach_the_candidate():
    gaps = [{"signal_id": "s2", "what_a_stronger_answer_adds": "Describe their own contribution."}]
    rendered = render_scorecard(card(question(2, gaps=gaps)))
    assert "your own contribution" in rendered and "their own" not in rendered and "The candidate gave" not in rendered


@pytest.mark.parametrize("value,expected", [(1, "Needs work"), (2, "Developing"), (3, "Solid"), (4, "Strong"), (3.4, "Solid to Strong"), (1.5, "Needs work to Developing")])
def test_level_band(value, expected):
    assert level_band(value) == expected


def test_overall_is_a_band_not_two_decimals():
    rendered = render_scorecard(card(question(3, hits=3), question(4, hits=4)))
    assert "Overall level: Solid to Strong (2 answers scored)" in rendered and "3.50" not in rendered


def test_answers_aimed_at_the_grader_are_flagged():
    assert "text aimed at the grader" in render_scorecard(card(question(2, injection=True)))
    assert "text aimed at the grader" not in render_scorecard(card(question(2)))
