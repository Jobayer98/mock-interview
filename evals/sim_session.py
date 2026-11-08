"""Simulated interview with the real model, to measure cost and turn latency and to read a transcript.

    PYTHONIOENCODING=utf-8 python evals/sim_session.py --seed 1 --out evals/results/sim_<name>.json
    PYTHONIOENCODING=utf-8 python evals/sim_session.py --seed 1 --resume evals/resumes/junior_backend_01.txt

Candidate replies are pre-written from evals/dataset.yaml (weakest answer, then strongest), so they do not respond to the
follow-up that was asked. Use this for cost, latency and wording checks, never as evidence of follow-up relevance.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mockinterview.bank.loader import load_dataset, load_questions  # noqa: E402
from mockinterview.interview import voice  # noqa: E402
from mockinterview.interview.session import InterviewSession, plan_questions  # noqa: E402
from mockinterview.llm.base import MeteredLLM  # noqa: E402
from mockinterview.llm.claude_cli import ClaudeCliLLM  # noqa: E402
from mockinterview.report.scorecard import build_scorecard, render_scorecard  # noqa: E402
from mockinterview.resume.profile import extract_profile  # noqa: E402
from mockinterview.resume.questions import personalize_plan  # noqa: E402

WARMUP_REPLY = "I recently finished a computer science degree and did an internship building backend services in Python. I'm looking for my first full-time engineering role."
CLOSING_REPLY = "No, that's all from me, thank you."


class ScriptedCandidate:
    """Plays pre-written replies. The active queue is chosen from what the interviewer just said."""

    def __init__(self, plan: list[dict], replies_by_question: dict[str, list[str]]):
        self.by_text = {q["question"]: list(replies_by_question[q.get("seed_id", q["id"])]) for q in plan}
        self.queue = [WARMUP_REPLY]

    def say(self, text: str) -> None:
        print(f"\nInterviewer: {text}", flush=True)
        if text == voice.CANDIDATE_QUESTIONS:
            self.queue = [CLOSING_REPLY]
        for question, replies in self.by_text.items():
            if question in text:
                self.queue = replies

    def listen(self) -> str | None:
        reply = self.queue.pop(0) if self.queue else "Not sure, sorry."
        print(f"You: {reply}", flush=True)
        return reply


def replies_from_dataset() -> dict[str, list[str]]:
    honest = [a for a in load_dataset() if "adversarial" not in (a.get("tags") or [])]
    replies: dict[str, list[str]] = {}
    for question_id in {a["question_id"] for a in honest}:
        answers = sorted((a for a in honest if a["question_id"] == question_id), key=lambda a: a["score"])
        replies[question_id] = [" ".join(answers[0]["answer"].split()), " ".join(answers[-1]["answer"].split())]
    return replies


def turn_waits(events: list[dict]) -> list[float]:
    """Seconds from a candidate's answer to the interviewer's next substantive line (the acknowledgement is immediate)."""
    waits = []
    for index, event in enumerate(events):
        if event["type"] != "candidate" or event.get("reply_kind") != "answer" or event["question_id"] in ("warmup", "closing"):
            continue
        following = next((e for e in events[index + 1:] if e["type"] == "interviewer" and e.get("kind") != "ack"), None)
        if following:
            waits.append(round(following["t"] - event["t"], 2))
    return waits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--turn-model", default="claude-haiku-5-5")
    ap.add_argument("--resume", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    llm = MeteredLLM(ClaudeCliLLM(model=args.model))
    plan = plan_questions(load_questions(), seed=args.seed)
    if args.resume:
        text = Path(args.resume).read_text(encoding="utf-8")
        resume_llm = llm.for_purpose("resume")
        plan, _ = personalize_plan(resume_llm, plan, extract_profile(resume_llm, text, model=args.model), text, model=args.model)

    result = InterviewSession(llm, ScriptedCandidate(plan, replies_from_dataset()), plan, model=args.model, turn_model=args.turn_model, phrasing_seed=args.seed).run()
    scorecard = build_scorecard(result)
    print(render_scorecard(scorecard))

    waits = turn_waits(result["events"])
    summary = {"turn_wait_s": {"n": len(waits), "median": median(waits) if waits else None, "min": min(waits, default=None), "max": max(waits, default=None), "all": waits},
               "feedback_wait_s": result["feedback_wait_s"], "duration_s": result["duration_s"], "cost": result["cost"], "with_resume": bool(args.resume)}
    print(json.dumps(summary, indent=2))

    out = Path(args.out) if args.out else ROOT / "evals" / "results" / f"sim_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "session": result, "scorecard": scorecard}, indent=2), encoding="utf-8")
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
