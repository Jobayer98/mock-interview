# 05 — Business Model (Bangladesh, direct-to-candidate, bootstrapped)

Inputs from you: **launch market Bangladesh · candidates pay directly · bootstrapped.**
This changes the earlier (USD-style) recommendation materially. See [06-bangladesh-market.md](06-bangladesh-market.md) for the
market constraints behind it. All numbers are **assumptions to validate**: FX 122 BDT/USD (unverified), local gateway fee 2.5% (unverified),
and cost per session by mode (unmeasured). Re-run `econ_bd.py`-style math once spike S1 gives real costs.

## 1. What changed and why

| Fact | Consequence |
|---|---|
| Fresh software graduates at local firms commonly start around ৳20,000–35,000/month ([blog summary](https://blog.nextjobz.com.bd/?p=606); [Payscale](https://www.payscale.com/research/BD/Job=Software_Engineer/Salary/1ff29e35/Early-Career), self-reported, wide range) | A prep product must cost a small fraction of a month's pay. A pass in the ৳500–1,000 range is plausible; ৳2,500+ is a hard sell to most candidates |
| Original design cost ≈ $1 per realtime voice session | At ৳799 (~$6.5) a 10-session realtime-voice pass earns roughly **zero margin** (4% at 70% use, −32% at full use). Realtime voice cannot be the default experience at this price |
| Bootstrapped | No subsidy for a big free tier or a costly voice launch; must reach positive cash flow with early revenue, and every phase must be cheap to run |
| Local payment rails (bKash/Nagad/SSLCommerz-type gateways; recurring billing support unclear) | Sell **prepaid time-boxed passes**, not auto-renewing subscriptions |
| Candidates only | No B2B dependency; but also no institutional distribution. Growth must come from community channels |

## 2. Recommended model: tiered by cost-to-serve

Cheap modes carry the volume; expensive realtime voice is a premium, capped add-on.

| Mode | What it is | Est. cost/session* | Role |
|---|---|---|---|
| **Text interview** | Chat-based interview, resume-tailored, full scorecard | ~$0.10–0.15 | Free tier + bulk of practice |
| **Voice-note interview (async)** | Candidate records each answer in the browser; STT -> evaluation -> interviewer's follow-up in text (questions can be pre-rendered audio and cached) | ~$0.20–0.30 | Spoken-answer practice without realtime cost |
| **Live voice interview (realtime)** | LiveKit + low-latency STT/TTS, interruptions, natural turn-taking | ~$0.60–1.00 | Premium, capped sessions |
*Assumed 30-minute equivalent; unmeasured.

| Plan | Price (assumed) | Includes | Margin at 70% use* |
|---|---|---|---|
| **Free** | ৳0 | Resume analysis + profile, 1 text mini-interview with scorecard, 1 voice-note answer sample | Cost ≈ $0.10–0.15 per signup |
| **Sprint** | **৳799** (~$6.5) / 30 days | ~8 text/voice-note sessions + 2 live voice sessions, tailored to resume/JD, full scorecards, replay, progress, improvement plan | ~57% ($3.7 ≈ ৳455 per pass) |
| **Voice Pro** | **৳1,999** (~$16.4) / 30 days | 8 live voice sessions + unlimited text | ~63% ($10.3 ≈ ৳1,259 per pass) |
| **Top-ups** | ৳199–399 | Extra live-voice sessions / extra text packs | Similar to above |
*Contribution after COGS, payment fee (2.5%), $0.50 fixed per paying user. Full-cap use drops Sprint to ~43% and Voice Pro to ~50%.

Why this works for a bootstrapped BD launch:
- **Low floor price** matches ability to pay; **realtime voice is capped** so cost can't run away.
- **Prepaid passes** work with wallets and gateways that may not support recurring billing; no failed-renewal churn.
- **Gross margin stays above 40%** even at full usage; breakage and time-boxing improve it.
- **Free tier is cheap** (text + resume analysis), so even 3% conversion costs only about **$3.3 (৳407)** per paying user at $0.10 per signup.

Volume view (Sprint, 70% use): 100 passes/month ≈ $373 (≈ ৳45k) contribution; 300 ≈ $1,119 (≈ ৳136k); 1,000 ≈ $3,729 (≈ ৳455k) —
**before** marketing, infrastructure fixed costs, and your time. Use these to size how many monthly paying users make this a viable business.

## 3. Pricing and offer strategy
- **Validate willingness to pay before building voice.** Run a pre-sale/fake-door: landing page + resume analyzer + a ৳499–799 "founding member" offer
  collected by manual bKash/Nagad transfer. Target: enough paid sign-ups to prove demand (e.g. 30–50 within a few weeks) before heavy engineering.
- Test 3 price points (e.g. ৳499 / ৳799 / ৳1,199 for Sprint) with small cohorts; track conversion, usage, refunds.
- **Interview-date plans**: "Interview in 7 days?" -> 7-day intensive pass (৳399–499). Urgency converts and matches behavior.
- **Referral credits** (bootstrapped growth): friend completes a session -> both get a free text session. Community distribution fits BD word-of-mouth.
- **Student/campus codes**: discounts via campus tech clubs and bootcamp communities (informal B2B2C without enterprise sales).
- Avoid USD-priced plans for the core market. Optionally add a **USD tier** for Bangladeshis targeting remote/foreign jobs if international payment collection is solved (separate decision).

## 4. Cost discipline (essential for bootstrapped)
1. **Build order follows cost**: text + voice-note first, realtime voice after revenue is proven (see revised phases in PRODUCT.md).
2. **Cache aggressively**: seed-question audio rendered once and reused; stable phrases pre-rendered; prompt caching for static prefixes.
3. **Shorter agent speech** (cuts TTS cost and sounds more human); per-session TTS character caps.
4. **Model tiering**: small model on conversational turns, stronger model only for scoring; cheaper batch tiers for post-session work.
5. **Per-user cost ledger and hard caps**; free-tier abuse protection (phone/email verification, device limits) because free accounts cost real money.
6. Re-benchmark TTS/STT vendors yearly; consider self-hosting STT only at volume.
7. Offer a **text-only fallback** for low-bandwidth users instead of subsidizing failed voice sessions.

## 5. Metrics that decide viability (instrument from day one)
| Metric | Early target |
|---|---|
| Signup -> first completed session | ≥ 50% |
| Free -> paid conversion | ≥ 4% (kill/redesign signal below 2%) |
| Sprint utilization of cap | 60–80% |
| Gross margin per pass (measured) | ≥ 50% |
| Pre-sale validation | ≥ 30 paying founding members before building realtime voice |
| CAC via community channels | < ৳300–400 per paying user (about 1/2 of a Sprint's contribution) |
| Refund rate | < 5% |
| Score improvement sessions 1→3 | Visible on matched competencies |
| 30-day repeat/top-up | Tracked; target after first cohorts |

## 6. What would change the plan
- Pre-sale shows ৳799 is too high -> lower floor (৳399–499), make Sprint text/voice-note only, keep live voice for Pro.
- Measured live-voice COGS > ~$1.50 -> keep it Pro-only with fewer sessions, or ship voice-note only until costs fall.
- Conversion < 2% -> improve the free experience's proof of quality (sample scorecards, success stories) or tighten the free tier.
- Demand for non-English interviews -> out of scope by decision (interviews and product are strictly English). Revisit only if the user explicitly changes that decision.
- Institutions approach you -> consider cohort licenses (the earlier B2B path) as an optional later channel, not the plan.

## 7. Remaining open questions
1. ~~Interview language~~ **Decided: strictly English.** Speech vendors are chosen on Bangladeshi-accented English only.
2. **Roles for the first release:** keep "junior software engineer", or choose the specific roles/companies Bangladeshi candidates target (e.g. software/QA/data/telecom)?
3. **Entity and payments:** do you have, or can you get, a registered business to open bKash/Nagad/gateway merchant accounts, or start with manual payments for the pre-sale?
4. **Time and team:** how many hours per week can you commit, and are you building alone? It sets how far we can go before revenue.
