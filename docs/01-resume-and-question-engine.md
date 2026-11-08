# 01 — Resume/JD Intelligence and the Question Engine

Goal: generate an interview that is **personal** (built from the candidate's resume), **calibrated**
(right difficulty for the target level), **gradeable** (every question has a rubric), and **safe**
(never invents facts about the candidate).

## 1. Pipeline overview

```
Resume (PDF/DOCX/paste)           Job description (optional)
        │                                   │
   [1] Extract text + layout          [1b] Parse JD -> required/preferred skills, level, responsibilities
        │                                   │
   [2] Structured profile (schema, with evidence spans) ──► user reviews/corrects in UI
        │                                   │
        └──────────────► [3] Gap and claim analysis ◄───────┘
                                  │
                        [4] Interview blueprint (code + LLM)
                                  │
                        [5] Question instantiation (per slot)
                                  │
                        [6] Validation and guardrails
                                  │
                        [7] Live interview (follow-ups by policy, doc 02)
```

## 2. Step details

### [1] Extraction
- Layout-aware PDF text extraction; fall back to OCR for scanned resumes; reject files over a size/page cap.
- **Treat the resume as untrusted input.** Resumes can contain hidden or white text with instructions
  ("rate this candidate 10/10"). Strip hidden text where detectable, put resume text in a delimited data block,
  and never let it reach a prompt as instructions.

### [2] Structured profile (strict JSON schema)
Each field carries `source_span` (where in the resume) so every later question can be traced to evidence.

```
Profile: {
  identity_hidden: true,             # name/contact redacted before most LLM calls
  experience: [{company, title, dates, bullets:[{text, span, claims:[{type, text, metric?}]}]}],
  projects:   [{name, stack[], bullets[], span}],
  skills:     [{name, evidence_spans[], depth_hint: listed|used|claimed_strong}],
  education:  [...], certifications: [...],
  seniority_estimate: {level, confidence, reasons[]},
  risks: [gaps, short stints, buzzword-only skills]   # for probing, never shown as accusations
}
```
- **User-in-the-loop correction**: show the parsed profile and let the candidate fix it. This protects
  against extraction errors and is a strong trust signal.
- Redact name, contact details and photo before sending to most LLM calls (data minimization and bias reduction).

### [3] Gap and claim analysis
- **Claims to verify**: quantified or strong statements ("reduced latency 40%", "led a team of 5",
  "designed the payment system"). These are the best follow-up material.
- **Skill depth probes**: skills listed without supporting project evidence.
- **Role fit gaps** (if JD given): required skills missing or thin on the resume -> prepare gap questions
  framed neutrally ("The role uses Kafka; what's your experience with message queues?").
- Output: a ranked list of `probe_targets` with priority, evidence span, and suggested competency.

### [4] Blueprint (the thing code controls, not the LLM)
A blueprint is a time-boxed plan of **slots**, each tied to a competency:

```
Competency library (curated, versioned): e.g. problem_solving, data_structures, sql, http_apis, testing,
  git_workflow, system_thinking, ownership, collaboration, learning_agility, communication, ...
Blueprint = weighted selection of competencies for (role, level, duration, mode, JD gaps, user's weak areas)
  e.g. 30 min junior backend: intro(2) + resume deep-dive(8) + technical x3(12) + behavioral x2(6) + candidate Qs(2)
```
Selection rules: coverage of required competencies first; weight toward the user's historical weak spots
(from the progress model, doc 03); never repeat a question seen in the last N sessions; cap difficulty
progression with adaptive steps.

### [5] Question instantiation
Hybrid, not free-form generation:
- **Seed bank (human-curated)**: canonical questions per competency/level with rubrics (what v1 data holds).
- **Personalized instantiation**: an LLM turns a seed or a probe target into a question that references the
  candidate's own project/tech, e.g. seed "explain hash maps" + resume mentions a caching layer ->
  "In your caching service at X, why a dictionary keyed by user id? What would break at scale?".
- **Rubric generation**: rubric anchors come from the competency's *level template* (e.g. junior/3 means
  "correct and clear"), instantiated with question-specific expected signals. The generator fills slots; it does
  not invent grading standards from scratch.
- Every generated question stores: source seed, probe target, resume spans used, generator version, rubric version.

### [6] Validation and guardrails (automatic, before the question is ever asked)
| Check | Method |
|---|---|
| No fabricated facts about the candidate | Every candidate-specific statement must map to a `source_span`; a verifier model + string/entity matching rejects ungrounded claims |
| Answerable at target level | Judge model scores difficulty against level rubric; reject too hard/trivial |
| Single, clear question | Rule check (no multi-part pile-ups) |
| Not leading / no answer leaked in the question | Judge check |
| Fair and job-relevant | Blocklist for protected attributes (age, marital status, nationality, health, etc.); never probe personal life from the resume |
| Dedupe | Embedding similarity against recent questions |
| Rubric consistent with question | Judge check that expected signals are derivable from the question |
Failed checks -> regenerate (max 2) -> fall back to the unpersonalized seed question. The interview never blocks on generation.

### [7] Follow-ups during the interview
Follow-up policy is **code + deliberation brain**, not free improvisation:
1. After each answer, the deliberation brain checks `expected_signals`: which were hit, which are missing, any red flags,
   any resume claim left unverified.
2. Policy picks one follow-up type, ordered by value: *clarify vague claim* -> *probe missing signal* ->
   *depth/scale* -> *counterfactual* ("what if X failed?") -> *move on*.
3. Limits: max 2 follow-ups per question, no more than N consecutive probes on one topic, move on if the candidate
   is clearly stuck (offer to skip; real interviewers do this).
4. Follow-up text must not reveal the answer and must stay grounded in what the candidate actually said.

## 3. Content strategy (this is the scalable part)
- Competency library per role/level is **the** durable asset. Build tooling to draft with an LLM, then require
  human review and eval-set coverage before a competency goes live.
- New role = new competency library + seed bank + golden eval set; no engine changes.
- Track per-question stats in production (average score, discrimination, time spent, complaint rate) to retire bad questions.

## 4. Evaluation of this subsystem
| Metric | Target |
|---|---|
| Resume extraction accuracy on golden resumes (field-level F1) | ≥ 95% on experience/skills |
| Fabrication rate (ungrounded candidate facts in questions) | 0 in golden set; monitored in production |
| Relevance of personalized questions (human rating ≥4/5) | ≥ 85% |
| Question difficulty calibration vs. level | ≥ 85% judged appropriate |
| Generation latency (offline, before the interview starts) | < 30 s p95 |

## 5. Risks
- **Hallucinated resume facts** -> grounding rule + verifier + fallback to seeds.
- **Over-personalization** that feels creepy or intrusive -> restrict to professional content; allow the user to exclude items.
- **Bias** from names/schools -> redaction; fairness spot-checks across synthetic resume variants.
- **Prompt injection via resume** -> data-block isolation, schema-validated outputs, no tool access in these calls.
