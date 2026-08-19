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
| Corpus size | **1,897 bills** in the 2026 session (both chambers). |
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

**Two ingestion caveats:**

- Bill *text* is available as HTML → clean extraction, no OCR. Good.
- Bill *analyses* and *vote records* are **PDF-only** → needs a text-extraction
  layer. These are digitally generated PDFs (not scans), so extraction is
  tractable, but layout parsing of the fiscal tables will need real work.

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
citable, and impossible to fake. Ship that before any AI feature. It's also the
thing a legislator can check in ten seconds, which is exactly how you earn the
right to be believed about the harder claims.

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

- Correct SPF, DKIM, **and DMARC** (IONOS handles SPF/DKIM automatically on their
  nameservers; DMARC you set yourself).
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

## 7. Local LLM sizing — MacBook Pro M5 Max, 64GB unified

Confirmed hardware changes the recommendation in two ways.

**vLLM is out.** It is CUDA-first and does not run meaningfully on Metal. The
Apple Silicon stack is **llama.cpp** (Metal backend, `--parallel` slots),
**MLX** (Apple's own framework, batch generation), or **Ollama** as a
convenience wrapper over llama.cpp. Plan on llama.cpp or MLX.

**64GB unified is a lot more headroom than an 8B model needs.** Reserving
~16GB for the OS and everything else still leaves ~48GB for weights and KV
cache. That admits far better models than the 8B originally assumed.

### Model recommendation

**Use a Mixture-of-Experts model, not a dense one.** On Apple Silicon,
generation speed is governed by how many bytes of weights must be read per
token, and MoE models read only their active experts:

| Candidate | 4-bit size | Active params | Relative gen. speed |
|---|---|---|---|
| **Qwen3-30B-A3B (MoE)** | ~17 GB | ~3B | **fastest by a wide margin** |
| Qwen3-32B (dense) | ~18 GB | 32B | ~3–4× slower than the MoE |
| Qwen3-8B (dense) | ~5 GB | 8B | fast, but noticeably weaker output |
| 70B-class (dense, 4-bit) | ~40 GB | 70B | fits, but too slow for 3,000 bills |

The 30B MoE is the sweet spot: roughly 30B-model quality at roughly 3B-model
speed, in 17GB. It should be the default profile, with 8B kept as the
low-end contributor profile and a cloud API as the high-end option.

### The real bottleneck is prefill, not generation

This is the finding that matters most for the schedule. Bills are long inputs
producing moderate outputs, and **prompt processing on Apple Silicon is
relatively weaker than token generation** compared to a datacenter GPU. A
50,000-token bill can spend minutes in prefill before emitting a single token.

Two optimizations follow, and neither is optional:

1. **Reuse the KV cache across passes.** The four analysis passes read the
   *same* bill. Prefill the bill once, then branch each pass off the shared
   prefix. Done naively — five independent prompts — you pay the dominant cost
   five times over. llama.cpp and MLX both support prompt/prefix caching.
   This alone is worth roughly a **5× reduction** in total wall-clock.
2. **Run parallel slots.** Batching amortizes weight reads across concurrent
   sequences, which on a bandwidth-bound machine is close to free throughput.
   Expect roughly 3–4× aggregate over single-stream.

### Revised throughput estimate

With prefix reuse and ~4–8 parallel slots, a full 2026 backfill (~3,000
document-versions × 4 passes + verification) lands in the region of **1–3 days**
of continuous compute. Daily incremental during session is **well under an
hour**. Both need a benchmark spike to firm up — treat these as planning
numbers, not measurements.

**One practical caveat:** this is a laptop. A 48-hour flat-out backfill means
sustained thermal load and a machine you can't really use for anything else.
Schedule backfills overnight or across a weekend, make the pipeline checkpoint
and resume cleanly (it should anyway), and expect to re-run it whenever you
change a prompt — which you will, often.

### Design implications for small models

The open-source goal means contributors will run 3B models on laptops far
weaker than yours. That is a real constraint, not a nice-to-have:

- **Section-aware chunking with map-reduce.** Long bills exceed any practical
  context window; the General Appropriations Act runs to hundreds of pages.
  Chunk on the bill's own section structure, never on arbitrary token counts.
- **Grammar-constrained JSON output** (llama.cpp GBNF, MLX equivalents). Small
  models are unreliable at free-form JSON and completely reliable when the
  decoder cannot emit invalid output. Highest-leverage small-model technique
  there is.
- **A pluggable backend interface** — `analyze(bill) -> AnalysisBundle` — with
  implementations for llama.cpp, MLX, Ollama, and OpenAI-compatible/Anthropic
  APIs. The optional cloud-API path falls out of this for free.
- **Named model profiles** (`tiny-3b`, `local-8b`, `local-30b-moe`, `cloud`)
  varying chunk size, pass count, and self-check depth. A contributor on a 3B
  model should get degraded-but-honest output, and the verification pass should
  *tell* them quality dropped below threshold rather than silently publishing.
- **Publish the eval harness.** With 2026 as ground truth, "is this model good
  enough?" becomes a command anyone can run. That is what makes the
  multi-model promise credible rather than aspirational.

## 8. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Hallucinated provision or cost figure reaches a legislator | **High** | Citation-verification pass; deterministic diff as the headline feature; human review queue before first publish; visible corrections process |
| "Predictive use" section read as imputing bad faith | **High** | Textual-consequence framing only, never imputed intent; distinct labeling; no named individuals in speculative text |
| Email lands in spam / IONOS send cap | **High** | SPF+DKIM+DMARC, double opt-in, throttling, dedicated subdomain; **verify caps with IONOS before building** |
| flsenate.gov changes markup or adds bot blocking | Medium | Contract tests on the scraper; Open States as reconciliation; cache all raw HTML/PDF permanently so re-analysis never needs a re-scrape |
| Session-time volume spike (hundreds of actions/day) | Medium | Batched inference; priority queue (bills with movement first, dead bills last) |
| Small-model output quality is embarrassing | Medium | Eval harness against 2026; quality gate that refuses to publish below threshold |
| Solo-maintainer bus factor during a 60-day session | Medium | Everything reproducible from raw cache; the deterministic layer keeps working even if the AI pipeline is down |
| PDF fiscal-table parsing is harder than it looks | Medium | Scope Phase 1 to extraction of the *narrative* fiscal section first; tables later |
| Subscriber PII on shared hosting | Medium | Minimal data (email + prefs only), encrypted at rest, documented retention, no third-party analytics |

---

## 9. Phased plan

Anchored to the session calendar. Dates are targets, not commitments.

**Phase 0 — Foundations (Sept 2026, ~3 weeks)**
Repo skeleton, license, contribution docs, decision records. Scraper spike against
the **complete 2026 session**: pull all 1,897 bills, text, analyses, votes, history
into a permanent raw cache. Nothing AI yet. *Exit: we can rebuild the whole 2026
corpus from scratch, offline.*

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
2. **Ask IONOS support** two questions: (a) the shared plan's outbound email
   limits per hour and per day; (b) whether outbound SMTP to their own mail
   servers from PHP is rate-limited differently than authenticated SMTP.
3. **Confirm the AI box specs** — GPU model and VRAM. This determines model size,
   batch width, and every throughput number in §7.
4. **Run the scraper spike** against the 2026 session. This is the highest-value
   next action: it either validates §2 completely or surfaces the surprise early,
   while there's still six months of runway.
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
