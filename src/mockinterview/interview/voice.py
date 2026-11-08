"""The interviewer's fixed spoken lines.

Everything here comes from code and never depends on a score (I17), so wording cannot reveal how the candidate is doing.
Instructions about the interface belong to the client, not in the interviewer's mouth.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

VOICE_VERSION = "voice/0.5"
PERSONA_NAME = "Alex"  # placeholder persona; see IMPLEMENTATION_PLAN.md section 8
DEFAULT_ROLE_LABEL = "junior software engineer"

OPENING = "Hi, thanks for joining today. I'm {name}, and I'll be interviewing you for the {role} role. We have {n} questions to get through."
WARMUP = "To start us off, tell me a little about yourself."
CANDIDATE_QUESTIONS = "That's all my questions. Before we wrap up, do you have any questions for me?"
GOODBYE = "Thanks for your time today. We'll be in touch soon."
OUTRO_NO_QUESTIONS = f"No problem. {GOODBYE}"
OUTRO_WITH_QUESTION = f"Thanks for asking. The hiring team can give you a better answer on that. {GOODBYE}"
LAST_QUESTION_CUE = "This is my last question."

# Replies to turns that are not answers. No LLM call, and nothing here defines or hints at rubric content.
REPEAT = "Sure. {question}"
CLARIFY = (
    "Sure. I'm looking for your own understanding here, and it's fine if parts of it are new to you. "
    "{question} Give it your best shot, or we can move on."
)
TAKE_YOUR_TIME = "Of course, take your time."
STILL_HERE = "I'm here, take your time. Go ahead whenever you're ready."
SUPPORT = (
    "That's completely okay, there's no rush at all. Take a moment, and when you're ready "
    "you can carry on, we can move on to another question, or we can stop here."
)
# The candidate knows from the app that this is AI practice. The interviewer stays in character but never lies when asked (I18).
IDENTITY = "I'm an AI interviewer, and this is a practice session. {question}"
CRISIS = (
    "I'm really sorry you're feeling this way, and I'm glad you said something. I'm an AI practice tool, so I can't give you "
    "the support you deserve. Please reach out right now to someone you trust, or to a local emergency or crisis service. "
    "I'll end our session here."
)
OUT_OF_TIME = "We're just about out of time, so I'll stop my questions there."
STOPPING = "Of course, we can stop here. Thanks for your time today."
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


@dataclass
class Phrasing:
    """Picks acknowledgements and transitions with no immediate repeat.

    Every field is plain data and each pick comes from the seed and a draw counter, so a saved interview resumes with the same wording.
    """

    seed: int | None = None
    draws: int = 0
    last_ack: str | None = None
    used_transitions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.seed is None:
            self.seed = random.randrange(2**32)

    def _rng(self) -> random.Random:
        self.draws += 1
        return random.Random(f"{self.seed}:{self.draws}")

    def acknowledgement(self) -> str:
        choice = self._rng().choice([a for a in ACKNOWLEDGEMENTS if a != self.last_ack])
        self.last_ack = choice
        return choice

    def no_problem(self) -> str:
        return self._rng().choice(NO_PROBLEM)

    def transition(self, previous_type: str, next_type: str, *, is_last: bool = False) -> str:
        options = TRANSITIONS.get((previous_type, next_type), ("Let's move on.",))
        fresh = [t for t in options if t not in self.used_transitions] or list(options)
        choice = self._rng().choice(fresh)
        self.used_transitions.append(choice)
        return f"{choice} {LAST_QUESTION_CUE}" if is_last else choice
