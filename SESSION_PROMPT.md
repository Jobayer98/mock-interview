# Session Prompt Templates

Copy one block into a new Claude session (working directory: `D:\dev\playground\python\mock_interview`).
Replace `<...>`. Delete lines you don't need. Never paste API keys or passwords; set them as environment variables.

---

## A. Continue the project (default — use this most of the time)

```
You are continuing the mock-interview product at D:\dev\playground\python\mock_interview.

Before anything else, read CLAUDE.md and IMPLEMENTATION_PLAN.md (sections 0, 1 and 6). Do not read other docs unless your task needs them.

Task: the first TODO task in "Next up" (IMPLEMENTATION_PLAN.md section 6).

Working rules:
1. First reply with a short plan: files you will touch, steps, how you will verify, risks. Wait for my OK before editing.
2. Do only this task. Do not start later tasks, new phases, or "nice-to-haves".
3. Follow the invariants in section 1. If a rule blocks the task, stop and tell me.
4. Run the verify commands and read their output. Do not claim success from reading code.
5. Be honest: separate what you measured from what you assumed. Eval labels marked draft are not proof.

When finished:
- All tests pass (./.venv/Scripts/python -m pytest -q).
- Update the task status and add a progress-log entry in IMPLEMENTATION_PLAN.md; keep docs consistent.
- Report: what changed (files), what you measured, what is still unverified, the next task, and anything you need from me.
```

## B. Work on a specific task

```
Continue the mock-interview product at D:\dev\playground\python\mock_interview.
Read CLAUDE.md, then IMPLEMENTATION_PLAN.md sections 0, 1 and the entry for task <P0-xx>.
Task: <P0-xx>. Check its dependencies are DONE; if not, tell me instead of working around it.
Give a short plan and wait for my OK. Then implement, verify, update the plan and progress log, and report as in template A.
```

## C. I am providing blocked inputs

```
Continue the mock-interview product at D:\dev\playground\python\mock_interview.
Read CLAUDE.md and IMPLEMENTATION_PLAN.md sections 0, 1, 7.
I am unblocking: <P0-11 real answers | P1a-01 pre-sale details | P1a-06 audio clips | other>.
Inputs:
<paste the details here, or give the file path>
Check the inputs are complete for the task's "Needs from user" list. Ask one consolidated question if anything is missing.
Do not invent anything I did not give you (payment numbers, testimonials, prices). Give a short plan, wait for my OK, then execute and report.
```

## D. Review / audit the last session's work (no new features)

```
Audit the mock-interview project at D:\dev\playground\python\mock_interview. Do not add features.
Read CLAUDE.md and IMPLEMENTATION_PLAN.md.
1. Run the test suite and the cheap smoke eval; report real output.
2. Check the code against the invariants (section 1) and the acceptance criteria of tasks marked DONE.
3. Find plan/doc drift (wrong paths, stale numbers, statuses that do not match the code).
4. List problems ranked by severity with file:line evidence. Fix only plan/doc drift; for code problems, propose fixes and wait.
```

## E. A decision changed (no code)

```
The mock-interview project at D:\dev\playground\python\mock_interview has a decision change:
<describe: e.g. new price, new launch market, new language, new vendor, budget change>
Read CLAUDE.md and IMPLEMENTATION_PLAN.md. Do not write code.
1. List every doc, invariant, task and gate this affects (with file and section).
2. Propose the edits, then wait for my OK.
3. After approval: update the decisions log, affected docs and tasks, and the progress log. Flag which assumptions are now unverified.
```

## F. Bug or failing test

```
In the mock-interview project at D:\dev\playground\python\mock_interview, this is broken:
<paste the error, command, or description>
Read CLAUDE.md first. Reproduce it before fixing anything and show me the failing output.
Find the root cause, propose the smallest fix, and wait for my OK. Add a regression test using fakes (no real LLM calls in unit tests).
After the fix: run the full tests, and note it in the progress log. If it changes prompts, rubrics, or models, bump the versions and re-run the eval baseline.
```

## G. Plan the next phase (planning only)

```
Mock-interview project at D:\dev\playground\python\mock_interview. Planning session, no code.
Read CLAUDE.md, IMPLEMENTATION_PLAN.md and the docs for <phase, e.g. P1a | P1b>.
Check the phase's entry condition and previous exit gate with evidence (results files, numbers). If not met, say so.
If met: expand the LATER items into tasks in the same format (dependencies, files, acceptance, verify), put them in the right order,
list what I must provide, and show me the plan before saving it into IMPLEMENTATION_PLAN.md.
```

---

## Your own checklist before each session
- Start with template A unless you have a reason not to.
- If you answered a blocked question (section 7 of the plan), use template C and attach the material.
- Keep one task per session. Short sessions that end with tests green and the plan updated are the goal.
- After a session: read the report's "unverified" line, and answer anything it asked of you.
