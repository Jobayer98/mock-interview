# Mock Interview Product — A-to-Z Design (index)

Status: design draft v2. Supersedes the v1-only framing. This is a commercial product, not a prototype.
Vendor facts below come from public sources read on 2026-11-08 and are marked **[verify]** where they
must be re-checked or benchmarked before we commit money or architecture to them.

**Build status, next tasks and rules for continuing sessions: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).**

Deep dives:
1. [Resume/JD intelligence and question engine](docs/01-resume-and-question-engine.md)
2. [Realtime voice and video: human-like interaction and latency](docs/02-realtime-voice-video.md)
3. [Evaluation, progress tracking and the improvement guide](docs/03-evaluation-progress-coaching.md)
4. [Platform, cost, safety, compliance and operations](docs/04-platform-cost-safety-ops.md)
5. [Business model (Bangladesh, direct-to-candidate, bootstrapped)](docs/05-business-model.md)
6. [Bangladesh market: language, payments, connectivity, legal, go-to-market](docs/06-bangladesh-market.md)

**Decided so far:** market = Bangladesh · customers = candidates paying directly · funding = bootstrapped.
Consequence: realtime voice (about $0.6–1.00 per session) is too costly to be the default at local price points, so
text and voice-note modes ship first and live voice becomes a capped premium after demand is proven.

**Changed 2026-11-08 (user decision):** V1 now includes live voice through LiveKit, real juniors test the finished V1 live, and the pre-sale waits until the app is complete (IMPLEMENTATION_PLAN.md sections 4 and 8). The phase table below shows the earlier order. The cost reasoning still stands: live voice is the costliest mode and its cost is still unmeasured.

---

## 1. Product in one paragraph

A candidate uploads a resume (and optionally a job description), picks a target role and interview type,
and has a **spoken interview with an AI interviewer that feels like a real person**: it asks questions
generated from *their* resume and the target role, probes the specific claims they made, adapts to what
they say, and never coaches mid-interview. Afterwards they get an **evidence-based scorecard** (quotes,
timestamps, rubric levels), a **skill profile that tracks progress across sessions**, and a **concrete
improvement plan** (targeted drills, model answers, next-session focus). The moat is not the voice; voices are
rented. The moat is **trustworthy, measurable feedback that makes people better**, plus the content/eval
system behind it.

## 2. Users and jobs-to-be-done

| User | Job | What "great" means |
|---|---|---|
| Job seeker (primary) | "Make me ready for interviews at roles like X" | Feels real, feedback is specific, I can see I'm improving |
| Career switcher / fresher | "I have little experience, help me tell my story" | Resume-based questions that draw out projects, not gotchas |
| Non-native English speaker | "Practice without being judged on accent" | Content scored separately from delivery; patient turn-taking |
| Later: bootcamp/university | "Run practice at scale and see cohort gaps" | Cohort dashboard, admin controls (B2B phase, not designed in detail yet) |

## 3. Core user journey

```
Onboard -> Upload resume (+ JD) -> Review extracted profile (user corrects it) -> Choose target role/interview type
-> Mic/camera check + consent -> Live interview (voice, optional video/avatar) -> Instant summary
-> Full scorecard + annotated replay -> Improvement plan + drills -> Next session recommended focus -> Progress dashboard
```

Interview modes (same engine, different blueprints): resume deep-dive screen, behavioral, technical-conceptual,
role-specific (e.g. backend), full loop, 10-minute targeted drill on one weak competency, and later coding and
system design.

## 4. System context (end-goal architecture)

