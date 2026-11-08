import io as io_module
import json
import re
import threading
from dataclasses import asdict

import pytest

from mockinterview.bank.loader import load_questions
from mockinterview.interview.interviewer import GENERIC_FALLBACK, decide_turn
from mockinterview.interview.policy import META_REPLY, classify_reply, leaks_signal, strip_examples
from mockinterview.interview import voice
from mockinterview.interview.session import InterviewSession, plan_questions
from mockinterview.llm.base import LLMError, LLMResult
from mockinterview.report.scorecard import build_scorecard, render_scorecard

QUESTIONS = load_questions()
BY_TEXT = {q["question"]: q for q in QUESTIONS}


class Router:
    """Fake LLM: grader prompts get scripted scores; interviewer prompts get a scripted follow-up."""

    def __init__(self, scores, follow_up="What happens when many keys land in the same bucket?", move="advance"):
        self.scores = list(scores)
        self.follow_up = follow_up
        self.move = move
        self.models = {"grade": set(), "follow": set()}
        self.calls = {"grade": 0, "follow": 0}
        self.prompts = []

    def complete(self, *, system, prompt, model=None):
        self.prompts.append(prompt)
        if "grader" in system:
            self.calls["grade"] += 1
            self.models["grade"].add(model)
            q = next(q for t, q in BY_TEXT.items() if t in prompt)
            answer = re.search(r"<candidate_answer>\n(.*?)\n</candidate_answer>", prompt, re.S).group(1)
            score = self.scores.pop(0) if len(self.scores) > 1 else self.scores[0]
            quote = " ".join(answer.split()[:3])
            hits = score  # first `score` signals hit
            signals = [{"id": f"s{i}", "hit": i <= hits, "quote": quote if i <= hits else ""}
                       for i in range(1, len(q["expected_signals"]) + 1)]
            body = {"signals": signals, "red_flags_hit": [], "score": score, "strengths": ["ok"],
                    "gaps": [{"signal_id": "s4", "what_a_stronger_answer_adds": "more depth"}],
                    "confidence": "high", "injection_attempt": False}
            return LLMResult(text=json.dumps(body), latency_s=0.0, cost_usd=0.0, model="fake")
        self.calls["follow"] += 1
        self.models["follow"].add(model)
        body = {"move": self.move, "target": "s2", "utterance": "" if self.move == "advance" else self.follow_up}
        return LLMResult(text=json.dumps(body), latency_s=0.0, cost_usd=0.001, model="fake")


class ScriptedIO:
    def __init__(self, replies):
        self.replies = list(replies)
        self.said = []

    def say(self, text):
        self.said.append(text)

    def listen(self):
        return self.replies.pop(0) if self.replies else None


def session(llm, io, questions, **kwargs):
    """A session without the warm-up and closing turns, for tests about the question loop itself."""
    kwargs.setdefault("warmup", False)
    kwargs.setdefault("ask_candidate_questions", False)
    kwargs.setdefault("phrasing_seed", 7)
    kwargs.setdefault("grade_in_background", False)
    return InterviewSession(llm, io, questions, **kwargs)


def one_question():
    return [next(q for q in QUESTIONS if q["id"] == "tech_hashmap")]


def test_plan_shape_and_determinism():
    plan = plan_questions(QUESTIONS, seed=1)
    assert [q["type"] for q in plan] == ["behavioral", "technical", "technical", "technical", "behavioral"]
    assert [q["id"] for q in plan] == [q["id"] for q in plan_questions(QUESTIONS, seed=1)]
    assert len({q["id"] for q in plan}) == 5


