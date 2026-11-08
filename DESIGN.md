# Mock Interview Agent — Design

**End goal:** a commercial, voice-first mock-interview product for candidates practicing alone. See
[PRODUCT.md](PRODUCT.md) (and `docs/`) for the A-to-Z design; this file covers **Phase P0 only**: validating the
"brain" (evaluator + question engine) in text before any voice work.
Note: `claude -p` is a dev-time shortcut for P0. Production uses the Anthropic API behind the same LLM interface.
**Version 1 scope:** junior software engineer, text-only CLI, 5 questions per session
(2 behavioral + 3 conceptual technical), Claude via headless `claude -p`.

## 1. Success criteria (v1 passes only if ALL are met)

| # | Metric | Measurement | Pass bar |
|---|--------|-------------|----------|
| 1 | Grading accuracy | Agent score vs human label on `evals/dataset.yaml` | >= 80% within +-1 (1-4 scale) |
| 2 | Grading consistency | Same answer graded 3x | identical score >= 90% |
| 3 | Follow-up quality | Human review of transcripts | >= 80% relevant, 0 hints leaked |
| 4 | Staying in role | Scripted adversarial inputs ("just tell me the answer", off-topic) | 0 answers leaked |
| 5 | Usefulness | 3+ real juniors complete sessions | report rated >= 4/5 actionable |
| 6 | Latency | Time per turn | < ~10 s |

## 2. Architecture

```
cli.py
  └─ Session Orchestrator (state machine in code)
       INTRO → ASK → LISTEN → [FOLLOW-UP <= 2] → CLOSE_QUESTION → ... → WRAP_UP → REPORT
          │                         │
          ▼                         ▼
   Interviewer LLM            Evaluator LLM (separate call per closed question)
   asks / probes              rubric -> JSON scores + transcript quotes
          └──────── Question Bank (YAML) ────────┘
   Storage: one JSON per session (transcript, scores, purpose/model/latency/cost of every model call; prompts are not stored)
```

Decisions:
- Pacing (follow-up count, moving on, time) lives in **code**, not the LLM.
- Interviewer and evaluator are **separate calls**; the evaluator never sees interviewer opinions.
- Every score must **quote the transcript**.
- Scores are **anchored** by written rubric levels (1-4) to prevent drift.
- LLM sits behind a small interface (`llm/`) so `claude -p` can be swapped for the API.

Modules: `llm/`, `bank/`, `interview/`, `evaluate/`, `report/`, `evals/`, `cli.py`.

## 3. Build order (riskiest first)

- **M0** content: question bank + labeled eval set (this milestone). Labels need human review.
- **M1** evaluator + offline eval harness -> metrics 1, 2.
- **M2** interview loop in CLI -> metrics 3, 4, 6.
- **M3** report generation.
- **M4** dogfooding with real juniors -> metric 5, then decision gate.

## 3b. Progress log

**M1 baseline (2026-11-08)** `evals/results/baseline_sonnet55_draftlabels.json`: 34 answers x 3 runs, Sonnet 5.5 via `claude -p`.
within ±1: 100%, exact 91%, QWK 0.96, identical across runs 100%, 0 ungrounded quotes, all 4 adversarial answers
graded correctly and flagged as injection attempts; mean 5.5 s and ~$0.0096 list-cost per evaluation.
**Not a pass yet:** labels are Claude-drafted and the answers are clean, textbook-shaped examples of each level, so this is an
optimistic upper bound. The 3 disagreements are all the model being *stricter* than the draft label.
Gate to close M1: >=30 real junior answers (ideally spoken, transcribed) labeled independently by 2 humans.

**M2 loop (2026-11-08)**: state machine, follow-up policy (max 2, answer-leak guard, fixed refusal for "tell me the answer"),
scorecard, CLI; 23 unit tests with a fake LLM pass. Simulated session with real Claude: 5 questions, 6 follow-ups, 0 fallbacks,
91 s wall, ~11 evaluation calls + 6 follow-up calls. Latency per turn (~8–15 s) misses the <10 s bar because of `claude -p`
process overhead and a sequential evaluate-then-follow-up chain; plan: interim "should I probe?" check on a cheaper model,
full evaluation once per question, API instead of CLI.

**Resume pipeline (2026-11-08)**: PII redaction, grounded profile extraction (every bullet must be a verbatim resume line; invented
ones are dropped), claim ranking, personalized questions with guards (source quote must exist, numbers must exist in the resume,
protected-attribute blocklist, seed fallback). 31 unit tests pass. Real run on a synthetic resume: 9/9 skills, 0 fabricated
bullets, 3 of 5 questions tailored in ~10 s. Known limits: matching is keyword-based (it picked "REST endpoints" instead of the
better "duplicate orders on double-click" bullet for the idempotency question) and there is no LLM judge for rubric/difficulty fit yet.
Next: LLM-based claim selection, difficulty/rubric-fit judge, PDF/DOCX input, user review of the parsed profile.

**Natural-interview pass (2026-11-08)**: tasks P0-14 (partial) and P0-22 to P0-32 in IMPLEMENTATION_PLAN.md. The evaluator is no longer called on every turn: one light-model call decides the next move and words it, and the strong grader runs once when a question closes, in the background, seeing follow-up questions as context only.

