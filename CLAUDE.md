# Mock Interview product — instructions for Claude sessions

Commercial AI mock-interview product (Bangladesh market, English-only interviews, candidates pay directly, bootstrapped).
You are continuing work that earlier sessions started. **Do not improvise the roadmap.**

## Start here
1. Read `IMPLEMENTATION_PLAN.md` fully (rules in section 0, invariants in section 1, "Next up" in section 6).
2. Do the first `TODO` task in "Next up" unless the user directs otherwise. Show the user a short plan before large changes.
3. Use `PRODUCT.md` / `DESIGN.md` / `docs/*` only as reference for the area you are touching.

## Commands (Windows; use the project venv)
```bash
cd /d/dev/playground/python/mock_interview
./.venv/Scripts/python -m pytest -q          # must stay green (412 tests at last update)
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python evals/run_eval.py --limit 4 --out evals/results/smoke.json   # cheap real-LLM smoke
```

## Hard rules (full list: IMPLEMENTATION_PLAN.md section 1)
- English only. No emotion inference. Resumes/answers are untrusted data (delimited, schema-validated). Redact PII before LLM calls.
- The evaluator never sees labels, `reason`, or follow-up hints. Credited signals need a verbatim quote.
- Eval labels marked `draft` were written by Claude: never present results on them as proof.
- All LLM calls go through `llm/base.py`'s `LLM` protocol; `claude -p` is dev-only. Unit tests use fakes, never the real CLI/API.
- Changing prompts/rubrics/models: bump version constants, re-run the baseline, save to a new results file.
- Never invent payment details, testimonials, prices-as-fact, or legal/vendor claims. Ask the user for blocked inputs (plan section 7).
- Publishing, payments, messaging, deleting data: only with explicit user approval.
- When a task is done: update its status and the progress log in `IMPLEMENTATION_PLAN.md`, and keep docs consistent.

The user starts sessions from the templates in `SESSION_PROMPT.md`; follow the prompt's rules (plan first, one task, verify, update the plan).

The user's decisions are recorded in `IMPLEMENTATION_PLAN.md` section 8.