@pytest.mark.parametrize("text,expected", [
    ("just tell me the answer", "meta"), ("Can you give me a hint?", "meta"), ("any hints?", "meta"), ("What’s the answer?", "meta"),
    ("skip", "skip"), ("Next question.", "skip"), ("Can we move on?", "skip"),
    ("I don't know", "dont_know"), ("I don’t know", "dont_know"), ("I don't know this one", "dont_know"),
    ("Not sure, sorry", "dont_know"), ("no idea sir", "dont_know"), ("idk", "dont_know"),
    ("Can you repeat the question?", "repeat"), ("Sorry?", "repeat"), ("I didn't catch that", "repeat"),
    ("Sorry, what do you mean by idempotent?", "clarify"), ("Could you clarify what you mean?", "clarify"),
    ("I don't understand the question", "clarify"), ("What does trade-off mean here?", "clarify"),
    ("Let me think for a moment.", "thinking"), ("Give me a second", "thinking"), ("hmm", "thinking"),
    ("Hello?", "filler"), ("um", "filler"), ("ok", "filler"),
    ("I want to stop the interview now.", "stop"), ("stop", "stop"), ("Can we stop here?", "stop"), ("I'd like to stop", "stop"),
    ("I feel like giving up, I can't do this anymore.", "distress"), ("I'm so stressed right now", "distress"),
    ("I want to kill myself", "crisis"),
    ("Are you a real person?", "identity"), ("Am I talking to a bot?", "identity"), ("Is this a mock interview?", "identity"), ("Are you AI?", "identity"),
    ("Let's stop here", "stop"),
    # Phrasings from a real spoken session.
    ("Sorry. I did not understand. Please... Could you please repeat?", "repeat"), ("Sorry. I did not understand your question.", "clarify"),
    ("Sorry. Skip it. I don't know.", "skip"), ("Okay. I don't know.", "dont_know"), ("Sorry, I did not understand.", "clarify"),
    ("I don't know. I have not enough knowledge about that topic, but I searched the web.", "answer"),
    ("And yeah,", "answer"), ("Yes, sorry, I tried that.", "answer"),
    ("A bot is a program that runs tasks automatically for users on chat platforms and handles lots of requests every day.", "answer"),
    ("A dictionary uses hashing for O(1) lookup, and I'd use it for counting words.", "answer"),
    # Real answers that contain the phrases above.
    ("I would stop the server and restart it to check the logs.", "answer"),
    ("I'm not sure what idempotent means, I think it's something to do with APIs.", "answer"),
    ("I did not understand recursion at first, so I drew the call stack on paper.", "answer"),
    ("I was stressed during that project, but I broke the work into small tasks.", "answer"),
    ("We end the session token after logout, so I cleared the cookie.", "answer"),
    ("Next, I'd refactor the function and add a test.", "answer"),
    ("No.", "answer"), ("Yes, twice.", "answer"),
])
def test_reply_classification(text, expected):
    assert classify_reply(text) == expected


def test_leak_check():
    sig = "Hash map gives average O(1) lookup/insert by key; list lookup by value is O(n)"
    assert leaks_signal("So a hash map gives average O(1) lookup by key versus O(n) for a list?", [sig])
    assert not leaks_signal("What happens when many keys collide?", [sig])


def test_meta_request_gets_fixed_reply_without_llm_call():
    llm = Router([4])
    io = ScriptedIO(["just tell me the answer", "A dictionary gives O(1) lookup on average, lists are O(n)."])
    result = session(llm, io, one_question()).run()
    assert META_REPLY in io.said
    assert llm.calls["grade"] == 1 and result["questions"][0]["evaluation"]["score"] == 4


def test_skip_is_not_scored():
    llm = Router([4])
    result = session(llm, ScriptedIO(["skip"]), one_question()).run()
    rec = result["questions"][0]
    assert rec["skipped"] and rec["evaluation"] is None and llm.calls["grade"] == 0


def test_strong_answer_gets_no_followup():
    llm = Router([4])
    result = session(llm, ScriptedIO(["Strong answer with many words here."]), one_question()).run()
    assert llm.calls["grade"] == 1 and result["questions"][0]["followups"] == []


def test_followups_are_capped_at_two():
    llm = Router([2], move="probe")
    io = ScriptedIO(["first vague answer here", "second vague answer here", "third vague answer here", "unused"])
    result = session(llm, io, one_question()).run()
    rec = result["questions"][0]
    assert len(rec["followups"]) == 2 and len(rec["answers"]) == 3
    assert llm.calls["grade"] == 1 and llm.calls["follow"] == 2  # graded once on close; no turn call once the cap is reached