```
 Web/Mobile app (Next.js + LiveKit client SDK)  |  (v1 CLI for brain testing only)
        │ HTTPS/WebRTC
 ┌──────▼──────────────── Control plane (FastAPI, Postgres, Redis, object storage) ───────────────┐
 │ Auth/billing  Profile Service  Content Service  Session Service  Report/Progress Service      │
 └──────┬───────────────────────┬───────────────────────────────┬───────────────────────────────┘
        │ job queue (durable workflows)                          │ session token / dispatch
 ┌──────▼──────────┐   ┌────────▼──────────┐          ┌─────────▼───────────────────────────────┐
 │ Resume/JD       │   │ Post-session      │          │ Realtime plane: LiveKit room + Agent    │
 │ pipeline        │   │ pipeline          │          │ worker (STT -> fast LLM -> TTS, VAD,    │
 │ parse->profile  │   │ evaluate, score,  │          │ turn detection, optional avatar)        │
 │ ->blueprint     │   │ report, coaching  │          │  ▲ director notes   │ transcript stream  │
 └─────────────────┘   └───────────────────┘          │  │ (async)          ▼                   │
                                                      │ Deliberation loop (stronger LLM, off     │
                                                      │ the audio hot path): tracks coverage,    │
                                                      │ decides what to probe next               │
                                                      └─────────────────────────────────────────┘
 Cross-cutting: LLM Gateway (provider-agnostic, caching, schema validation) | Observability (traces, cost, latency)
                Eval harness (golden sets, regression gates) | Safety & privacy layer
```

The most important idea: **two brains**. A fast, small-model "conversation brain" owns the audio hot path
and only has to sound natural. A slower, stronger "deliberation brain" runs beside it, reads the transcript,
tracks which competencies/claims are covered, and sends the conversation brain short *director notes*
("probe the 40% latency claim; ask how it was measured"). This is how we get both human-like latency and
high-quality probing. Details in doc 02.

## 5. Build vs buy (decisions with reasons)

| Concern | Decision | Why | Alternative / revisit when |
|---|---|---|---|
| Realtime transport + agent framework | **LiveKit (Agents framework, Cloud to start)** | Open-source agent framework, pluggable STT/LLM/TTS, semantic turn detector, adaptive interruption handling, avatar plugins, self-hostable later [verify versions] | Pipecat (similar), or ElevenLabs Agents platform if time-to-market beats control |
| Voice (TTS) | **ElevenLabs as default, behind an interface; benchmark Cartesia and others** | Naturalness is the product's first impression; sources disagree on who is fastest, so measure | Switch per-language or per-cost; keep 2 providers wired for failover |
| STT | Streaming provider with word timestamps (Deepgram / ElevenLabs Scribe / others); **benchmark on our users' accents** | STT errors corrupt scoring and feel rude; accent coverage decides the winner | Whisper-class self-host for cost at scale |
| Conversation LLM | Small/fast model (Haiku-class) via Anthropic API with prompt caching | Time-to-first-token dominates perceived latency | Speech-to-speech models only if they allow transcript + control (evaluation needs both) |
| Deliberation / evaluator LLM | Stronger model (Sonnet/Opus-class) | Quality and consistency matter more than speed; runs async | Use batch/cheaper tiers for post-session work |
| Avatar / video | **Phase 4, optional.** Audio-first. Use a LiveKit-integrated avatar provider | Avatar adds latency/cost and uncanny-valley risk; voice carries most of the realism | If testing shows avatar improves retention enough to pay for it |
| Resume parsing | LLM extraction with strict schema + layout-aware PDF text extraction | Fast to build; verify with golden resumes | Specialized parser if accuracy is poor |
| Orchestration of async jobs | Durable workflow engine (Temporal) or Postgres-backed queue first | Resume/report pipelines must be retryable and idempotent | Start simple; adopt Temporal when workflows multiply |
| **LLM access in production** | **Anthropic API (not `claude -p`)** | `claude -p` rides a personal subscription, adds process-start latency, gives no sampling control, and is not a commercial-scale integration; check the terms of use before any paid product relies on it. Use it only for dev-time prototyping | — |

