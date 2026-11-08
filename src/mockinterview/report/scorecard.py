"""Deterministic scorecard from a finished session dict. LLM-written coaching comes later (docs/03 section 5)."""
from __future__ import annotations

import math
import re
from statistics import mean

from mockinterview.interview.policy import strip_examples

DISCLAIMER = "Practice feedback only. This is not a hiring assessment or a prediction of interview outcomes."
LEVEL_LABEL = {1: "Needs work", 2: "Developing", 3: "Solid", 4: "Strong"}
WEAK_SCORE = 2
INJECTION_NOTE = "This answer contained text aimed at the grader. It was ignored."


def second_person(text: str) -> str:
    """Coaching is addressed to the candidate. Only unambiguous phrases are rewritten: a bare "they" may mean a hash map or a team."""
    text = re.sub(r"\bthe candidate's\b", "your", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthe candidate\b", "you", text, flags=re.IGNORECASE)
    return re.sub(r"\btheir own\b", "your own", text, flags=re.IGNORECASE)


def level_band(value: float) -> str:
    """A level name or a range of two, because a two-decimal average would claim more precision than the grader has."""
    low, high = LEVEL_LABEL[math.floor(value)], LEVEL_LABEL[math.ceil(value)]
    return low if low == high else f"{low} to {high}"


def _practice_next(ev: dict | None) -> str | None:
    """One concrete thing to practise for each weak answer."""
    if ev is None or ev["score"] > WEAK_SCORE:
        return None
    for gap in ev["gaps"]:
        if gap.get("what_a_stronger_answer_adds"):
            return second_person(gap["what_a_stronger_answer_adds"])
    missed = [strip_examples(s["text"]) for s in ev["signals"] if not s["hit"]]
    if missed:
        return f"Answer this again and make sure you cover: {missed[0]}."
    return "Answer this again out loud and include one concrete example."


def build_scorecard(session: dict) -> dict:
    items = []
    for q in session["questions"]:
        ev = q.get("evaluation")
        words = sum(len(a.split()) for a in q["answers"])
        item = {
            "question_id": q["question_id"], "type": q["type"], "topic": q["topic"], "question": q["question"],
            "skipped": q["skipped"], "error": q["error"], "answer_words": words, "followups": len(q["followups"]),
            "score": ev["score"] if ev else None, "confidence": ev["confidence"] if ev else None,
            "signals_hit": sum(s["hit"] for s in ev["signals"]) if ev else 0,
            "signals_total": len(ev["signals"]) if ev else 0,
            "strengths": [second_person(s) for s in ev["strengths"]] if ev else [],
            "missed": [s["text"] for s in ev["signals"] if not s["hit"]] if ev else [],
            "gaps": ev["gaps"] if ev else [],
            "evidence": [{"signal": s["text"], "quote": s["quote"]} for s in ev["signals"] if s["hit"]] if ev else [],
            "practice_next": _practice_next(ev), "injection_attempt": bool(ev and ev.get("injection_attempt")),
        }
        items.append(item)
    scored = [i for i in items if i["score"] is not None]
    by_type = {t: round(mean(i["score"] for i in scored if i["type"] == t), 2)
               for t in {i["type"] for i in scored}}
    ordered = sorted(scored, key=lambda i: i["score"])
    return {
        "overall": round(mean(i["score"] for i in scored), 2) if scored else None,
        "by_type": by_type, "questions": items, "focus_next": [i["topic"] for i in ordered[:2] if i["score"] <= WEAK_SCORE],
        "skipped": sum(i["skipped"] for i in items), "ended_early": session["ended_early"],
        "unscored": len(items) - len(scored), "scored": len(scored), "disclaimer": DISCLAIMER,
    }


def render_scorecard(sc: dict) -> str:
    out = ["", "=" * 64, " INTERVIEW FEEDBACK", "=" * 64]
    if sc["overall"] is None:
        out.append("No answers were scored this session.")
    else:
        plural = "answer" if sc["scored"] == 1 else "answers"
        out.append(f"Overall level: {level_band(sc['overall'])} ({sc['scored']} {plural} scored)   "
                   + "   ".join(f"{t}: {level_band(v)}" for t, v in sorted(sc["by_type"].items())))
    if sc["ended_early"]:
        out.append("(Session ended early; only answered questions are scored.)")
    for n, i in enumerate(sc["questions"], 1):
        label = "skipped" if i["skipped"] else ("not scored" if i["score"] is None else f"{i['score']}/4 {LEVEL_LABEL[i['score']]}")
        out += ["", f"{n}. {i['question']}", f"   Result: {label}" + (f"  (confidence: {i['confidence']})" if i["confidence"] else "")]
        for s in i["strengths"]:
            out.append(f"   + {s}")
        for e in i["evidence"][:2]:
            out.append(f'     evidence: "{e["quote"]}"')
        for g in i["gaps"][:3]:
            out.append(f"   - To strengthen: {second_person(g.get('what_a_stronger_answer_adds', ''))}")
        if i["practice_next"]:
            out.append(f"   > Practice this next: {i['practice_next']}")
        if i["injection_attempt"]:
            out.append(f"   ! {INJECTION_NOTE}")
        if i["error"]:
            out.append("   ! Could not score this answer (technical error).")
    if sc["focus_next"]:
        out += ["", "Focus next session: " + ", ".join(sc["focus_next"])]
    out += ["", sc["disclaimer"], ""]
    return "\n".join(out)