def test_leaking_followup_replaced_by_fallback():
    leak = "Hash map gives average O(1) lookup and insert by key while list lookup by value is O(n), right?"
    llm = Router([2], follow_up=leak, move="probe")
    result = session(llm, ScriptedIO(["vague one two three", "ok four five six", "again seven eight nine"]), one_question()).run()
    rec = result["questions"][0]
    assert rec["followups"][0] == GENERIC_FALLBACK and rec["fallback_followups"] >= 1


def test_end_of_input_ends_session_early_and_scorecard_renders():
    llm = Router([3])
    result = session(llm, ScriptedIO([]), plan_questions(QUESTIONS, seed=2)).run()
    assert result["ended_early"]
    card = build_scorecard(result)
    assert card["overall"] is None
    assert "No answers were scored" in render_scorecard(card)


def test_scorecard_overall_and_focus():
    llm = Router([4, 1])
    qs = [q for q in QUESTIONS if q["id"] in ("tech_hashmap", "tech_unit_tests")]
    io = ScriptedIO(["great answer words here", "weak words here", "weak again here", "weak more here"])
    card = build_scorecard(session(llm, io, qs).run())
    scores = [i["score"] for i in card["questions"]]
    assert scores[0] == 4 and card["overall"] == round(sum(scores) / len(scores), 2)
    assert card["focus_next"] == [card["questions"][1]["topic"]]
    assert "Practice feedback only" in render_scorecard(card)


EVALUATIVE = re.compile(r"(good|great|nice|perfect|correct|right|wrong|excellent|exactly|well done|impressive|awesome)", re.I)


def test_fixed_lines_never_judge_the_candidate():
    lines = list(voice.ACKNOWLEDGEMENTS) + [t for options in voice.TRANSITIONS.values() for t in options]
    assert lines and not [line for line in lines if EVALUATIVE.search(line)]


def test_opening_stays_in_character_with_no_interface_instructions():
    io = ScriptedIO(["I studied CSE.", "A dictionary gives O(1) lookup on average.", "no thanks"])
    InterviewSession(Router([4]), io, one_question(), phrasing_seed=1).run()
    assert "interviewing you for" in io.said[0] and "AI" not in io.said[0]
    assert not [line for line in io.said if re.search(r"press enter|blank line|empty line|type '", line, re.I)]


def test_acknowledgements_do_not_repeat_back_to_back():
    phrasing = voice.Phrasing(seed=3)
    picks = [phrasing.acknowledgement() for _ in range(60)]
    assert all(a != b for a, b in zip(picks, picks[1:]))


def test_phrasing_is_plain_data_and_resumes_with_the_same_wording():
    phrasing = voice.Phrasing(seed=5)
    phrasing.acknowledgement(), phrasing.transition("start", "technical")
    saved = json.loads(json.dumps(asdict(phrasing)))
    resumed = voice.Phrasing(**saved)
    assert [phrasing.acknowledgement() for _ in range(10)] == [resumed.acknowledgement() for _ in range(10)]
    assert phrasing.transition("technical", "technical") == resumed.transition("technical", "technical")


def test_phrasing_without_a_seed_picks_one():
    assert isinstance(voice.Phrasing().seed, int)


def test_warmup_is_ungraded_and_never_reaches_the_grader():
    llm = Router([4])
    io = ScriptedIO(["My background is warmup-secret-phrase.", "A dictionary gives O(1) lookup on average.", "no"])
    result = InterviewSession(llm, io, one_question(), phrasing_seed=1).run()
    assert result["warmup"]["answer"].endswith("warmup-secret-phrase.")
    assert not [p for p in llm.prompts if "warmup-secret-phrase" in p and "PRIVATE CHECKLIST" not in p]
    assert build_scorecard(result)["overall"] == 4


def test_acknowledgement_is_spoken_before_grading():
    io = ScriptedIO(["A dictionary gives O(1) lookup on average."])

    class Spy(Router):
        said_at_first_call = None

        def complete(self, *, system, prompt, model=None):
            Spy.said_at_first_call = Spy.said_at_first_call or io.said[-1]
            return super().complete(system=system, prompt=prompt, model=model)

    session(Spy([4]), io, one_question()).run()
    assert Spy.said_at_first_call in voice.ACKNOWLEDGEMENTS


