"""Interview session orchestrator: a state machine driven by code. The LLM only chooses wording and grades."""
from __future__ import annotations

import random
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Protocol

from mockinterview.evaluate.evaluator import EVALUATOR_VERSION, RUBRIC_VERSION, Evaluation, EvaluationError, evaluate_exchange
from mockinterview.llm.base import LLM, MeteredLLM, summarize_calls
from mockinterview.privacy import redact_contact

from . import voice
from .interviewer import INTERVIEWER_VERSION, decide_turn
from .policy import (MAX_DEPTH_FOLLOWUPS, MAX_FOLLOWUPS, MAX_META_REPLIES, MAX_NON_ANSWERS, META_REPLY, classify_reply, followups_allowed,
                     time_is_up)

MEMORY_ITEMS = 4
RECENT_OPENERS = 3
WARMUP_MEMORY_WORDS = 40

NON_ANSWER_LINES = {
    "meta": (META_REPLY, "meta_guard"),
    "repeat": (voice.REPEAT, "clarify"),
    "clarify": (voice.CLARIFY, "clarify"),
    "thinking": (voice.TAKE_YOUR_TIME, "clarify"),
    "filler": (voice.STILL_HERE, "clarify"),
    "distress": (voice.SUPPORT, "support"),
}


class IO(Protocol):
    def say(self, text: str) -> None: ...
    def listen(self) -> str | None: ...


def plan_questions(questions: list[dict], *, seed: int | None = None, n_behavioral: int = 2, n_technical: int = 3) -> list[dict]:
    """Open with one behavioral question that is easy to start on, run the technical block, close with behavioral."""
    rng = random.Random(seed)
    beh = [q for q in questions if q["type"] == "behavioral"]
    tech = [q for q in questions if q["type"] == "technical"]
    rng.shuffle(beh)
    rng.shuffle(tech)
    openers = [q for q in beh if not q.get("avoid_as_opener")] or beh
    opening = openers[:1] if n_behavioral else []
    closing = [q for q in beh if q not in opening][: max(n_behavioral - 1, 0)]
    return opening + tech[:n_technical] + closing


def evaluation_to_dict(ev: Evaluation) -> dict:
    return {
        "score": ev.score, "model_score": ev.model_score, "confidence": ev.confidence, "injection_attempt": ev.injection_attempt,
        "signals": [{"id": s.id, "text": s.text, "hit": s.hit, "quote": s.quote} for s in ev.signals],
        "red_flags_hit": ev.red_flags_hit, "strengths": ev.strengths, "gaps": ev.gaps,
        "ungrounded_quotes": ev.ungrounded_quotes, "rubric_version": ev.rubric_version,
        "evaluator_version": ev.evaluator_version, "model": ev.llm.model,
        "latency_s": ev.llm.latency_s, "cost_usd": ev.llm.cost_usd,
    }


