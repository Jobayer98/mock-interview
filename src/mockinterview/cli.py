"""Text CLI for the mock interview. Dev harness for the brain; the product UI is voice/web (see PRODUCT.md)."""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path

from mockinterview.bank.loader import ROOT, load_questions
from mockinterview.interview.session import InterviewSession, plan_questions
from mockinterview.llm.base import MeteredLLM
from mockinterview.llm.claude_cli import ClaudeCliLLM
from mockinterview.report.scorecard import build_scorecard, render_scorecard
from mockinterview.resume.loaders import ResumeError, load_resume
from mockinterview.resume.profile import extract_profile
from mockinterview.resume.questions import personalize_plan


class ConsoleIO:
    def say(self, text: str) -> None:
        print("\nInterviewer: " + textwrap.fill(text, 88, subsequent_indent="             "), flush=True)

    def listen(self) -> str | None:
        """Read lines until a blank line (submit). Returns None on end of input."""
        lines: list[str] = []
        print("You (blank line to submit):", flush=True)
        while True:
            line = sys.stdin.readline()
            if line == "":
                return "\n".join(lines).strip() or None
            if line.strip() == "" and lines:
                return "\n".join(lines).strip()
            if line.strip():
                lines.append(line.rstrip())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Junior software engineer mock interview (text)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--model", default="claude-sonnet-5-5", help="strong model used to grade each closed question")
    ap.add_argument("--turn-model", default="claude-haiku-5-5", help="fast model that decides and words each interviewer turn")
    ap.add_argument("--turn-timeout", type=float, default=30.0, help="seconds to wait for an interviewer turn before moving on")
    ap.add_argument("--minutes", type=float, default=30.0, help="session time budget")
    ap.add_argument("--technical", type=int, default=3)
    ap.add_argument("--behavioral", type=int, default=2)
    ap.add_argument("--resume", default=None, help="resume to tailor questions to (PDF, .docx or .txt)")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    llm = MeteredLLM(ClaudeCliLLM(model=args.model))
    questions = plan_questions(load_questions(), seed=args.seed, n_behavioral=args.behavioral, n_technical=args.technical)
    if args.resume:
        try:
            resume_text, resume_warnings = load_resume(args.resume)
        except ResumeError as error:
            print(f"Could not use that resume: {error}", file=sys.stderr)
            return 2
        for warning in resume_warnings:
            print(f"Note: {warning}")
        resume_llm = llm.for_purpose("resume")
        profile = extract_profile(resume_llm, resume_text, model=args.model)
        questions, report = personalize_plan(resume_llm, questions, profile, resume_text, model=args.model)
        print(f"Resume read: {len(profile['skills'])} skills, {len(profile['experience'])} roles, {len(profile['projects'])} projects "
              f"({len(profile['dropped_ungrounded_bullets'])} unverifiable lines ignored; email/phone removed before analysis).")
        if profile["injection_attempt"]:
            print("Note: the resume contained text aimed at the AI. It was ignored, and standard questions will be used.")
        for r in report:
            print(f"  - {'tailored' if r['personalized'] else 'standard '} {r.get('question', r['seed_id'])}")
    print("\nHow this works: press Enter on a blank line to send each answer. Type 'skip' to pass on a question, or 'stop' to end the interview.")
    session = InterviewSession(llm, ConsoleIO(), questions, model=args.model, turn_model=args.turn_model,
                               turn_llm=ClaudeCliLLM(model=args.turn_model, timeout_s=args.turn_timeout), time_budget_s=args.minutes * 60)
    started = time.time()
    result = session.run()
    scorecard = build_scorecard(result)
    if result["ended_reason"] != "crisis":  # never follow a message of support with a grade report
        print(render_scorecard(scorecard))
    if not args.no_save:
        out = ROOT / "data" / "sessions" / f"{datetime.fromtimestamp(started):%Y%m%d_%H%M%S}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"session": result, "scorecard": scorecard}, indent=2), encoding="utf-8")
        print(f"Saved session to {Path(out).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