def test_transition_is_spoken_but_never_graded():
    llm = Router([4])
    io = ScriptedIO(["A dictionary gives O(1) lookup on average."])
    result = session(llm, io, one_question()).run()
    question = one_question()[0]["question"]
    spoken = next(line for line in io.said if question in line)
    assert spoken != question and result["questions"][0]["question"] == question
    lead_in = spoken.replace(question, "").strip()
    assert lead_in and lead_in not in llm.prompts[0]


def test_last_question_is_announced():
    qs = [q for q in QUESTIONS if q["id"] in ("tech_hashmap", "tech_unit_tests")]
    io = ScriptedIO(["first answer words here", "second answer words here"])
    session(Router([4]), io, qs).run()
    assert voice.LAST_QUESTION_CUE in [line for line in io.said if qs[1]["question"] in line][0]


def test_closing_invites_questions_and_replies_honestly():
    for reply, expected in [("no thanks", voice.OUTRO_NO_QUESTIONS), ("What does the team work on?", voice.OUTRO_WITH_QUESTION),
                            ("Nowadays what stack do you use", voice.OUTRO_WITH_QUESTION)]:
        io = ScriptedIO(["A dictionary gives O(1) lookup on average.", reply])
        session(Router([4]), io, one_question(), ask_candidate_questions=True).run()
        assert voice.CANDIDATE_QUESTIONS in io.said and io.said[-1] == expected


def test_a_hard_question_never_opens_the_interview():
    assert {plan_questions(QUESTIONS, seed=s)[0]["id"] for s in range(200)}.isdisjoint({"beh_mistake"})
    assert any("beh_mistake" in [q["id"] for q in plan_questions(QUESTIONS, seed=s)] for s in range(200))


def test_cli_prints_interface_help_and_runs(monkeypatch, capsys):
    from mockinterview import cli

    monkeypatch.setattr(cli, "ClaudeCliLLM", lambda model=None, timeout_s=None: Router([4]))
    typed = "".join(f"answer number {i} here" + chr(10) * 2 for i in range(12))
    monkeypatch.setattr(cli.sys, "stdin", io_module.StringIO(typed))
    assert cli.main(["--seed", "1", "--no-save", "--technical", "1", "--behavioral", "1"]) == 0
    out = capsys.readouterr().out
    assert "press Enter on a blank line" in out and "AI interviewer" in out and "INTERVIEW FEEDBACK" in out


def test_clarification_costs_no_llm_call_and_restates_the_question():
    llm = Router([4])
    io = ScriptedIO(["Sorry, what do you mean by trade-offs?", "A dictionary gives O(1) lookup on average."])
    result = session(llm, io, one_question()).run()
    rec = result["questions"][0]
    assert llm.calls["grade"] == 1 and rec["answers"] == ["A dictionary gives O(1) lookup on average."]
    assert any(line.startswith("Sure. I'm looking for your own understanding") and one_question()[0]["question"] in line for line in io.said)


def test_repeat_restates_the_active_follow_up():
    llm = Router([2], follow_up="Why does a key need to be hashable at all?", move="probe")
    io = ScriptedIO(["first vague answer here", "Can you repeat that?", "skip"])
    session(llm, io, one_question()).run()
    assert "Sure. Why does a key need to be hashable at all?" in io.said


def test_declining_a_follow_up_keeps_the_score_and_is_not_marked_skipped():
    llm = Router([2], move="probe")
    io = ScriptedIO(["A dict is faster for lookups.", "I don't know"])
    result = session(llm, io, one_question()).run()
    card = build_scorecard(result)
    assert card["questions"][0]["skipped"] is False and card["overall"] == 2 and card["skipped"] == 0
    assert "skipped" not in [line for line in render_scorecard(card).splitlines() if "Result:" in line][0]
    assert any(line in voice.NO_PROBLEM for line in io.said)