Sources: [LiveKit adaptive interruption handling](https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling.md),
[LiveKit turn-taking tuning](https://docs.livekit.io/agents/logic/turns/tuning.md),
[LiveKit latency guide](https://livekit.com/blog/understand-and-improve-agent-latency),
[ElevenLabs agent pricing guide](https://elevenlabs.io/blog/ai-voice-agent-pricing-guide).

## 6. Product principles (they decide trade-offs)

1. **Feedback must be specific and defensible**: every claim cites a quote and timestamp.
2. **Measure content and delivery separately.** Accent never lowers a content score.
3. **Interviewer never coaches mid-interview**; coaching lives in the debrief.
4. **Every AI component has an eval and a regression gate.** No prompt change ships on vibes.
5. **Graceful degradation**: voice fails -> text; avatar fails -> audio; LLM fails -> fallback model.
6. **User owns their data**: resume and recordings deletable; no training on them without opt-in.
7. **Honest framing**: practice feedback, not a hiring decision or a guarantee.

## 7. Phased delivery with gates (each phase ends with a measured go/no-go)

| Phase | Deliverable | Gate to continue | If it fails |
|---|---|---|---|
| **P0 Brain (current)** | Text-only: evaluator + resume-driven question generation validated offline on golden sets | Grading within ±1 of humans on ≥80%; consistency ≥90%; generated questions judged relevant ≥85% with 0 fabricated resume facts | Fix rubrics/prompts; add examples; per-signal grading; as last resort pivot to self-assessment practice mode |
| **P1a Revenue MVP (cheap modes)** | Web app: resume analyzer, text interview, voice-note interview (record answer -> transcript -> scorecard), BDT prepaid passes. **First step: pre-sale landing page to prove demand** | ≥30 paying founding members; free->paid ≥4%; COGS per pass within plan; scorecards rated fair ≥80% | Lower price floor, simplify scope, interview users, fix grader before adding features |
| **P1b Live voice (premium, capped)** | LiveKit realtime voice interview, 1 role/level, resume-aware | p50 voice-to-voice <1.0s, p95 <2.0s; ≥70% of testers say "felt natural"; live-voice COGS ≤ ~$1.00 per session; accent test set WER acceptable | Re-tune turn detection, switch TTS/STT, move deliberation off hot path, shrink context; keep voice-note as the main voice mode |
| **P2 Progress and coaching** | Skill profile, trend, improvement plan, drills, spaced practice | Cohort of ≥30 users: ≥40% do a 2nd session within 7 days; measurable score gain after 3 sessions on matched competencies | Interview users, redesign plan UX, fix the grader if gains are noise |
| **P3 Breadth** | Mid/senior, coding + system design, more roles via content pipeline | New role goes live in <2 weeks of content work with eval passing | Improve authoring tools/LLM-assisted content with human review |
| **P4 Video/avatar + polish** | Optional avatar, candidate-video delivery coaching (no emotion inference) | Avatar A/B shows retention or conversion lift above its cost | Drop avatar, invest in voice quality |
| **P5 B2B** | Cohort dashboards, admin, SSO, data controls | Pilot with 2–3 institutions | Stay B2C |

## 8. Decisions I need from you (they change the design, so they must be explicit)

1. **Business model and price point.** Proposed in [docs/05-business-model.md](docs/05-business-model.md): freemium funnel -> time-boxed
   "Sprint" pass (capped voice sessions) -> top-ups -> B2B later. **Updated for Bangladesh/bootstrapped:** BDT prepaid passes
   (Sprint ~৳799, Voice Pro ~৳1,999, assumed, to be pre-sale tested), tiered by cost-to-serve. See docs 05 and 06.
2. ~~Interview language~~ **Decided: strictly English** (market: Bangladesh). Speech vendors are benchmarked on Bangladeshi-accented English.
3. **Platform first:** web only (my recommendation) or mobile too?
4. **Team and runway:** who builds what, and by when? It decides how much we buy vs build.
5. **Roles after Software Engineer** that you want in the first year.
6. **Recording policy:** store audio/video by default, or transcript only unless the user opts in?
7. **Target market for legal purposes** (EU users trigger stricter rules; see doc 04).
