"""Deterministic interview policy: pacing and role-keeping live in code, not in the LLM (docs/02, docs/01 section 7)."""
from __future__ import annotations

import re

_META = re.compile(
    r"\b(tell me the answer|give me the answer|what('?s| is) the (right |correct )?answer|just tell me|"
    r"show me the answer|answer (this|it) for me|what should i say|give me a hint|can you hint|any hints?|give me a clue)\b",
    re.IGNORECASE,
)
_SKIP = re.compile(r"^(skip( (this|it|this one|this question))?|pass|next( question)?|(let's |can we )?move on)$", re.IGNORECASE)
_DONT_KNOW = re.compile(
    r"^(sorry[, ]*)?(i (really |honestly |just )?(don't|do not|dont) (really )?know( this( one)?| that| about (this|that)| the answer)?"
    r"|(i have )?no idea|idk|(i'm |i am )?not sure|no clue|i can't (answer|remember|recall)( this| that| it)?)"
    r"([, ]*(sorry|sir|madam|ma'am))*$",
    re.IGNORECASE,
)
_REPEAT = re.compile(
    r"\b(repeat (that|the question|it|yourself)|can you repeat|say (that|it) again|come again|what was the question|"
    r"(didn't|did not|couldn't|could not) (catch|hear) (that|it|the question|you)|one more time)\b"
    r"|^(pardon|sorry|what|huh|excuse me)$",
    re.IGNORECASE,
)
_CLARIFY = re.compile(
    r"\b(what do you mean|what does .{1,40} mean|(could|can) you clarify|please clarify|clarify (that|the question)|"
    r"(could|can) you (explain|rephrase) (the question|what you mean|that)|not sure what you('re| are) asking|"
    r"what are you asking|do you mean|are you asking)\b"
    r"|\b(didn't|did not|don't|do not) understand( the question| that| you| your question| what you (mean|are asking))?$",
    re.IGNORECASE,
)
_THINKING = re.compile(
    r"^(let me (think|see)( about (it|that)| for a (second|sec|moment|minute))?|give me a (second|sec|moment|minute)|hold on|wait|thinking|"
    r"(one|just a) (second|sec|moment|minute)|hmm+)( please)?$",
    re.IGNORECASE,
)
_FILLER = re.compile(r"^(um+|uh+|erm*|ah+|ok|okay|hello|hi|hey|(are )?you there|hello there)$", re.IGNORECASE)
_EXIT = re.compile(
    r"^(please )?(stop|quit|exit)( please)?$"
    r"|\b(stop|end|quit|finish|leave) (the|this|our) (interview|session|practice)( now| here| please)?$"
    r"|\b(can|could|may|shall) we (please )?(stop|end|finish)( here| now| please| for today| this| the interview)?$"
    r"|\bi( want| need| would like|'d like) to (stop|quit|leave)( now| here| please)?$"
    r"|\b(let's|lets) (stop|end|finish)( here| now| please| for today| the interview)?$",
    re.IGNORECASE,
)
_IDENTITY = re.compile(
    r"\b(are|r) you (a |an )?(real|human|person|ai|bot|robot|machine|computer)\b"
    r"|\bam i (talking|speaking) (to|with) (a |an )?(real|human|person|ai|bot|robot|machine)\b"
    r"|\bis this (a |an )?(real|mock|practice|fake|ai)\b",
    re.IGNORECASE,
)
_CRISIS = re.compile(
    r"\b(kill(ing)? myself|suicid\w*|end my life|take my (own )?life|(don't|do not) want to (live|be alive)|hurt(ing)? myself|self[- ]harm)\b",
    re.IGNORECASE,
)
_DISTRESS = re.compile(
    r"\b(i('m| am) (so |really |very |too )?(stressed|anxious|overwhelmed|panicking|terrified|hopeless|worthless|depressed)"
    r"|i feel (so |really |very )?(stressed|anxious|overwhelmed|hopeless|worthless)|giving up|i give up|i can't do this|"
    r"i can't take (this|it)|panic attack|i('m| am) crying|i want to cry)\b",
    re.IGNORECASE,
)
_TYPOGRAPHIC = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_STOP = {
    "a", "an", "the", "of", "or", "and", "to", "in", "is", "are", "for", "on", "with", "as", "by", "at", "it", "its",
    "that", "this", "be", "can", "mentions", "gives", "uses", "explains", "about", "from", "than", "vs", "not", "no",
}