def test_dont_know_on_the_main_question_is_skipped_kindly():
    llm = Router([4])
    io = ScriptedIO(["I don’t know"])
    result = session(llm, io, one_question()).run()
    assert result["questions"][0]["skipped"] is True and llm.calls["grade"] == 0
    assert any(line in voice.NO_PROBLEM for line in io.said)


def test_stop_ends_the_interview_politely_and_keeps_what_was_covered():
    llm = Router([3, 3])
    qs = [q for q in QUESTIONS if q["id"] in ("tech_hashmap", "tech_unit_tests")]
    llm_follow = {"answer": "A dict gives O(1) lookup, used for counting."}
    io = ScriptedIO([llm_follow["answer"], "I want to stop the interview now."])
    result = session(llm, io, qs, ask_candidate_questions=True).run()
    assert result["ended_early"] and result["ended_reason"] == "stop" and io.said[-1] == voice.STOPPING
    assert result["questions"][0]["evaluation"]["score"] == 3 and voice.CANDIDATE_QUESTIONS not in io.said


def test_distress_gets_support_and_can_continue():
    llm = Router([4])
    io = ScriptedIO(["I'm so stressed right now", "A dictionary gives O(1) lookup on average."])
    result = session(llm, io, one_question()).run()
    assert voice.SUPPORT in io.said and llm.calls["grade"] == 1 and len(result["questions"][0]["answers"]) == 1


def test_crisis_ends_the_session_without_storing_the_text_or_inventing_a_number():
    llm = Router([4])
    io = ScriptedIO(["I want to kill myself"])
    result = session(llm, io, one_question()).run()
    assert io.said[-1] == voice.CRISIS and result["ended_reason"] == "crisis" and llm.calls["grade"] == 0
    assert not re.search(r"\d", voice.CRISIS)
    assert "kill myself" not in json.dumps(result)


def test_non_answers_are_capped_so_the_interview_cannot_stall():
    llm = Router([4])
    io = ScriptedIO(["um", "um", "um", "um", "um", "never reached"])
    result = session(llm, io, one_question()).run()
    assert result["questions"][0]["skipped"] is True and voice.MOVE_ON in io.said and io.replies == ["never reached"]


def test_repeated_answer_requests_still_move_on_after_the_limit():
    llm = Router([4])
    io = ScriptedIO(["give me the answer", "give me the answer", "give me the answer", "never reached"])
    result = session(llm, io, one_question()).run()
    assert result["questions"][0]["skipped"] is True and io.said.count(META_REPLY) == 2 and llm.calls["grade"] == 0


class FailingTurn(Router):
    """The grader works but every interviewer-turn call fails."""

    def complete(self, *, system, prompt, model=None):
        if "grader" not in system:
            raise LLMError("turn call failed")
        return super().complete(system=system, prompt=prompt, model=model)


def test_failed_turn_call_moves_on_and_the_answer_is_still_graded():
    llm = FailingTurn([3])
    result = session(llm, ScriptedIO(["A dict gives O(1) lookup, used for counting."]), one_question()).run()
    rec = result["questions"][0]
    assert rec["evaluation"]["score"] == 3 and rec["followups"] == [] and rec["turn_errors"] == 1
    assert [c for c in result["llm_calls"] if c["purpose"] == "turn"][0]["error"] is True


def test_nonsense_move_is_treated_as_moving_on():
    llm = Router([3], move="dance")
    result = session(llm, ScriptedIO(["A dict gives O(1) lookup, used for counting."]), one_question()).run()
    assert result["questions"][0]["followups"] == [] and result["questions"][0]["turn_errors"] == 1


