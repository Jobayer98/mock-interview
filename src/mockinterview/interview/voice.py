"""The interviewer's fixed spoken lines.

Everything here comes from code and never depends on a score (I17), so wording cannot reveal how the candidate is doing.
Instructions about the interface belong to the client, not in the interviewer's mouth.
"""
from __future__ import annotations

import random
import re

VOICE_VERSION = "voice/0.3"
PERSONA_NAME = "Alex"  # placeholder persona; see IMPLEMENTATION_PLAN.md section 8
DEFAULT_ROLE_LABEL = "junior software engineer"

OPENING = (
    "Hi, I'm {name}, an AI interviewer, and I'll be running your practice interview today for a {role} role. "
    "We'll start with a little about your background, then go through {n} questions, and I may ask a few follow-ups along the way. "
    "Answer as you would in a real interview. I'll save all my feedback for the end, so I won't be able to give hints as we go."
)
WARMUP = "To start us off, tell me a little about yourself."
CANDIDATE_QUESTIONS = "That's all my questions. Before we wrap up, do you have any questions for me?"
OUTRO_NO_QUESTIONS = "No problem. Thanks for your time today. I'll put your feedback together now."
OUTRO_WITH_QUESTION = (
    "Thanks for asking. Since this is a practice session I can't speak for a real team, but this is the point in a real interview "
    "where you'd ask it. Thanks for your time today. I'll put your feedback together now."
)
LAST_QUESTION_CUE = "This is my last question."

# Replies to turns that are not answers. No LLM call, and nothing here defines or hints at rubric content.
REPEAT = "Sure. {question}"
CLARIFY = (
    "Sure. I'm looking for your own understanding here, and it's fine if parts of it are new to you. "
    "{question} Give it your best shot, or say skip and we'll move on."
)
TAKE_YOUR_TIME = "Of course, take your time."
STILL_HERE = "I'm here, take your time. Go ahead whenever you're ready."
SUPPORT = (
    "That's completely okay. This is only practice, and there's no rush at all. Take a moment, and when you're ready "
    "you can carry on, say skip to try another question, or say stop and we'll end here."
)
CRISIS = (
    "I'm really sorry you're feeling this way, and I'm glad you said something. I'm an AI practice tool, so I can't give you "
    "the support you deserve. Please reach out right now to someone you trust, or to a local emergency or crisis service. "
    "I'll end our session here."
)
OUT_OF_TIME = "We're just about out of time, so I'll stop my questions there."
STOPPING = "Of course, we can stop here. Thanks for your time today. I'll put together feedback on what we covered."
MOVE_ON = "Let's leave that one there."
NO_PROBLEM = ("No problem.", "That's fine.", "Okay, no problem.")

ACKNOWLEDGEMENTS = (
    "Okay, thanks.",
    "Got it.",
    "Alright, thank you.",
    "Okay.",
    "Thanks for explaining that.",
    "I see, thanks.",
    "Understood.",
    "Thanks for that.",
)

TRANSITIONS: dict[tuple[str, str], tuple[str, ...]] = {
    ("start", "behavioral"): ("Let's begin with a question about your experience.",),
    ("start", "technical"): ("Let's begin with something technical.",),
    ("behavioral", "technical"): ("Let's switch to something more technical.", "I'd like to move on to a technical topic now."),
    ("technical", "technical"): ("Let's look at a different topic.", "Let me ask about something else.", "Here's another one."),
    ("technical", "behavioral"): ("Let's step away from the technical side for a moment.", "I'd like to hear about your experience now."),
    ("behavioral", "behavioral"): ("Let me ask about a different situation.",),
}

_NO_QUESTIONS = re.compile(r"^(no|nope|none|nothing|not really|i don't|i do not|that's all|that's it)\b", re.IGNORECASE)


def opening(n_questions: int, role: str = DEFAULT_ROLE_LABEL, name: str = PERSONA_NAME) -> str:
    return OPENING.format(name=name, role=role, n=n_questions)


def has_no_questions(reply: str) -> bool:
    cleaned = reply.replace("’", "'").strip()
    return "?" not in cleaned and len(cleaned.split()) <= 8 and bool(_NO_QUESTIONS.match(cleaned))


class Phrasing:
    """Picks acknowledgements and transitions with no immediate repeat. Seeded so sessions and tests are reproducible."""

    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed)
        self._last_ack: str | None = None
        self._used_transitions: list[str] = []

    def acknowledgement(self) -> str:
        choice = self._rng.choice([a for a in ACKNOWLEDGEMENTS if a != self._last_ack])
        self._last_ack = choice
        return choice

    def no_problem(self) -> str:
        return self._rng.choice(NO_PROBLEM)

    def transition(self, previous_type: str, next_type: str, *, is_last: bool = False) -> str:
        options = TRANSITIONS.get((previous_type, next_type), ("Let's move on.",))
        fresh = [t for t in options if t not in self._used_transitions] or list(options)
        choice = self._rng.choice(fresh)
        self._used_transitions.append(choice)
        return f"{choice} {LAST_QUESTION_CUE}" if is_last else choice