_GENERIC_PROBE_WORDS = {
    "concrete", "example", "examples", "specific", "clear", "real", "practical", "case", "cases", "use", "explain", "describe",
    "why", "what", "how", "when", "which", "who", "would", "you", "your", "did", "does", "has", "have", "had", "should", "could",
    "will", "was", "were", "mentioned", "said", "happens", "happen", "think", "work", "works", "through", "walk", "thing", "things",
    "one", "two", "more", "some", "any", "each", "need", "needs", "want", "wants", "get", "gets", "make", "makes", "same", "tell",
}

MAX_FOLLOWUPS = 2
MAX_DEPTH_FOLLOWUPS = 1
FOLLOWUP_CUTOFF_FRACTION = 0.85
MAX_META_REPLIES = 2
MAX_NON_ANSWERS = 4
META_REPLY = (
    "I can't give answers or hints during the interview. "
    "Take a moment and give it your best attempt, or we can move on."
)


def classify_reply(text: str) -> str:
    """Classify what the candidate wrote (never how they sound; I2). Only 'answer' goes on to an LLM.

    Returns one of: crisis, distress, stop, skip, dont_know, identity, repeat, clarify, thinking, filler, meta, answer.
    Length caps keep a real answer that happens to contain one of these phrases from being misread.
    """
    normal = " ".join(text.translate(_TYPOGRAPHIC).split())
    core = normal.rstrip(" .!?,…")
    words = len(core.split())
    if _CRISIS.search(normal):
        return "crisis"
    if words <= 25 and _DISTRESS.search(normal):
        return "distress"
    if words <= 14 and _EXIT.search(core):
        return "stop"
    if _SKIP.match(core):
        return "skip"
    if _DONT_KNOW.match(core):
        return "dont_know"
    if words <= 16 and _IDENTITY.search(core):
        return "identity"
    if words <= 16 and _REPEAT.search(core):
        return "repeat"
    if words <= 20 and _CLARIFY.search(core):
        return "clarify"
    if _THINKING.match(core):
        return "thinking"
    if _FILLER.match(core):
        return "filler"
    if words <= 25 and _META.search(normal):
        return "meta"
    return "answer"


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 2}


def leaks_signal(follow_up: str, signal_texts: list[str], threshold: float = 0.6) -> bool:
    """True if the follow-up restates an expected signal (i.e. gives the answer away)."""
    fu = _tokens(follow_up)
    for sig in signal_texts:
        st = _tokens(sig)
        if st and len(fu & st) / len(st) >= threshold:
            return True
    return False


def followups_allowed(elapsed_s: float, budget_s: float | None) -> bool:
    """Stop opening new threads near the end of the session."""
    return budget_s is None or elapsed_s < budget_s * FOLLOWUP_CUTOFF_FRACTION


def time_is_up(elapsed_s: float, budget_s: float | None) -> bool:
    return budget_s is not None and elapsed_s >= budget_s


def strip_examples(signal: str) -> str:
    """Drop worked examples and enumerations from a signal, so the interviewer has nothing concrete to repeat. Keeps O(1)-style notation."""
    return re.sub(r"\s*\((?=[^)]*(?:e\.g\.|,|;|:))[^)]*\)", "", signal).strip()


def introduces_signal_terms(follow_up: str, signal_texts: list[str], known_text: str, min_new: int = 2) -> bool:
    """True if a follow-up brings in words from one signal that nothing the candidate, the question or a vetted hint has used."""
    new_words = _tokens(follow_up) - _tokens(known_text) - _GENERIC_PROBE_WORDS
    return any(len(new_words & _tokens(signal)) >= min_new for signal in signal_texts)