def test_next_question_is_not_delayed_by_grading():
    second_question_spoken = threading.Event()
    qs = [q for q in QUESTIONS if q["id"] in ("tech_hashmap", "tech_unit_tests")]

    class SlowGrader(Router):
        grading_waited_for_next_question = None

        def complete(self, *, system, prompt, model=None):
            if "grader" in system and SlowGrader.grading_waited_for_next_question is None:
                SlowGrader.grading_waited_for_next_question = second_question_spoken.wait(timeout=5)
            return super().complete(system=system, prompt=prompt, model=model)

    class Watching(ScriptedIO):
        def say(self, text):
            super().say(text)
            if qs[1]["question"] in text:
                second_question_spoken.set()

    llm = SlowGrader([3])
    result = InterviewSession(llm, Watching(["first answer words here", "second answer words here"]), qs,
                              warmup=False, ask_candidate_questions=False, phrasing_seed=1).run()
    assert SlowGrader.grading_waited_for_next_question is True
    assert [q["evaluation"]["score"] for q in result["questions"]] == [3, 3]


def test_grader_and_turn_calls_use_their_own_models():
    llm = Router([3])
    session(llm, ScriptedIO(["A dict gives O(1) lookup, used for counting."]), one_question(), model="strong", turn_model="fast").run()
    assert llm.models == {"grade": {"strong"}, "follow": {"fast"}}


def test_every_llm_call_is_metered_with_a_session_summary():
    llm = Router([2], move="probe")
    io = ScriptedIO(["first vague answer here", "second vague answer here", "third vague answer here"])
    result = session(llm, io, one_question()).run()
    purposes = [c["purpose"] for c in result["llm_calls"]]
    assert purposes.count("turn") == 2 and purposes.count("grade") == 1
    assert result["cost"]["calls"] == 3 and result["cost"]["cost_usd"] == pytest.approx(0.002)
    assert result["cost"]["by_purpose"]["turn"]["calls"] == 2
    assert result["versions"]["interviewer"] and result["versions"]["evaluator"].startswith("evaluator/")


def question(question_id):
    return next(q for q in QUESTIONS if q["id"] == question_id)


class Turn:
    """Fake turn model: returns one scripted decision and keeps the prompt."""

    def __init__(self, **decision):
        self.decision = decision
        self.prompts = []

    def complete(self, *, system, prompt, model=None):
        self.prompts.append(prompt)
        return LLMResult(text=json.dumps(self.decision), latency_s=0.0, model="fake")


ANSWER = "Inheritance is for reusing code and composition is for combining classes."


def test_car_and_engine_leak_from_the_review_is_rejected():
    leak = "You said composition is for combining classes. How would you describe it, say with a car and an engine?"
    decision = decide_turn(Turn(move="probe", target="s1", utterance=leak), question("tech_composition"), [ANSWER], [], followups_left=2)
    assert decision.fallback and decision.utterance == GENERIC_FALLBACK and decision.rejected == "introduces terms from an expected answer"


def test_double_submit_leak_from_the_review_is_rejected():
    leak = "You mentioned POST sends data. What happens if a user ends up double-clicking submit?"
    answer = "GET gets data and POST sends data to the server."
    decision = decide_turn(Turn(move="probe", target="s4", utterance=leak), question("tech_http_methods"), [answer], [], followups_left=2)
    assert decision.rejected == "introduces terms from an expected answer"


def test_a_candidates_own_terms_are_not_a_leak():
    text = "You mentioned a car and an engine. Why would you avoid inheritance there?"
    answer = "Composition is like a car that has an engine."
    assert decide_turn(Turn(move="probe", target="s2", utterance=text), question("tech_composition"), [answer], [], followups_left=2).fallback is False


def test_a_vetted_hint_is_not_a_leak():
    text = "Should a payment endpoint be retried automatically?"
    assert decide_turn(Turn(move="probe", target="s4", utterance=text), question("tech_http_methods"), ["GET reads and POST writes."], [], followups_left=2).fallback is False


def test_behavioral_probes_may_ask_about_outcomes():
    text = "What outcome came out of that, and what did you learn?".replace(", and what did you learn", "")
    assert decide_turn(Turn(move="probe", target="s4", utterance=text), question("beh_stuck_bug"), ["There was a bug once."], [], followups_left=2).fallback is False


def test_turn_prompt_has_hints_and_misconceptions_but_no_worked_examples():
    llm = Turn(move="advance")
    q = question("tech_composition")
    decide_turn(llm, q, [ANSWER], [], followups_left=2)
    assert all(hint in llm.prompts[0] for hint in q["follow_up_hints"]) and q["red_flags"][0] in llm.prompts[0]
    assert "Car has an Engine" not in llm.prompts[0]


