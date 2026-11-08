# 04 — Platform, Cost, Safety, Compliance and Operations

## 1. Platform and stack (recommendation, adjustable)

| Layer | Choice | Notes |
|---|---|---|
| Frontend | Next.js/React + LiveKit client SDK | Web first; PWA later; native mobile only if data shows demand |
| API / control plane | Python (FastAPI) | Same language as agent workers and eval code |
| Realtime | LiveKit Cloud to start (self-host later if cost/regulation demands) + LiveKit Agents (Python) workers | Worker autoscaling, one agent per session |
| Database | Postgres (+ pgvector for dedupe/similarity) | Sessions as append-only events + derived tables |
| Cache / state | Redis | Live session state, rate limits |
| Object storage | S3-compatible, encrypted, per-user prefixes | Audio, resumes; lifecycle rules for retention |
| Async workflows | Postgres-backed queue first -> Temporal when needed | Resume pipeline, post-session scoring, report generation: idempotent and retryable |
| LLM access | Anthropic API via our LLM Gateway | Prompt caching, streaming, structured output, retries, fallbacks, spend caps |
| Observability | OpenTelemetry + a LLM-trace tool (e.g. Langfuse) + dashboards/alerts | Per-call latency, tokens, cost, schema violations |
| Auth / billing | Managed auth (OIDC) + local payment rails (see docs/06 section 2) | Don't build either; launch market is Bangladesh, so international card processors are not assumed |
| Infra | Containers on a managed platform; IaC; staging + prod | Region choice driven by user geography and provider locations |

## 2. Core data model

`User` · `Resume` (versioned, file + parsed `Profile`) · `JobDescription` · `Role/Level/Competency/Question/Rubric` (versioned)
· `Blueprint` · `Session` (config, versions pinned) → `Event`* (turn, probe, director-note, score, system) · `Recording` ·
`Evaluation` (rubric_version, evaluator_version, signals, quotes) · `Scorecard` · `SkillEstimate` (user × competency × time) ·
`ActionCard` / `PracticePlan` · `Feedback` (user ratings of scores) · `UsageLedger` (cost per component per session).
Everything versioned so history stays comparable and old sessions can be re-graded.

## 3. Cost model (estimates to validate in spike S1; not measured)

Cost per session = voice infra + STT + TTS + conversation LLM + deliberation LLM + post-session evaluation/report + storage.

