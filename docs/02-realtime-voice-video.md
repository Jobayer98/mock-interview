# 02 — Realtime Voice and Video: Human-Like Interaction and Latency

Goal: a conversation that feels like talking to a person who is paying attention: fast responses, no
talking over the candidate, natural pauses, a warm and consistent voice, and robust handling of messy
real-world audio.

## 1. Architecture choice: cascaded pipeline with streaming (recommended)

```
Candidate mic ─WebRTC─► LiveKit room ─► Agent worker
   VAD + semantic turn detector ─► streaming STT ─► conversation LLM (streaming) ─► streaming TTS ─► room audio
                                         │                  ▲
                                         ▼                  │ director notes (async, short)
                                  transcript events ─► Deliberation brain (stronger LLM, off hot path)
```

**Why cascaded over speech-to-speech:**
- We need exact transcripts with timestamps for scoring, replay and audits; the pipeline gives them natively.
- We need control (what the interviewer may say, no answer leakage, injection defense) that is easier to
  enforce with text between the stages.
- Components can be swapped and benchmarked independently, and each stage is observable.
- Speech-to-speech can be re-evaluated later as an experiment; the engine only needs `Turn` objects either way.
Industry guidance also reports cascaded as the usual production default for tool use and observability
([Fora Soft overview](https://www.forasoft.com/learn/livekit-for-ai-agents-guide); vendor-style source, so treat as a signal not proof).

## 2. Latency budget (targets, to be validated in spike S1)

Target: **p50 ≤ 0.8–1.0 s, p95 ≤ 2.0 s** from the candidate's last word to the first audible agent sound.
Humans typically respond in a few hundred ms; anything over ~1.5 s feels like a call lag. An interviewer
can sometimes pause longer for "thinking" and that is acceptable if it is natural (see backchannels).

| Stage | Typical contribution (to measure) | Main levers |
|---|---|---|
| Endpointing (decide the candidate finished) | 200–500 ms, **the biggest tunable** | Semantic turn detector + VAD; tune per interview phase (longer pause tolerance for thinking questions) |
| STT final transcript | 50–200 ms after endpoint (streaming) | Streaming STT with interim results; vendor choice per accent |
| LLM time-to-first-token | 250–500 ms | Small fast model; prompt caching; short, stable system prompt; region co-location |
| TTS time-to-first-audio | 75–250 ms (claims vary: ElevenLabs Flash advertises ~75 ms; other vendors claim lower; sources disagree, so benchmark) | Streaming TTS, sentence-level chunking, keep-alive connections |
| Network + audio buffering | 50–150 ms | Agent worker in same region as LiveKit and providers; avoid cross-region hops |
| Avatar sync (if used) | adds latency; LiveKit rates impact low–medium | Avatar joins room directly as a participant; measure `AvatarMetrics` |

### Latency tactics (ordered by payoff)
1. **Tune turn detection first.** Use LiveKit's semantic turn detector and adaptive interruption handling
   (distinguishes real interruptions from "mm-hmm") rather than raw silence timeouts
   ([turn tuning](https://docs.livekit.io/agents/logic/turns/tuning.md),
   [adaptive interruption](https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling.md)).
2. **Keep the heavy thinking off the hot path.** The conversation brain gets a small prompt: persona, current
   question, last N turns, director notes. No grading, no long resume dump, no tool calls in the audio loop.
3. **Speculative generation**: start the LLM on a likely-final interim transcript; cancel/restart if the
   candidate keeps talking. Only enable if measured to help without causing double-speak.
4. **Stream everything**: LLM tokens -> sentence chunker -> TTS; first sentence short (6–10 words) so audio starts fast.
5. **Prompt caching** for the static prefix (persona + rubric + resume summary) to cut TTFT and cost.
6. **Pre-render stable phrases** (greeting, transitions, "take your time") as cached audio: zero TTS latency.
7. **Warm connections** and pre-created sessions before the user clicks start.
8. **Co-locate** worker, LiveKit, STT, TTS and LLM regions; measure per-region.
9. **Fallback chains** with timeouts (e.g. 1.5 s TTFT timeout -> switch model) so tail latency stays bounded.
Instrument every stage (LiveKit exposes per-stage metrics) and chart p50/p95 per release
([LiveKit latency guide](https://livekit.com/blog/understand-and-improve-agent-latency)).

## 3. What makes it feel human (behaviors we design, test, and tune)

| Behavior | Implementation |
|---|---|
| Doesn't interrupt thinking pauses | Semantic end-of-turn + phase-aware silence tolerance; candidates pause 2–5 s mid-answer |
| Handles being interrupted | Barge-in: stop speaking quickly, keep context, resume or yield gracefully |
| Backchannels | Brief "mm-hmm", "I see" at natural pauses, rare and non-intrusive; never as scored feedback |
| Filler while "thinking" | Short natural acknowledgement ("Good, let me ask about...") when a long LLM/tool step is needed |
| Varied, natural prosody | High-quality TTS voice, concise sentences, no bullet-speak, contractions, no repeating the candidate's full answer |
| Consistent persona | Fixed interviewer persona per session (name, tone, formality), selectable intensity (friendly / neutral / stress-style) |
| Memory within the session | Refers back to earlier answers ("Earlier you mentioned Redis...") |
| Stays in role | No coaching, no leaking answers, polite deflection of "just tell me the answer" |
| Pacing control | Code-level timers: warns near the end, wraps up gracefully, never cuts mid-sentence |
| Recovery | "Sorry, I didn't catch that" on low-confidence STT instead of acting on garbage |
| Accessibility | Live captions, adjustable speaking rate, text-fallback mode, longer pause tolerance setting |

Conversation style guide (prompt-level): short turns (1–3 sentences), one question at a time, spoken-language
register, no markdown or lists, acknowledge before asking next, vary acknowledgements.

## 4. Audio quality and robustness
- Noise suppression / echo cancellation on the client and optional server-side filter.
- Device/permission pre-check (mic level meter, 10 s test) to avoid failed sessions.
- Low-confidence STT handling: confirm key terms ("Kafka or Kafka Streams?"), never silently score garbled text.
- Reconnect logic: the session survives network drops up to ~30–60 s; state lives server-side and resumes.
- Jitter / packet loss telemetry; automatic downgrade to audio-only or text on poor links.
- Domain vocabulary boosting for STT (tech terms from the resume and role: "Kubernetes", "PostgreSQL").

## 5. Video and avatar

Principle: **audio-first; video is optional and must earn its cost.**

| Option | Value | Cost/risk | Phase |
|---|---|---|---|
| Audio-only + animated presence UI (waveform/avatar still) | Low cost, low risk, 80% of the realism | — | P1 |
| Candidate video on (for self-review of posture/eye contact) | Useful replay for delivery coaching | Storage, consent, bandwidth, privacy | P4 |
| Photoreal/talking-head avatar for the interviewer | Stronger presence, "real interview" feel | Added latency, per-minute vendor cost, uncanny valley, lip-sync glitches | P4 (A/B before committing) |

LiveKit's agents ecosystem has avatar plugins (e.g. Anam, Bey) that join the room as a participant, which is the
recommended low-overhead integration ([LiveKit Agents docs](https://docs.livekit.io/agents-js)) **[verify provider list/pricing]**.

**Hard guardrail — no emotion inference.** Do not infer emotions, stress, or confidence from face or voice.
The EU AI Act prohibits emotion recognition in workplace and education settings, treats other emotion
recognition as high-risk, and interview contexts are likely to be caught; the exact reach is still debated
([FPF analysis](https://fpf.org/blog/red-lines-under-eu-ai-act-unpacking-the-prohibition-of-emotion-recognition-in-the-workplace-and-education-institutions/);
single secondary-source reading, **needs legal review**). Delivery coaching uses only **objective, behavior-level
signals** the user can verify: words per minute, filler-word rate, long pauses, talk-time ratio, answer length,
time to first word, and (opt-in, video) gaze-off-camera percentage framed as a tip, never as a personality score.

## 6. Reliability design
- Agent workers are stateless; session state in Redis/Postgres; any worker can resume a session.
- Provider failover per stage (TTS A -> TTS B; LLM primary -> secondary) with circuit breakers.
- Degradation ladder: avatar -> audio -> text chat. The interview is never lost; the transcript is always saved.
- Autoscaling by concurrent sessions with warm pool; load tests at 2x expected peak before launch.
- Graceful session end on budget guard (cost/time ceilings per session).

## 7. Testing the realtime layer
- **Scripted synthetic callers** (TTS-generated candidate audio with accents, pauses, interruptions, noise) in CI
  to catch regressions in latency and turn-taking.
- **Metrics dashboard**: voice-to-voice p50/p95, interruption false-positive/negative rate, endpointing
  early-cut rate, STT word error rate on a held-out accent set, TTS TTFB.
- **Human "naturalness" panel**: 20 recorded sessions rated 1–5 on naturalness, pacing, and interruption handling,
  per release candidate; gate P1 on ≥ 4.0 average.
- Adversarial audio set: crosstalk, silence, long monologues, "um" storms, mid-sentence topic changes.

## 8. Spikes to run before committing (1–2 weeks)
- **S1 Latency/quality bake-off**: LiveKit pipeline with 2 STT x 2 LLM x 3 TTS options; measure p50/p95 and human
  naturalness on 30 scripted conversations.
- **S2 Turn-taking tuning**: semantic turn detector vs VAD-only on candidates who think aloud with long pauses.
- **S3 ElevenLabs Agents platform vs custom LiveKit pipeline**: compare time-to-build, observability (can we get exact
  transcripts/timestamps and inject director notes?), cost per minute. If it cannot support the two-brain design,
  it is a prototype tool only.
- **S4 Accent coverage**: STT WER on target-region speakers.