def test_strip_examples_keeps_big_o_notation():
    assert strip_examples("Concrete example (e.g. Car has an Engine; avoid deep hierarchies)") == "Concrete example"
    assert strip_examples("Covers normal cases and edge cases (zero, boundary, negative, max)") == "Covers normal cases and edge cases"
    assert strip_examples("Hash map gives average O(1) lookup; list lookup is O(n)") == "Hash map gives average O(1) lookup; list lookup is O(n)"


def test_red_flag_probe_needs_the_candidates_real_words():
    q, answer = question("tech_http_methods"), "POST is more secure because the data is in the body."
    real = Turn(move="probe", target="red_flag", quote="POST is more secure", utterance="Why do you see POST as more secure?")
    assert decide_turn(real, q, [answer], [], followups_left=2).fallback is False
    invented = Turn(move="probe", target="red_flag", quote="GET leaks passwords", utterance="Why do you think GET leaks passwords?")
    assert decide_turn(invented, q, [answer], [], followups_left=2).rejected == "attributes a claim the candidate did not make"


def test_depth_follow_up_is_limited_to_one_per_question():
    llm = Router([4], move="probe", follow_up="What would change at ten times the traffic?")
    original = llm.complete

    def depth_turn(*, system, prompt, model=None):
        result = original(system=system, prompt=prompt, model=model)
        if "grader" in system:
            return result
        return LLMResult(text=result.text.replace('"s2"', '"depth"'), latency_s=0.0, cost_usd=0.001, model="fake")

    llm.complete = depth_turn
    io = ScriptedIO(["Strong answer with many words here.", "Deeper answer with many words here.", "unused"])
    rec = session(llm, io, one_question()).run()["questions"][0]
    assert rec["followups"] == ["What would change at ten times the traffic?"] and rec["depth_followups"] == 1 and io.replies == ["unused"]


def test_the_interviewer_never_breaks_character_unprompted():
    lines = [voice.opening(5), voice.WARMUP, voice.CANDIDATE_QUESTIONS, voice.OUTRO_NO_QUESTIONS, voice.OUTRO_WITH_QUESTION, voice.REPEAT, voice.CLARIFY,
             voice.TAKE_YOUR_TIME, voice.STILL_HERE, voice.SUPPORT, voice.OUT_OF_TIME, voice.STOPPING, voice.MOVE_ON, META_REPLY, *voice.NO_PROBLEM,
             *voice.ACKNOWLEDGEMENTS, *[t for options in voice.TRANSITIONS.values() for t in options]]
    breaking = re.compile(r"\b(mock|practice|simulation|AI|bot|feedback|say skip|say stop)\b")
    assert not [line for line in lines if breaking.search(line)]


def test_the_interviewer_admits_being_an_ai_when_asked():
    llm = Router([4])
    io = ScriptedIO(["Are you a real person?", "A dictionary gives O(1) lookup on average."])
    result = session(llm, io, one_question()).run()
    question = one_question()[0]["question"]
    assert f"I'm an AI interviewer, and this is a practice session. {question}" in io.said
    assert llm.calls["grade"] == 1 and len(result["questions"][0]["answers"]) == 1


def test_being_asked_during_the_warmup_is_answered_and_the_warmup_repeated():
    io = ScriptedIO(["Is this a mock interview?", "I studied computer science.", "skip", "no"])
    InterviewSession(Router([4]), io, one_question(), phrasing_seed=1, grade_in_background=False).run()
    assert f"I'm an AI interviewer, and this is a practice session. {voice.WARMUP}" in io.said


def test_being_asked_at_the_close_is_answered_truthfully():
    io = ScriptedIO(["skip", "Are you a bot?"])
    session(Router([4]), io, one_question(), ask_candidate_questions=True).run()
    assert io.said[-1] == f"I'm an AI interviewer, and this is a practice session. {voice.GOODBYE}"


def test_a_crisis_still_breaks_character_with_the_truth():
    assert "AI" in voice.CRISIS