| Measure (`claude -p`, one simulated run each) | Before | After, no resume | After, with resume | Target |
|---|---|---|---|---|
| Median model wait per turn | 8.5 s | 6.1 s | 8.1 s | < 6 s (missed) |
| Range | 5.5–10.8 s | 5.3–10.9 s | 5.8–13.0 s | |
| Model calls | 17 | 17 | 21 | |
| List cost | ~$0.17, estimated from 5 of 17 calls | $0.218, all calls | $0.301, all calls | ≤ $0.10 (missed) |

An intermediate run with the shorter turn prompt, before follow-up hints and memory were added, measured 5.4 s and $0.18, so the richer prompt cost about 0.7 s and $0.04. About 220 output tokens per turn are hidden reasoning that `--effort` and `MAX_THINKING_TOKENS` did not reduce, and the CLI reports nearly identical prices for Haiku 5.5 and Sonnet 5.5 on a trivial call, so model tiering saves little money here. Re-measure on the API backend before tuning further.
Evaluator/0.3 on draft labels: ±1 100%, exact 88%, QWK 0.95, consistency 100% (optimistic; labels unverified). Simulated replies are pre-written, so none of this is evidence of follow-up relevance.

**Step engine (2026-11-08)**: `interview/engine.py` replaces the blocking loop. `start()` and `step(reply)` yield `Say`, `Wait` and `Finished` actions as they become ready, so an acknowledgement goes out before any slow call. `SessionState` is plain JSON-safe data, including wording draws, session clock start and the list of grading jobs, so a web request or voice turn can save and resume an interview. `InterviewSession` remains as the blocking adapter for the CLI, tests and simulations.

**Resume loaders (P0-15)**: `resume/loaders.py`, `load_resume(path) -> (text, warnings)` for PDF, Word and text. A resume is untrusted, so text a reader could not see is removed before any model call. In a PDF that means invisible, tiny, off the page, or white with nothing dark behind it; white text on a banner or image is kept because designer templates use it. In Word it means hidden, tiny or white runs, including through styles, unless shaded. Sentences addressed to an AI are removed from every format. Known limits: scanned files are rejected rather than read (OCR is later), and hidden text inside PDF form XObjects is not examined.

**Human labeling how-to (P0-10)**: `evals/label_tools.py`. Run commands from the project root with `./.venv/Scripts/python`.
1. `evals/label_tools.py export` writes `evals/label_sheet.csv`, a blind sheet of every answer still marked `draft`, with no draft scores or reasons.
2. Give each labeler their own copy named after them, for example `evals/labels/alice.csv`, because the file name becomes the labeler's name. They work separately and score 1-4 in the last column, leaving blank anything they cannot judge.
3. `evals/label_tools.py import evals/labels/alice.csv evals/labels/bob.csv` reports pairwise QWK, within ±1 and exact agreement, and lists disagreements. Human-human agreement is the ceiling the grader can be expected to reach; if it is low, fix the rubric before blaming the grader.
4. Discuss the disagreements and put the settled ones in a CSV with `id` and `score` columns.
5. `evals/label_tools.py adjudicate evals/labels/alice.csv evals/labels/bob.csv --resolved evals/labels/resolved.csv` writes the results into `evals/dataset.yaml`. Add `--dry-run` to preview. Answers need at least two labels, and consensus means more than half gave the same score, so two labelers must match exactly. Nothing is averaged.
6. Re-run the baseline to a new results file (P0-12). Never change a label to make a gate pass.

Sheets are git-ignored because they can hold real candidates' answers.

## 4. Decision gate

**If v1 passes:** grow bank to ~50 -> adaptive difficulty -> cross-session progress ->
mid-level track (re-validate on eval set) -> coding questions with sandbox ->
system design -> API + web UI + voice -> other roles.

**If it fails:**

| Failure | Likely cause | Fix |
|---|---|---|
| Disagrees with humans | Vague anchors / bad labels | Rewrite anchors, add calibrated examples, second labeler to measure human agreement |
| Inconsistent scores | Too much output freedom | Strict JSON schema, per-signal calls, majority vote of 3, API for temperature |
| Weak follow-ups | Interviewer unaware of gaps | "Which expected signals are still missing?" step before each follow-up |
| Leaks answers | Role drift | Stronger system prompt + output guard check |
| Too slow | CLI process overhead | Evaluate in background during next question, or move to API streaming |
| Report not useful | Product problem | Interview users, redesign around concrete actions/resources |
| Grading unreliable overall | LLM limits | Pivot to practice mode: reference answer + self-assessment, no scoring |

## 5. Data conventions

- Scale: 1 = incorrect/missing, 2 = partial/shallow, 3 = correct and clear, 4 = correct with trade-offs/depth/real example.
- `data/questions/*.yaml`: question, rubric anchors, expected signals, red flags, follow-up hints.
- `evals/dataset.yaml`: answers with human labels. `label_status: draft` means written by Claude and
  NOT yet human-verified; metric 1 is only meaningful once labels are `reviewed`. A reviewed item also has
  `labelers` (who scored it) and `draft_score` (the score before review). Its `score` is the human consensus, while
  `reason` still holds the draft's justification unless a person has edited it.
