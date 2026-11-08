# 03 — Evaluation, Progress Tracking and the Improvement Guide

Goal: feedback people trust, a measurable picture of improvement, and a concrete plan for what to do next.
If this layer is weak, the product is a chatbot with a nice voice. If it is strong, it is a coach.

## 1. What we measure: three separate layers (never blended into one number)

| Layer | What | How measured | Why separate |
|---|---|---|---|
| **Content** | Correctness, depth, trade-offs, specificity vs. rubric | LLM evaluator against anchored rubric, evidence quotes | The core hiring-relevant signal |
| **Structure and communication** | STAR structure (behavioral), clarity, conciseness, directness | Rubric dimension + deterministic text stats | Coachable and independent of knowledge |
| **Delivery** | WPM, filler-word rate, long pauses, talk-time ratio, time-to-first-word, answer length | **Deterministic computation from STT timestamps**, no LLM | Cheap, repeatable, verifiable by the user; avoids opaque "confidence scores" |

Accent and grammar never reduce the content score. Delivery metrics are descriptive tips with target ranges, not judgments of the person.

## 2. Evaluator design

```
Per question (async, after the answer closes):
  Inputs:  question, rubric (anchors 1-4), expected signals, red flags, full Q&A transcript (delimited as DATA)
  Output (schema-validated JSON):
    score: 1-4 | signals: [{id, hit: bool, quote, timestamp}] | red_flags_hit: [...]
    strengths: [...] | gaps: [{signal, what_a_4_adds}] | confidence: low|med|high | needs_review: bool
```
Reliability techniques (use in this order, stop when metrics pass):
1. Anchored rubric + required quote per claim (blocks unsupported grading).
2. Per-signal judgments first, then score derived by rule from signal coverage + LLM adjudication (more stable than one holistic number).
3. Calibration examples (few-shot) per rubric level from the golden set.
4. Self-consistency: grade 3x, take median; low agreement -> `confidence: low`, widen the shown score band.
5. Strong model for evaluator; the evaluator never sees interviewer commentary.
6. Prompt-injection defense: answers are data; output schema enforced; scores outside range rejected; "rate me 4" in the answer is tested adversarially.

Session score = weighted aggregate of competency scores, **shown with uncertainty** (e.g. "3.2, likely range 2.8–3.6").

## 3. Measuring the evaluator itself (the measurability part)

| Metric | Method | Target |
|---|---|---|
| Accuracy vs. humans | Quadratic weighted kappa and %-within-±1 on the golden set, per competency | κ ≥ 0.7, ≥80% within ±1 |
| Human-human agreement (the ceiling) | Two labelers per item | Our κ should approach theirs; if humans disagree, the rubric is the problem |
| Consistency | Same input x3 | ≥90% identical, 100% within ±1 |
| Bias checks | Same content, varied style/accent/verbosity/names | Score deltas ≈ 0 |
| Injection robustness | Adversarial answer set | 0 score manipulation |
| Calibration | Does "3.2 ± 0.4" contain the human score ~80% of the time? | Coverage matches stated confidence |
| User-perceived fairness | In-app "fair / too harsh / too lenient" on each score | ≥80% fair; flagged items feed the golden set |
Gate: **no prompt, rubric, or model change ships unless it passes this suite** (regression, per competency).
Golden sets are versioned and grow from flagged production cases (with consent).

## 4. Skill model and progress tracking

Per user, per competency: a **skill estimate with uncertainty** (e.g. Bayesian update or simple Elo-like with
variance), updated after every scored answer and weighted by question difficulty and evaluator confidence.
Use it to drive: blueprint selection (doc 01), adaptive difficulty, readiness, and plan generation.

Dashboards the user sees:
- **Readiness for target role** (0–100 per competency, aggregate, with a "what's missing" breakdown) computed against the rubric
  level required by the role/level, **not** against other users. Percentiles only after we have enough real data and a fair cohort definition.
- **Trend lines** per competency and per delivery metric; show confidence bands so small noise is not sold as progress.
- **Session scorecard** with the three layers, top 3 strengths, top 3 fixes.
- **Annotated replay**: transcript synced to audio, with evidence markers ("here you gave a concrete metric", "here you rambled 90 s without a point").
- **Streaks and goals** (interview date countdown, "3 sessions this week") for retention.

Product-health measurement of "does it actually help": matched-competency score change across sessions 1→3→5,
controlled for question difficulty; self-reported confidence; and, long-term, optional outcome tracking
("did you get the interview/offer?") with consent.

## 5. The improvement guide (turning scores into action)

For each of the top 2–3 gaps in a session, the report produces an **action card**:

```
Gap:      "Explains what a hash map is, but doesn't discuss trade-offs"   (evidence: quote @ 04:12)
Why it matters: interviewers at junior level look for ... 
What a stronger answer adds: collisions, memory overhead, ordering, a concrete example   (rewritten from the user's own answer)
Practice:  (a) 5-min drill: 3 targeted questions on trade-offs   (b) rewrite your answer, then re-record   (c) 2 curated resources
Re-check:  "We will test this again in your next session" -> spaced repetition
```
Components:
- **Model answers at the next level up**: a 3→4 rewrite of the user's own answer, not a generic textbook answer.
- **Drills**: short (5–10 min) targeted sessions generated from the weak competency; these use the same engine with a narrow blueprint.
- **Delivery coaching**: concrete numbers ("filler rate 9/min; target <4/min; top fillers: 'like', 'basically'"), with before/after across sessions.
- **STAR scaffolding** for behavioral answers: highlights which of Situation/Task/Action/Result was missing.
- **Study plan** until the interview date: weekly goals mapped to weak competencies, with spaced repetition.
- **"Next session focus"** on the home screen (single clear recommendation).
- **Resume feedback hooks**: if the interviewer repeatedly struggled to probe a bullet, suggest quantifying/clarifying it.
Content for resources is curated and verified (links checked); the LLM selects among them but does not invent URLs.

## 6. Experimentation and improvement loop for the product itself
- Every session writes an event log (versions of prompts/rubrics/models, scores, latencies, user feedback).
- Offline replay: re-run new evaluator/interviewer versions on stored sessions (with consent) before release.
- A/B framework for conversation style, follow-up policy, plan UX; success metrics: 7-day return, sessions to first
  measurable improvement, perceived usefulness, complaint rate.
- Weekly review of: lowest-rated scores, most-skipped questions, longest-latency sessions, highest-cost sessions.

## 7. Risks
- **Score inflation/deflation** erodes trust -> calibration monitoring, anchored rubrics, regular human re-labeling.
- **False precision** -> always show ranges; avoid percentiles until validated.
- **Gaming** (memorizing rubrics) -> question variety via personalization and generation; unseen-question checks.
- **Misuse as hiring signal** -> clear disclaimers; no employer-facing scoring in B2C product.
