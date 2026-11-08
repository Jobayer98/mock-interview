# 06 — Bangladesh Market: Constraints and Product Adaptations

Evidence quality: mostly third-party blogs, vendor-run benchmarks, and law-firm summaries found by web search on 2026-11-08.
Everything here is a **hypothesis to verify**, especially payments, legal, and connectivity.

## 1. Language and speech

**DECIDED: interviews are strictly English. No Bangla or code-mixed support.** This removes Bengali STT/TTS from scope and
makes accent robustness (Bangladeshi-accented English) the speech requirement. The product UI is English too. Language of marketing, support and legal notices is an OPEN QUESTION (ask the user; ask the lawyer whether consent notices must also be in Bangla).
The table below keeps the earlier Bengali findings for reference; only the English-accent rows still apply.

| Issue | What we know | Adaptation |
|---|---|---|
| Interview language | Unknown for your users: English, Bangla, or code-mixed. Many local tech interviews likely mix both [hypothesis] | Decide with user research (interview 10 target candidates). Design prompts and STT for English first with Bangla UI; add code-mixed support as a P2 feature if demanded |
| STT accuracy for Bengali | Vendor-run benchmarks conflict wildly: Soniox's test shows ~1.4–3.2% WER across Speechmatics/AWS/Deepgram/Google/ElevenLabs ([Soniox](https://soniox.com/media/SonioxSTTBenchmarks2025.pdf)), while ElevenLabs' own page reports Scribe v1 at 8.1% and Deepgram Nova 2 at 100% ([ElevenLabs](https://elevenlabs.io/speech-to-text/bengali)); a contact-centre test reports 18–30% on phone-quality audio ([Convozen](https://convozen.ai/products/bengali-speech-to-text)) | **Never trust vendor claims.** Build a 50-clip test set of real Bangladeshi-accented English and Bangla/Banglish answers on real phones/laptop mics, then blind-test 3–4 engines. This is spike S4 and now blocks the voice choice |
| Bangladeshi-accented English | No independent benchmark found | Same test set; include accent-robust vocabulary boosting for technical terms |
| TTS (interviewer voice) | Not researched for Bengali; English voices are mature | Start with English interviewer voice; evaluate Bengali TTS naturalness only if Bangla interviews are in scope |
| Grading in the presence of STT errors | STT errors can unfairly lower scores | Show the transcript for the user to correct; low-confidence segments flagged and excluded from delivery metrics; never penalize accent |

## 2. Payments

- Local wallets and gateways (bKash, Nagad, SSLCommerz-type aggregators) are the realistic rails. Merchant onboarding requires business accounts/keys and live-store approval; docs say bKash supports refunds and recurring support at SSLCommerz is **reported inconsistent** across sources ([overview](https://nowpayments.io/blog/payment-gateway-banglsdesh), third-party).
- Implication: **prepaid, time-boxed passes** (one payment, 30 days) instead of auto-renewing subscriptions.
- Early stage: accept **manual wallet transfers with a transaction-ID check** for the pre-sale, automate only after demand is proven.
- Whether international processors can serve a Bangladeshi-registered business was not verified; **assume local rails only** and check before planning USD pricing.
- Price display in BDT, mobile-wallet-first checkout, clear receipts; refunds only for technical failures.

## 3. Connectivity and devices (no Ookla/device data found; design defensively)

- Assume many users are on **mobile networks and variable Wi-Fi**, and many on phones or older laptops [hypothesis].
- **Mobile-web first**, light pages, PWA; no mandatory video. Audio at low bitrate; automatic fallback ladder: live voice -> **voice-note (async) mode** -> text.
- Voice-note mode tolerates drops: recording uploads in chunks and resumes; no live connection needed during answers.
- Reconnect/resume for live sessions (30–60 s); show connection quality before starting; one-click switch to text.
- Data-light assets, offline-friendly dashboards; avoid avatar video entirely at launch.

## 4. Data protection and legal (needs a local lawyer)

- Bangladesh's Personal Data Protection Ordinance 2025 was approved and reportedly came into force November 2025 ([Daily Star](https://www.thedailystar.net/tech-startup/news/bangladeshs-personal-data-protection-ordinance-2025-key-takeaways-4015401);
  [law-firm summary](https://mahbub-law.com/key-highlights-of-the-personal-data-protection-ordinance-2025-for-businesses)). Sources describe **cross-border transfer limits**
  (equivalent protection required, **separate consent** for cross-border transfers, regulator power to suspend transfers) and **localisation for certain restricted/confidential data**;
  details are inconsistent across sources and a 2026 amendment is referenced ([CCIA](https://ccianet.org/wp-content/uploads/2026/04/CCIA-Views-on-Bangladeshs-Personal-Data-Protection-Ordinance.pdf)).
- Why it matters here: resumes, voice, and transcripts sent to US-hosted LLM/STT/TTS vendors are personal data crossing borders.
- Mitigations to design in now: **explicit, separate consent screen for processing outside Bangladesh**; data minimization (redact name/phone/email before LLM calls); vendor no-training/zero-retention terms;
  user-controlled deletion; transcript-only by default; English privacy policy (confirm with a lawyer whether a Bangla version is legally required); record of processing. **Get legal advice before taking payments at scale.**
- Candidates in the EU/UK later would add GDPR and the EU AI Act points in doc 04; not a launch concern for a Bangladesh-only product but keep emotion inference out regardless.
- Also check: business registration, tax on digital services/VAT, and consumer-protection rules for digital passes and refunds.

## 5. Go-to-market (bootstrapped, candidates direct)

Hypotheses to test, not facts:
- **Community-led distribution**: Bangladeshi developer/job-seeker Facebook groups, university CS clubs, bootcamp alumni groups, YouTube/TikTok career creators [verify channel mix].
- **Free resume analyzer as the hook** (shareable, cheap, valuable alone) -> funnel to a free text interview -> Sprint.
- **Content that ranks**: English guides to common interview questions for local company types; each guide links to a practice session (marketing language is an open question).
- **Referral credits** and campus ambassador codes instead of paid ads at first.
- **Social proof**: before/after score improvements (with consent), real candidates' offers (only genuine).
- Candidates' real pain points to confirm in 10 customer interviews: nervousness speaking English, no one to practice with, unclear what companies ask, resume quality.

## 6. Product adaptations summary

| Area | Adaptation |
|---|---|
| Launch experience | English-only interviews and UI, mobile-web first |
| Modes | Text and voice-note first; realtime voice as capped premium |
| Content | Question seeds reflecting questions Bangladeshi employers ask (to be sourced from local candidates/engineers) |
| Pricing | BDT, prepaid passes, wallet checkout |
| Voice stack choice | Decided by our own accent test set, not vendor claims |
| Compliance | Separate cross-border consent, data minimization, deletion controls, local legal review |
| Growth | Community channels, free analyzer, referrals, campus codes |