@dataclass
class InterviewSession:
    llm: LLM
    io: IO
    questions: list[dict]
    model: str | None = None
    turn_model: str | None = None
    max_followups: int = MAX_FOLLOWUPS
    warmup: bool = True
    ask_candidate_questions: bool = True
    grade_in_background: bool = True
    phrasing_seed: int | None = None
    turn_llm: LLM | None = None  # optional separate backend, e.g. with a short timeout for the conversation path
    time_budget_s: float | None = None
    clock: Callable[[], float] = time.monotonic
    events: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._phrasing = voice.Phrasing(self.phrasing_seed)
        self._ended_reason: str | None = None
        self._metered = self.llm if isinstance(self.llm, MeteredLLM) else MeteredLLM(self.llm)
        self._turn_llm = MeteredLLM(self.turn_llm, self._metered.log, "turn") if self.turn_llm is not None else self._metered.for_purpose("turn")
        self._started_clock = self.clock()
        self._grader = ThreadPoolExecutor(max_workers=2, thread_name_prefix="grader")
        self._pending_grades: list[tuple[dict, Future]] = []
        self._memory: list[tuple[str, str]] = []  # (question id, verified phrase the candidate said)
        self._recent_openers: list[str] = []

    def _end_for(self, kind: str, question_id: str) -> None:
        """The candidate asked to stop, or said something a practice interview must not carry on past."""
        self._ended_reason = kind
        self._say(voice.CRISIS if kind == "crisis" else voice.STOPPING, kind="support" if kind == "crisis" else "outro", question_id=question_id)

    def _elapsed(self) -> float:
        return self.clock() - self._started_clock

    def _listen(self) -> str | None:
        """Contact details are removed at intake, so they reach neither a model nor the saved session (I4)."""
        reply = self.io.listen()
        return None if reply is None else redact_contact(reply)[0]

    def _classify_and_log(self, reply: str, question_id: str) -> str:
        kind = classify_reply(reply)
        self._log("candidate", text="[not stored]" if kind == "crisis" else reply, question_id=question_id, reply_kind=kind)
        return kind

    def _log(self, type_: str, **data) -> None:
        self.events.append({"t": round(time.time(), 3), "type": type_, **data})

    def _say(self, text: str, **data) -> None:
        self._log("interviewer", text=text, **data)
        self.io.say(text)

    def _acknowledge(self, question_id: str) -> None:
        """Spoken before any slow work, so the candidate is never left in silence (I17: independent of any score)."""
        self._say(self._phrasing.acknowledgement(), kind="ack", question_id=question_id)

    def run(self) -> dict:
        started = time.time()
        first = self.questions[0] if self.questions else {}
        self._say(voice.opening(len(self.questions), first.get("role_label", voice.DEFAULT_ROLE_LABEL)), kind="intro")
        warmup, ended_early = self._run_warmup() if self.warmup else (None, False)
        records: list[dict] = []
        if not ended_early:
            previous_type = "start"
            for index, q in enumerate(self.questions):
                if time_is_up(self._elapsed(), self.time_budget_s):
                    self._ended_reason = "time"
                    self._say(voice.OUT_OF_TIME, kind="ack", question_id=q["id"])
                    break
                is_last = len(self.questions) > 1 and index == len(self.questions) - 1
                rec, ended = self._run_question(q, self._phrasing.transition(previous_type, q["type"], is_last=is_last))
                records.append(rec)
                previous_type = q["type"]
                if ended:
                    ended_early = True
                    break
        if not ended_early:
            self._close_session()
        feedback_wait_s = self._collect_grades()
        return {
            "versions": {"rubric": first.get("rubric_version", RUBRIC_VERSION), "evaluator": EVALUATOR_VERSION, "interviewer": INTERVIEWER_VERSION,
                         "voice": voice.VOICE_VERSION, "model": self.model, "turn_model": self.turn_model},
            "started_at": started, "duration_s": round(time.time() - started, 1), "feedback_wait_s": feedback_wait_s,
            "ended_early": ended_early, "ended_reason": self._ended_reason, "warmup": warmup, "questions": records, "events": self.events,
            "llm_calls": self._metered.log, "cost": summarize_calls(self._metered.log),
        }

    def _run_warmup(self) -> tuple[dict | None, bool]:
        """Ungraded opener: never sent to the grader, and it costs no LLM call."""
        self._say(voice.WARMUP, kind="warmup", question_id="warmup")
        reply = self._listen()
        if reply is None:
            self._ended_reason = "disconnect"
            return None, True
        kind = self._classify_and_log(reply, "warmup")
        if kind in ("crisis", "stop"):
            self._end_for(kind, "warmup")
            return None, True
        if kind == "answer":
            self._memory.append(("warmup", " ".join(reply.split()[:WARMUP_MEMORY_WORDS])))
            self._acknowledge("warmup")
            return {"question": voice.WARMUP, "answer": reply}, False
        self._say(voice.SUPPORT if kind == "distress" else self._phrasing.no_problem(), kind="support" if kind == "distress" else "ack", question_id="warmup")
        return {"question": voice.WARMUP, "answer": None}, False

    def _close_session(self) -> None:
        if not self.ask_candidate_questions:
            self._say(voice.OUTRO_NO_QUESTIONS, kind="outro")
            return
        self._say(voice.CANDIDATE_QUESTIONS, kind="closing")
        reply = self._listen()
        if reply is not None and self._classify_and_log(reply, "closing") == "crisis":
            self._end_for("crisis", "closing")
            return
        asked = reply is not None and not voice.has_no_questions(reply)
        self._say(voice.OUTRO_WITH_QUESTION if asked else voice.OUTRO_NO_QUESTIONS, kind="outro")

    def _run_question(self, q: dict, lead_in: str) -> tuple[dict, bool]:
        rec: dict = {"question_id": q["id"], "type": q["type"], "topic": q["topic"], "question": q["question"],
                     "answers": [], "followups": [], "fallback_followups": 0, "depth_followups": 0, "turn_errors": 0, "skipped": False,
                     "evaluation": None, "error": None}
        self._say(f"{lead_in} {q['question']}", kind="question", question_id=q["id"], transition=lead_in)
        active_question, meta_replies, non_answers, ended = q["question"], 0, 0, False
        while True:
            reply = self._listen()
            if reply is None:
                self._ended_reason, ended = "disconnect", True
                break
            kind = self._classify_and_log(reply, q["id"])
            if kind in ("crisis", "stop"):
                self._end_for(kind, q["id"])
                ended = True
                break
            if kind in ("skip", "dont_know"):
                rec["skipped"] = not rec["answers"]  # declining a follow-up does not undo the answer already given
                self._say(self._phrasing.no_problem(), kind="ack", question_id=q["id"])
                break
            if kind != "answer":
                non_answers += 1
                meta_replies += kind == "meta"
                if non_answers > MAX_NON_ANSWERS or meta_replies > MAX_META_REPLIES:
                    rec["skipped"] = not rec["answers"]
                    self._say(voice.MOVE_ON, kind="ack", question_id=q["id"])
                    break
                line, say_kind = NON_ANSWER_LINES[kind]
                self._say(line.format(question=active_question), kind=say_kind, question_id=q["id"])
                continue
            rec["answers"].append(reply)
            self._acknowledge(q["id"])
            followups_left = self.max_followups - len(rec["followups"])
            if followups_left <= 0 or not followups_allowed(self._elapsed(), self.time_budget_s):
                break
            decision = decide_turn(self._turn_llm, q, rec["answers"], rec["followups"], followups_left=followups_left,
                                   depth_allowed=rec["depth_followups"] < MAX_DEPTH_FOLLOWUPS, model=self.turn_model,
                                   earlier=[quote for question_id, quote in self._memory if question_id != q["id"]],
                                   resume_claim=(q.get("personalized_from") or {}).get("quote"), avoid_openers=self._recent_openers)
            if decision.remember:
                self._memory = [*self._memory, (q["id"], decision.remember)][-MEMORY_ITEMS:]
            if decision.move == "advance":
                rec["turn_errors"] += int(decision.fallback)
                break
            rec["followups"].append(decision.utterance)
            rec["fallback_followups"] += int(decision.fallback)
            rec["depth_followups"] += int(decision.target == "depth")
            active_question = decision.utterance
            self._recent_openers = [*self._recent_openers, " ".join(decision.utterance.split()[:2])][-RECENT_OPENERS:]
            self._say(decision.utterance, kind="follow_up", question_id=q["id"], move=decision.move, target=decision.target,
                      fallback=decision.fallback, rejected=decision.rejected)
        self._grade_on_close(q, rec)
        return rec, ended

    def _grade(self, q: dict, answers: list[str], followups: list[str]) -> Evaluation:
        return evaluate_exchange(self._metered.for_purpose("grade"), q, answers, followups, model=self.model)

    @staticmethod
    def _store_grade(rec: dict, fetch_evaluation: Callable[[], Evaluation]) -> None:
        try:
            rec["evaluation"] = evaluation_to_dict(fetch_evaluation())
        except EvaluationError as exc:
            rec["error"] = str(exc)

    def _grade_on_close(self, q: dict, rec: dict) -> None:
        """Grade once per question, off the conversation path, so the next question is never delayed."""
        if not rec["answers"]:
            return
        answers, followups = list(rec["answers"]), list(rec["followups"])
        if self.grade_in_background:
            self._pending_grades.append((rec, self._grader.submit(self._grade, q, answers, followups)))
        else:
            self._store_grade(rec, lambda: self._grade(q, answers, followups))

    def _collect_grades(self) -> float:
        started = time.time()
        for rec, future in self._pending_grades:
            self._store_grade(rec, future.result)
        self._grader.shutdown()
        return round(time.time() - started, 1)
