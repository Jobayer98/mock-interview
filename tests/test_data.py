from mockinterview.bank.loader import (
    load_dataset,
    load_questions,
    validate_dataset,
    validate_questions,
)


def test_question_bank_is_valid():
    assert validate_questions(load_questions()) == []


def test_dataset_is_valid_against_bank():
    assert validate_dataset(load_dataset(), load_questions()) == []


def test_dataset_has_every_score_level():
    scores = {a["score"] for a in load_dataset()}
    assert scores == {1, 2, 3, 4}


def test_session_mix_is_possible():
    types = [q["type"] for q in load_questions()]
    assert types.count("behavioral") >= 2
    assert types.count("technical") >= 3
