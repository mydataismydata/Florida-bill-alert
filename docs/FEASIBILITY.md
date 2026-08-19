# Florida Bill Alert — Feasibility Plan

**Status:** draft for discussion · **Date:** 2026-08-19
**License:** MIT · **Analysis host:** MacBook Pro M5 Max, 64GB unified
**Repo:** https://github.com/mydataismydata/Florida-bill-alert

---

## 1. Verdict

**Feasible, and the timing is unusually good.** Nothing in the concept requires
technology that doesn't exist or data that isn't public. The two genuinely hard
parts are not engineering problems:

1. **Editorial credibility.** The audience includes legislators. One hallucinated
   provision or one bogus cost figure in front of that audience costs more than
   every feature gains. This drives the core architectural decision in §4.
2. **Email deliverability** from shared hosting at newsletter volume.

Everything else — ingestion, stage tracking, summarization, the air gap, the
public site — is routine work with well-understood failure modes.

**Timing:** the 2027 Regular Session runs **March 2 – April 30, 2027**. Interim
committee weeks begin **fall 2026**. That is roughly six months of runway, with a
low-stakes dress rehearsal (committee weeks) before the real thing. It also means
the just-completed 2026 session is available as a **complete, finished corpus with
known outcomes** — the ideal evaluation set. Build against 2026, ship for 2027.

---

## 2. Data sources — verified

| Question | Finding |
|---|---|
| Is there an official bulk API? | **No.** Neither chamber publishes a bulk API or bulk export. |
| Is scraping permitted? | **Yes.** `flsenate.gov/robots.txt` is `User-agent: *` / `crawl-delay: 1` — no `Disallow` rules. A polite 1 req/sec crawler is within stated policy. |
| Single source or two? | **Effectively one.** flsenate.gov carries House bills, House bill text, *and* House committee staff analyses (e.g. `/Session/Bill/2025/579/Analyses/h0579z1.HAT.PDF` — the `h` prefix is a House document served from the Senate site). One scraper, not two. |
| Corpus size | **1,897 bills** in the 2026 session (both chambers) — now fully ingested, zero fetch failures. |
| URL patterns | Bill page `/Session/Bill/{year}/{number}`; analyses under `.../Analyses/{doc}.PDF`; special sessions get their own session code (`2026E`). Stable and predictable. |
| Per-bill content | Bill History (action log), Related Bills, **Bill Text in HTML *and* PDF**, Committee Amendments, Floor Amendments, **Bill Analyses (PDF)**, Vote History (committee + floor, PDF), Citations (statutes / constitution / chapter law). |
| Fiscal estimates to cross-reference | Committee staff analyses carry fiscal impact sections; the House uses "Fiscal Analysis & Economic Impact Statement." Local bills require a separate Economic Impact Statement form. Office of Economic & Demographic Research (edr.state.fl.us) publishes the official Revenue Estimating Conference numbers. |
| Copyright | Florida bills and statutes are government edicts — public domain. No licensing obstacle to republishing text. |

**Structured fallbacks / cross-checks** (not primary sources, but valuable for
reconciliation and for a future 50-state generalization):

- **Open States** — free API key, bulk downloads dedicated to the public domain
  (CC-0), typically 1–2 days behind. Free tier is ~10 req/min, ~500/day.
- **LegiScan** — weekly JSON/XML/CSV session snapshots, API key required.

**Recommendation:** scrape flsenate.gov as the system of record; use Open States
bulk as a nightly reconciliation check ("did we miss a bill?"). Never depend on a
third party for the primary path — it's the one thing that can be quietly
deprecated out from under an open-source project.

**The one real ingestion asymmetry** (measured across all 1,897 bills):

| | HTML bill text | PDF only |
|---|---|---|
| **Senate** (942 bills) | **942 — 100%** | 0 |
| **House** (955 bills) | **1 — 0.1%** | 954 |

Senate text is Word-generated HTML with explicit `Insert`/`Remove` markup.
House text is PDF. This matters because the additions/deletions diff (§4) is
the project's credibility backbone, so half the Legislature needs a different
mechanism. It is solved but not free — see §4.

Analyses and vote records are PDF for both chambers. They are digitally
generated, not scanned, so text extraction is tractable; parsing the fiscal
*tables* will still need real work.

Full technical detail is in [INGEST-NOTES.md](INGEST-NOTES.md).

---

## 3. Architecture

Three tiers, with a strictly one-directional flow. The gap you asked for is
enforced by *topology*, not by configuration:

```
  ┌──────────────────────┐      ┌──────────────────────┐      ┌────────────────────┐
  │  INGEST + ANALYZE    │      │   PUBLISH BUNDLE     │      │   PUBLIC SERVER    │
  │  (local AI box)      │      │   (build artifact)   │      │   (IONOS)          │
  │                      │      │                      │      │                    │
  │  scraper → raw store │ ───▶ │  static HTML/JSON    │ ───▶ │  static site       │
  │  deterministic layer │      │  + per-bill PDFs     │ push │  + tiny mail app   │
  │  LLM analysis        │      │  + email payloads    │ only │  + subscriber DB   │
  │  verification        │      │  signed + versioned  │      │                    │
  └──────────────────────┘      └──────────────────────┘      └────────────────────┘
         private, no inbound                                    no model, no inference
```

**The gap.** The public server never initiates a connection to the AI box, never
holds model weights, never runs inference, and has no code path that can trigger
analysis. The AI box pushes a signed, versioned bundle (rsync/SFTP over SSH, key
auth) and disconnects. If the public server is fully compromised, the attacker
gets a static site and a subscriber list — not a model, not the pipeline, not a
foothold into the private network.

**One deliberate exception to think about:** ad-hoc "email me an analysis of HB
1234" requests. These must *not* become a live path into the AI box. Design: the
public server can only serve analyses **that already exist in the published
bundle**. A request for an un-analyzed bill queues a request record, which the AI
box *pulls* on its next scheduled run (pull, never push-to-private), analyzes, and
includes in the next bundle. The user gets "queued — you'll have it within N
hours." This keeps the gap intact and is honestly a better UX than failing.

---

## 4. The core design principle: deterministic first, AI second

This is the most important decision in the project, and it's what makes the
legislator audience viable.

**Do NOT use the LLM for anything a parser can do.** A large fraction of the
product's value is fully deterministic and carries zero hallucination risk:

| Feature | Method | Hallucination risk |
|---|---|---|
| Stage / pathway tracking | State machine over the bill history log | **None** |
| What text is added vs. deleted | Florida bill HTML marks additions (underline) and deletions (strikethrough) — parse it | **None** |
| Which statutes are amended | Citations section + text parsing | **None** |
| Committee referrals, votes, sponsors | Structured scrape | **None** |
| Sponsor's own fiscal claim | Extract from staff analysis fiscal section | **Low** (extraction, not generation) |
| Plain-English summary | LLM | Medium |
| Important provisions | LLM, **with mandatory span citations** | Medium |
| Predictive "how might this be used" | LLM | **High** |
| Independent cost analysis | LLM extraction + deterministic arithmetic | **High if done naively** |

**The strikethrough/underline diff is the credibility backbone.** "This bill
deletes the words *'shall provide public notice'* from §286.011" is verifiable,
citable, and impossible to fake. Ship that before any AI feature. It's the
thing a legislator can check in ten seconds, which is exactly how you earn the
right to be believed about the harder claims.

**Verified, and it works for both chambers — by two different routes.**

*Senate (trivial).* The HTML carries `<u class="Insert">` and
`<s class="Remove">` directly. One CSS selector. CS/CS/CS/SB 1452 alone yields
1,601 additions and 977 deletions.

*House (geometry).* PDF only, and body text is black, so colour is
no help. But Word draws the formatting as thin rules that `pdfplumber` exposes
as rects, and their vertical position cleanly separates the two cases:

| rule position relative to the glyphs | meaning | measured |
|---|---|---|
| below the baseline | **addition** | frac ≈ 1.04 |
| through the middle | **deletion** | frac ≈ 0.53 |

**Both paths are now built and validated.** Because many House bills have an
identical Senate companion, the PDF parser can be checked against HTML ground
truth on the same bill. Across four such pairs, in both directions:
**mean 99.0% agreement, minimum 97.8%**. The residual is genuine drafting
differences between separately-filed companions plus minor tokenisation noise —
no systematic failure. Both paths also carry the legislature's own line numbers,
which is how amendments cite text and how a legislator will check a claim.

Detail and the two bugs that comparison exposed are in
[INGEST-NOTES.md](INGEST-NOTES.md).

**Citation-or-it-didn't-happen.** Every LLM-generated claim must carry a character
span into the source text. A deterministic post-pass then verifies the quoted span
actually exists verbatim in the bill. This single mechanism catches a large share
of hallucinations for near-zero cost, and it works just as well on a 3B model as
on a frontier one. Claims that fail verification get dropped, not published.

**On the cost analysis specifically.** An 8B model doing free-form arithmetic over
a fiscal table will produce confident, wrong numbers. Restructure it:

1. LLM **extracts** the cost *structure* (what's the unit? who pays? how many
   units? over what period?) and the sponsor's claimed figure — extraction, with
   citations, which small models do well.
2. A **human-authored, transparent calculation model** in ordinary code does the
   arithmetic. Published, auditable, in the repo.
3. Output a **range with stated assumptions**, not a point estimate — "if the
   caseload assumption of X is wrong by 2×, the cost is Y."
4. The headline framing should be **discrepancy detection**, not rival estimation:
   *"The sponsor's analysis assumes 4,000 affected cases. The bill's own
   eligibility language appears to cover roughly 40,000."* That is a defensible,
   checkable claim and it's far more useful to an activist than a competing
   dollar figure nobody can verify.

**On the predictive feature.** This is the highest-risk item in the project. It is
also probably the most valuable, so the answer is discipline, not deletion:

- Present it in a visually distinct, unmistakably labeled section — AI-generated,
  speculative, not a factual claim about anyone's intent.
- Ground every scenario in specific bill language ("§4 grants the agency authority
  to X without the notice requirement in current law; that authority could be
  used to…").
- Talk about **what the text permits**, never about what a sponsor *wants*.
  Textual consequence is defensible; imputed motive is not, and the distinction
  matters legally as well as editorially.
- Ship a visible correction/feedback mechanism from day one.

---

## 5. Hosting: shared vs. VPS

**Recommendation: IONOS shared hosting, with the AI box doing all generation.**
This works, it's cheaper, it gets the unlimited disk, and it *strengthens* the air
gap by leaving the public server with almost no logic to compromise.

Verified IONOS shared-hosting constraints:

- **Cron: max 60 seconds per invocation, 5-minute minimum interval, up to 500 jobs.**
  This is the binding constraint. It rules out long-running processes entirely.
- PHP 8.x and MySQL available.
- SPF and DKIM are auto-configured when IONOS is both host and DNS provider.

What this implies:

- The public site is **static HTML generated on the AI box**. Bill pages, search
  index, stage timelines, per-bill PDFs — all pre-rendered. Fast, cheap,
  unlimited-disk-friendly, and nothing to exploit.
- The only dynamic code is a small PHP app: subscribe / confirm / unsubscribe,
  ad-hoc request intake, and a **chunked, resumable email queue worker** that
  sends N messages per 5-minute cron tick and exits well under 60 seconds. This
  is a normal pattern, but it must be designed for it from the start — a naive
  "send the whole digest in one cron run" will silently truncate.
- Client-side search over a prebuilt index handles a 2,000-bill corpus fine
  without a database.

**Move to the VPS if** you hit sending limits, want server-side full-text search
over multiple sessions, or add real user accounts. Design the PHP layer thin
enough that this migration is a weekend, not a rewrite.

---

## 6. Email

The two email products are different and should be built in this order:

1. **Scheduled digests** (subscribed) — batch, predictable volume, generated as
   payloads on the AI box and queued on the public server.
2. **Ad-hoc single-bill analysis** — on request, served from the published bundle;
   queued to the AI box if not yet analyzed (see §3).

**Deliverability is the real risk, and it's worth taking seriously.** Sending
newsletter-volume mail from shared hosting is where projects like this quietly
die — messages land in spam and nobody tells you. Non-negotiables:

- Confirm **DMARC** is set alongside the SPF/DKIM records IONOS configures
  automatically — a spam-checker pass usually reflects SPF and DKIM, and DMARC
  is the one that tends to be missing.
- **Double opt-in.** Not optional — for deliverability, for CAN-SPAM, and because
  the subscriber list is the project's real asset.
- One-click unsubscribe (`List-Unsubscribe` header), plaintext alternative,
  throttled sending, bounce and complaint handling.
- Consider a dedicated sending subdomain so reputation damage can't contaminate
  your primary domain's mail.

**Must verify before committing:** IONOS's actual per-hour / per-day send limits
for the shared plan. Public documentation doesn't state them clearly — this needs
a direct answer from IONOS support. If the cap is low (some shared hosts sit at a
few hundred per hour), the 5-minute cron cadence gives roughly 288 ticks/day to
work with, which is workable for a list in the low thousands but not beyond.

---

## 7. Local model — MLX on the M5 Max

Runtime is **MLX**, not Ollama. Ollama stays installed as a second backend for
checking that a prompt is not overfitted to one runtime.

### Model

**`mlx-community/Qwen3.8-27B-4bit`** — dense, Apache-2.0, vision-capable,
16.1 GB on disk. On 64 GB of unified memory that leaves ample headroom for the
large KV cache long bills demand.

Note it needs **`mlx-vlm`**, not `mlx-lm` — it is a vision-language model, and
that catches people out. Full instructions in [SETUP.md](SETUP.md).

Two properties shape the design:

**Dense, not mixture-of-experts.** Every token reads all ~16 GB of weights, so
generation is bandwidth-bound and slower than an MoE of similar size. The
trade is quality: Qwen3.8-27B scores far above the architecturally comparable
3.6 generation. Start here — quality matters more than speed for this
workload, and the backfill is chunked anyway. If throughput ever binds before
quality does, benchmark an MoE (the Qwen3.6-35B-A3B line activates ~3B
parameters per token) and switch by changing a profile name.

**Vision, which this project can actually use.** Two of the hardest ingest
problems are secretly vision problems: House bill text is PDF-only, and the
fiscal tables inside staff analyses resist text extraction. A VLM reads a
rendered page directly. Use it strictly as a fallback and cross-check — the
geometric PDF parser (§4) is deterministic and cheap, and determinism is the
entire point of the diff layer. Vision is for what geometry cannot reach.

### Backfill is chunked, never a marathon

The operating model is short runs, not multi-day ones. Both the ingest and
analysis stages are resumable and skip completed work, so `--limit` is a clean
stopping point and the machine stays usable.

**Prioritisation matters more than raw throughput here.** Measured on 2026:

- **1,233 of 1,897 bills (65%) never advanced past their filed version.** One
  short document, one pass, no amendments, no committee substitutes.
- The ~664 that moved carry nearly all the versions, analyses, votes, and
  amendments — and all of the public interest.

So `--order activity` front-loads the expensive-and-valuable bills. Most of the
useful output lands in the first fraction of the compute, and the long tail of
dead bills can trickle in over following nights. That reorders the schedule
from "wait days for anything" to "useful corpus tonight."

Daily incremental during session is small enough not to matter — a peak
committee day is tens of bills, not hundreds.

Two optimisations still carry their weight and should be built in early:

1. **Reuse the KV cache across passes.** The analysis passes read the *same*
   bill. Prefill once, branch each pass off the shared prefix. Done naively —
   five independent prompts — you pay the dominant cost five times. On long
   inputs this is the single biggest win available.
2. **Batch where possible.** Concurrency amortises weight reads, which on a
   bandwidth-bound machine is close to free throughput.

Firm numbers need a benchmark spike; treat the above as shape, not measurement.

### Portability

The analysis box may not stay this machine, and may end up *less* capable.
That makes the backend abstraction load-bearing rather than decorative:
everything model-specific sits behind `analyze(bill) -> AnalysisBundle`, with
named profiles (`tiny-3b`, `local-27b`, `cloud`) selecting model, chunk size,
and pass count. Moving to a weaker box should mean changing a profile name.

The same mechanism serves the open-source goal — contributors will run small
models on modest hardware — so it is worth building properly the first time:

- **Section-aware chunking with map-reduce**, on the bill's own section
  structure rather than arbitrary token counts.
- **Grammar-constrained JSON output.** Small models are unreliable at
  free-form JSON and completely reliable when the decoder cannot emit invalid
  output.
- **Quality gates that fail loudly.** A contributor on a 3B model should get
  degraded-but-honest output, and the verification pass should say quality
  dropped below threshold rather than publish silently.
- **A published eval harness.** With 2026 as ground truth, "is this model good
  enough?" becomes a command anyone can run.

## 8. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Hallucinated provision or cost figure reaches a legislator | **High** | Citation-verification pass; deterministic diff as the headline feature; human review queue before first publish; visible corrections process |
| "Predictive use" section read as imputing bad faith | **High** | Textual-consequence framing only, never imputed intent; distinct labeling; no named individuals in speculative text |
| Email lands in spam | Low | No send caps; outgoing mail already scores well against a spam checker. Confirm DMARC, keep double opt-in |
| flsenate.gov changes markup or adds bot blocking | Medium | Contract tests on the scraper; Open States as reconciliation; cache all raw HTML/PDF permanently so re-analysis never needs a re-scrape |
| Session-time volume spike (hundreds of actions/day) | Medium | Batched inference; priority queue (bills with movement first, dead bills last) |
| Small-model output quality is embarrassing | Medium | Eval harness against 2026; quality gate that refuses to publish below threshold |
| Solo-maintainer bus factor during a 60-day session | Medium | Everything reproducible from raw cache; the deterministic layer keeps working even if the AI pipeline is down |
| Analysis box changes to weaker hardware later | Low | Backend abstraction plus named model profiles; switching should be a config change, and the eval harness says what quality was lost |
| PDF fiscal-table parsing is harder than it looks | Medium | Scope Phase 1 to extraction of the *narrative* fiscal section first; tables later |
| House diff extraction rots if the House changes its PDF generator | Medium | Golden-file tests over known 2026 House bills; the classifier is geometric, so drift shows up as unclassified rules rather than silent mislabelling — fail loudly |
| Subscriber PII on shared hosting | Medium | Minimal data (email + prefs only), encrypted at rest, documented retention, no third-party analytics |

---

## 9. Phased plan

Anchored to the session calendar. Dates are targets, not commitments.

**Phase 0 — Foundations — *substantially done, Aug 2026***
Repo, MIT license, ingest pipeline, and a full pull of the 2026 session: 1,897
bills with history, text versions, analyses, votes, amendments, citations and
companions, zero fetch failures, all cached permanently on disk. *Exit criterion
met: the corpus rebuilds from cache, offline.* Remaining: contribution docs and
decision records.

**Phase 1 — The deterministic product (Oct 2026, ~4 weeks)**
Stage/pathway state machine. Strikethrough/underline diff parser. Statute
cross-references. Sponsor fiscal-claim extraction. Static site generator.
*Exit: a genuinely useful bill tracker with zero AI in it.* This is the checkpoint
that de-risks everything — if the project stopped here it would still be worth
publishing.

**Phase 2 — AI analysis (Oct–Nov 2026, ~5 weeks, overlaps fall committee weeks)**
Backend abstraction, chunking, constrained decoding, the four analysis passes, the
citation-verification pass, and the eval harness against 2026 ground truth. Run it
live against real bills as they're filed for 2027 — low stakes, real feedback.
*Exit: measurable quality on a held-out set of 2026 bills.*

**Phase 3 — Publishing + email (Nov–Dec 2026, ~4 weeks)**
Bundle format, signed push to IONOS, PHP subscribe/queue/send layer, double
opt-in, digest templates, ad-hoc request flow. *Exit: end-to-end from scrape to
inbox.*

**Phase 4 — Harden and launch (Jan–Feb 2027)**
Load-test at session volume. Human review workflow. Corrections process. Soft
launch to a small activist group for feedback before going wide. *Exit: live and
stable before March 2, 2027.*

**Phase 5 — Session operations (Mar–Apr 2027)**
Run it. Feature freeze; fix only what breaks. Collect everything for the
post-session retrospective.

**Future — county level.** Explicitly out of scope until after the 2027 session.
Worth noting now only because it argues for keeping the source-adapter layer
separate from the analysis layer, which costs nothing to do from the start.

---

## 10. Immediate next steps

1. **Initialize the repo** with the `Data Mine <data.mine@mail.com>` identity set
   repo-locally, plus README, license, and this document.
2. ~~Ask IONOS about send limits.~~ **Resolved:** no caps, list under 100,
   spam scoring already verified. Only DMARC left to confirm.
3. ~~Confirm the AI box specs.~~ **Resolved:** M5 Max, 64 GB, MLX, Qwen3.8-27B.
4. ~~Run the scraper spike.~~ **Done.** All 1,897 bills of the 2026 session
   ingested with zero fetch failures; findings in [INGEST-NOTES.md](INGEST-NOTES.md).
5. ~~Build the House PDF diff parser.~~ **Done**, validated at 99.0% against
   identical Senate companions.
6. **Build the stage/pathway tracker** — a state machine over the history log.
   With the diff layer done, that completes the deterministic product (Phase 1):
   a genuinely useful bill tracker with no AI in it at all.
5. **Pick a license.** AGPL-3.0 if you want derivative *hosted* versions to stay
   open (relevant — someone will fork this for another state); MIT/Apache-2.0 for
   maximum adoption. This is a values call, not a technical one.

---

## 11. Open questions

- **Editorial posture:** does anything publish automatically, or does a human
  approve the first N analyses? Recommendation: human-in-the-loop for the first
  session, automated after, with the predictive section gated longer than the rest.
- **Scope of "every bill":** all 1,897, or filter to substantive general bills
  (excluding claim/relief bills, which are numerous and narrow)? Affects compute
  budget significantly.
- **Digest cadence and segmentation:** daily during session / weekly otherwise?
  Subscribe by topic, by committee, by sponsor, by county delegation?
- **Do you want per-bill one-page PDFs?** For the legislator audience specifically,
  a one-page brief that looks like the staff analyses they already read may be the
  single highest-adoption artifact in the project.
- **Attribution and transparency:** how prominently to disclose AI generation.
  Recommendation: very — it's a credibility asset with this audience, not a
  liability.