Reported list prices seen on 2026-11-08 (third-party and vendor pages, **[verify]**): ElevenLabs Agents platform about
$0.08/min *excluding* LLM and telephony ([guide](https://elevenlabs.io/blog/ai-voice-agent-pricing-guide),
[comparison](https://speechify.ai/blog/best-voice-agent-platforms-2026)); standalone Flash/Turbo TTS about $0.05 per 1K characters
([pricing roundup](https://www.happyrobot.ai/hub/elevenlabs-pricing)).

Worked estimate for a **30-minute interview** (interviewer speaks ~25% of the time, ~900 characters/minute of speech):
- Agent speech ≈ 7.5 min ≈ 6,750 characters -> TTS at $0.05/1K ≈ **$0.34** (so **TTS is likely the largest single line**).
- All-in managed platform at $0.08/min x 30 = **$2.40 before LLM**, i.e. substantially more than a custom pipeline at these list prices,
  but it buys speed-to-market; revisit with real volume discounts.
- STT streaming, conversation LLM (small model, cached prefix), deliberation + evaluation LLM calls, LiveKit minutes: **measure**;
  expected to be small individually, material together.
- **Target COGS**: ≤ $1.00 per 30-minute voice session at launch, ≤ $0.60 after optimization. Price floor therefore depends on
  your business model (decision #1 in PRODUCT.md).

### Cost levers (ordered by impact)
1. TTS: shorter agent utterances (also more human), cache stable phrases, cheaper voice tier for non-critical lines, characters-per-session cap.
2. Don't generate audio nobody hears: stop synthesis immediately on barge-in.
3. Prompt caching for static prefixes; keep hot-path context short (summaries of older turns).
4. Model tiering: small model on the hot path; strong model only for evaluation/deliberation; batch/cheaper tiers for non-urgent report work.
5. Evaluate once per answer, not per turn; skip LLM for deterministic metrics.
6. Free tier with strict minutes/features; text-only mode as the cheap tier.
7. Per-session and per-user budget guards; abuse and rate limiting; session time caps.
8. Volume negotiation and self-hosting STT/turn detection once volume justifies it.
Track `cost_per_session` and `cost_per_active_user_week` as first-class product metrics, with alerts.

## 4. Performance and scalability
- SLOs: voice-to-voice p50 ≤ 1.0 s / p95 ≤ 2.0 s; session start-up ≤ 5 s; scorecard ready ≤ 60 s after the session; availability ≥ 99.5% at launch.
- Capacity: concurrent sessions = workers; warm pool of agent processes; autoscale on active sessions and queue depth; load test at 2x peak.
- Backpressure: queue post-session jobs, never let them compete with live sessions for LLM rate limits (separate API keys/quota pools).
- Failure modes designed and drilled: STT outage, TTS outage, LLM rate-limit, LiveKit region issue, worker crash mid-session (resume from state).

## 5. Safety, privacy and compliance

| Area | Requirement |
|---|---|
| Consent | Explicit consent before mic/camera/recording; separate consent for storing audio; clear "AI interviewer" disclosure |
| Data minimization | Redact identifiers before LLM calls where possible; transcript-only by default (decision #6) |
| Retention and deletion | User-controlled delete (account, resume, recordings, transcripts); default retention windows; backups purged on schedule |
| Security | Encryption in transit/at rest, per-user access control, secrets management, audit logs, vendor DPAs, pen test before launch |
| Vendor data use | Confirm each vendor (LLM/STT/TTS/avatar) does not train on our data; zero-retention options where available |
| Prompt injection | Resume, JD, and answers are untrusted data; delimiters, schema-validated outputs, no tools in grading calls; adversarial test set |
| Fairness | Content vs. delivery separation, redaction, bias tests on synthetic variants; avoid protected-attribute questions |
| **Emotion/biometric inference** | **Not built.** EU AI Act restricts emotion recognition in workplace/education and treats related uses as high-risk; interview contexts are likely caught ([FPF](https://fpf.org/blog/red-lines-under-eu-ai-act-unpacking-the-prohibition-of-emotion-recognition-in-the-workplace-and-education-institutions/)); also check state laws on biometrics and AI interviews (e.g. voice/face data) **[needs legal review]** |
| Hiring-use classification | A B2C practice tool is different from tools used to select candidates; the AI Act treats recruitment AI as high-risk, so a B2B/employer pivot triggers heavy obligations. Keep employer-facing scoring out of scope until reviewed with counsel |
| Honesty | Visible disclaimers: practice feedback, not an assessment of hireability |
| Content safety | Moderate user inputs/outputs; handle abusive or distressed users politely, end session on abuse; no medical/legal advice |
| Accessibility | Captions, keyboard navigation, adjustable pace, text fallback (WCAG 2.2 AA target) |
| Children | Minimum age policy (18+ or local consent age) |

## 6. Operations and quality
- **Release process**: prompts/rubrics/models are versioned artifacts; every change passes the eval gate, canary at 5% traffic, auto-rollback on regression.
- **Monitoring**: latency/cost/failure dashboards; score-distribution drift alerts; STT confidence trends; user complaint rate; session abandonment points.
- **Incident response**: runbooks per failure mode; status page; postmortems.
- **Support tooling**: replay a session with all versions and prompts (access-controlled) to debug complaints.
- **Vendor management**: two providers per critical stage kept warm; quarterly re-benchmarks (TTS/STT/LLM quality and price move quickly).

## 7. Team shape (rough, for planning)
Product/design (1), full-stack (1–2), realtime/voice engineer (1), AI/evaluation engineer (1), content/curriculum lead (part-time,
domain expert), plus legal/privacy advice (ad hoc). A small team can ship P0–P2; P3+ scales content more than code.

## 8. Top risks (whole product)
| Risk | Impact | Mitigation |
|---|---|---|
| Feedback not trusted | Churn | Eval gate, evidence quotes, uncertainty display, user feedback loop |
| Voice feels robotic/laggy | Low retention | Spikes S1–S2, naturalness panel gate, provider failover |
| Cost per session too high | Unviable pricing | Budget guards, tiered models, TTS levers, text tier |
| Regulatory exposure (biometrics, hiring AI, privacy) | Fines, blocked markets | No emotion inference, consent, retention controls, legal review before launch |
| Content bottleneck | Slow role expansion | Competency library + LLM-assisted authoring + human review |
| Vendor lock-in / price changes | Margin or availability | Gateway interfaces, dual providers, self-host options |
| Retention after novelty | Weak business | Progress, plan, and spaced practice in P2; measure 7-day return early |
