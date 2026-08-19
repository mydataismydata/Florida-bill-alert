# Ingest notes — flsenate.gov

Verified against the complete 2026 Regular Session (August 2026). These are the
concrete details behind the claims in [FEASIBILITY.md](FEASIBILITY.md).

## Crawl policy

`robots.txt` is `User-agent: *` / `crawl-delay: 1` with no `Disallow` rules.
The crawler identifies itself, uses one connection, and holds to one request per
second. Measured throughput: 0.96 req/s.

## Coverage

The Senate site carries documents for **both** chambers, so it is the only
source needed for bills, text, and staff analyses.

| | 2026 session |
|---|---|
| Bills enumerated | **1,897** — exactly the count the site reports |
| Fetch failures | 0 |
| Senate / House | 942 / 955 (plus resolutions, memorials, SPB) |
| Became law | 228 (**12%**) |
| History events | 15,480 |
| Text versions | 4,632 rows / 3,127 distinct versions |
| Staff analyses | 3,029 |
| Committee + floor votes | 1,707 |
| Amendments | 1,610, of which **224 are Delete All** strike-alls |
| Statute/constitution citations | 8,931 |
| Companion-bill links | 2,702 |

Most bills never move: 1,233 of 1,897 have exactly one text version. Only 143
reach four or more versions.

## URL patterns

```
/Session/Bills/{session}?Chamber=both&PageNumber={n}   50 rows/page
/Session/Bill/{session}/{num}                          bill page
/Session/Bill/{session}/{num}/BillText/{version}/HTML  Senate text
/Session/Bill/{session}/{num}/BillText/{version}/PDF   House + Senate text
/Session/Bill/{session}/{num}/Analyses/{file}.PDF      both chambers
/Session/Bill/{session}/{num}/Vote/{file}.PDF          contains literal spaces
```

`{num}` is a single number space shared by both chambers, and it is unique per
bill — `(session, num)` is a safe primary key. Special sessions use their own
session code (`2026E`). Version slugs seen: `Filed`, `c1`, `c2`, `er`, `e1`.

Requesting `/BillText/Filed/HTML` for a House bill returns **HTTP 200 with the
site's chrome**, not the bill. Treat it as a soft 404 — check for the markup
below, never for the status code.

## Page structure

Every tab is a `div.tabbody` with a stable id — `tabBodyBillHistory`,
`tabBodyRelatedBills`, `tabBodyBillText`, `tabBodyAmendments`,
`tabBodyAnalyses`, `tabBodyVoteHistory`, `tabBodyCitations` — containing plain
`table.tbl` elements with `thead`/`tbody`. No JavaScript is required.

Parser traps found the hard way:

- **Committee-substitute prefixes.** Labels become `CS/HB 33`, `CS/CS/SB 290`,
  `CS/CS/CS/SB 1452` as a bill advances. Strip `^(?:CS/)+` before deriving the
  chamber, or detection fails on exactly the bills that matter most.
- **Sponsor markup differs by chamber.** Senate sponsors are `<a>` links to
  `/Senators/{id}`; House sponsors are bare text. Both render as
  `<SUBJECT> by A; B; (CO-INTRODUCERS) C; D`. Committee bills name a committee
  as sponsor (e.g. "Budget Committee").
- **Only Senate committee references appear**, even on House bills. House
  referrals must be recovered from the history log ("Referred to ...") or from
  the House's own site.
- **Amendment tables** live in `#CommitteeAmendmentASC` / `#FloorAmendmentASC`,
  keyed by a `<caption>` naming the bill version. The first cell is
  `{number} - Amendment` or `{number} - Amendment (Delete All)`. Strike-alls
  replace a bill's entire content and must be treated as a major event.
- **Chapter law** appears in the snapshot as `Chapter No. NNNN-NN` linking to
  `laws.flrules.org`.

## Additions and deletions — the diff layer

**Built and validated.** `flba.diff` produces the same `Segment` list for both
chambers, so everything downstream is chamber-agnostic:

```bash
flba --session 2026 diff 1277           # House, via PDF geometry
flba --session 2026 diff 552 --json     # Senate, via HTML
```

### Senate — HTML

Bill text is Word-generated HTML carrying:

```css
.Insert { color: #339933; text-decoration: underline }
.Remove { color: #FF0000; text-decoration: line-through }
```

**Do not collect whole `<u>`/`<s>` elements.** The generator splits runs
mid-word across adjacent tags, so joining elements with a space produces
`applica bility` and `department s`. Walk text nodes character-by-character and
run-length encode instead. 100% of Senate bills (942/942) have HTML.

### House — PDF geometry

Only 1 of 955 House bills has HTML. Body text is black, so colour is no signal —
only the per-page legend is coloured. The formatting is drawn as thin rules
(`height` 0.48–0.60) that `pdfplumber` exposes as `rects`. Classify each rule by
where it sits relative to the words it covers:

| rule position | meaning | measured |
|---|---|---|
| below the baseline | **addition** (underline) | frac ≈ 1.04 |
| through the glyph middle | **deletion** (strikethrough) | frac ≈ 0.53 |

where `frac = (rect.top - word.top) / (word.bottom - word.top)`. The clusters
are far apart; the threshold sits at 0.78. Classify at *word* level with a
coverage test rather than per character — it gets word spacing right for free.

Page furniture must be dropped by vertical position (body runs roughly
`120 < top < 650`). The footer legend — "words stricken are deletions; words
underlined are additions" — is itself struck and underlined, so leaving it in
adds a false hit on every page.

### Line numbers

Both paths carry the legislature's own line numbers, which is how amendments
reference text ("between lines 17 and 18, insert"). In the PDF they are the
leading digits in the left margin (x < 60); in the HTML they lead each line
inside the `<pre>`. Neither should end up in the extracted words.

### Validation

Many House bills have an **identical Senate companion**, which gives real
ground truth: parse the House PDF and the Senate HTML of the same bill and
compare. Across four such pairs, both directions:

| pair | additions | deletions |
|---|---|---|
| HB 315 / SB 328 | 99.4% | 99.3% |
| HB 963 / SB 320 | 98.7% | 99.3% |
| HB 1277 / SB 552 | 98.1% | 99.8% |
| HB 1419 / SB 1598 | 99.6% | 97.8% |

**Mean 99.0%, minimum 97.8%.** The residual is a mix of genuine drafting
differences between separately-filed companions and minor tokenisation noise;
no systematic failure remains.

Two fixes found by that comparison, both of which had been silently wrong:

- Typographic punctuation. The HTML uses U+2019 while the PDF yields ASCII, so
  `applicant’s` and `applicant's` tokenised differently. Both are normalised.
- Line-wrapped hyphens. The PDF breaks `end-of-course` as `end-of-` + `course`.
  Legislative drafting does not hyphenate to wrap, so the hyphen is real and is
  kept when stitching — dropping it yields `end-ofcourse`.

`tests/test_diff.py` covers the geometry with synthetic pages (runs anywhere)
and the corpus comparison as a regression guard (skips without a raw cache).

## Stage tracking — the pathway layer

`flba.stages` folds a bill's history into **two** values, because one is not
enough: **stage** is how far it travelled, **outcome** is what became of it. A
bill can reach the Governor and be vetoed, or die in its first committee while
its identical companion becomes law. No single progress number says that.

```bash
flba --session 2026 track 1452            # the pathway for one bill
flba --session 2026 track --summary       # distribution across the session
```

### History cells hold several actions

Each action is rendered `&bull; <action><br>`, and one dated cell often holds a
whole floor sequence (`Read 2nd time • Added to Third Reading Calendar • Read
3rd time • Passed`). Storing the cell as one string loses the sequence: doing so
undercounted the 2026 session by **10,953 events — 15,480 instead of 26,433**.

### Three ways the vocabulary misleads

Every one of these was a bug before it was a test.

**"Laid on Table under Rule 7.18(a)" is not a death — it is a substitution.**
When a committee adopts a substitute, the version it replaces is tabled and the
CS is filed in its place:

```
Favorable with CS by Criminal Justice Subcommittee
Reported out of Criminal Justice Subcommittee
Laid on Table under Rule 7.18(a)      <- the superseded version
CS Filed                               <- its replacement
Referred to Judiciary Committee        <- and onward
```

313 bills have this in their history and **107 of them became law**. Reading it
as death is the most damaging error this layer could make.

**"Withdrawn from <committee>" is not a death — it is acceleration.** It pulls
a bill out of committee straight onto the calendar; in 2026 it was followed by
"Placed on Calendar" 157 times out of 164. Only *"Withdrawn prior to
introduction"* and *"Withdrawn from further consideration"* end a bill.

**"Died ... companion bill(s) passed, see X" means the policy was enacted.**
The twin bill carried it. 294 bills — **15.5% of the session** — land here.
Reporting them as plain failures would misinform exactly the reader this
project exists to serve.

### Not everything is a bill

Joint resolutions (HJR/SJR) propose constitutional amendments and go to the
**voters**, never to the Governor. Concurrent resolutions and memorials are
filed with the Secretary of State. Simple resolutions bind only the chamber
that adopts them. Scoring these against "did it become law?" misreports every
one, so they get their own ladder ending in adoption, and `kind_of()` reads the
type through any `CS/` prefixes.

### Validation

Classifying all 1,897 bills **from history alone**, with the chapter-law column
withheld so the state machine cannot cheat:

| outcome | count | share |
|---|---|---|
| Died | 1,274 | 67.2% |
| Superseded — companion passed | 294 | 15.5% |
| Became law | 228 | 12.0% |
| Adopted (resolutions) | 92 | 4.8% |
| Vetoed | 8 | 0.4% |
| Still moving | 1 | 0.1% |

**228 classified as enacted — exactly the 228 bills holding a chapter law, with
zero misclassifications in either direction.**

The single "still moving" bill is SPB 7042, which was temporarily postponed in
committee and has no terminal action on the record at all. Reporting it as
pending is correct: the tracker states what the record says rather than
inferring a death the Legislature never wrote down.

## Statute cross-references

`flba.statutes` answers which statutes a bill operates on, what it does to
each, and how much text it moves — then inverts that into "who is touching this
statute this session?"

```bash
flba --session 2026 crossref                    # build the index
flba --session 2026 statutes 1452               # what one bill touches
flba --session 2026 statutes --statute 119.071  # who touches one statute
```

Two independent sources are combined. The **formal title** is legally required
to say what the bill does (`amending s. 215.422, F.S.; creating s. 497.1411,
F.S.`), and the **numbered sections** in the body then do it:

```
Section 1. Subsection (15) of section 215.422, Florida Statutes, is amended to read:
Section 7. Section 497.1411, Florida Statutes, is created to read:
Section 3. Subsection (16) is added to section 493.6102, Florida Statutes, to read:
Section 9. Section 322.27, Florida Statutes, is repealed.
```

Pairing those with the additions/deletions from `flba.diff` produces the claim
this layer exists for — *amends s. 215.5586 at line 292, adding 324 words and
deleting 173* — every part of which the reader can check against the bill.

Action verbs observed, in frequency order: amended, created, added,
redesignated, reenacted, renumbered, reordered, transferred, directed. `added`
is normalised to *amend* (a subsection added to an existing statute), and the
redesignate/renumber family to *renumber*.

### Traps

**A chapter can be operated on wholesale.** `Chapter 205, Florida Statutes,
consisting of ss. 205.013, 205.022, ..., is repealed.` Matching only
`section N, Florida Statutes` reports a repeal of forty statutes as a repeal of
none.

**Subsection markers are not statutes.** `reenacting s. 493.6201(4), F.S.`
names one statute. Harvesting every number in the clause invents a phantom
`s. 4`. Parentheticals are stripped first, and a section number must contain a
dot.

**Title clauses do not always end in "F.S."** A long reenactment list often
runs straight into `relating to ...`. Requiring the `F.S.` terminator silently
dropped whole enumerations — one bill lost 7 of its 11 statutes that way.

### Validation

The site's own Citations tab is an independent source, so extraction can be
checked against it. Across **558 bills / 2,771 listed citations**:

| | |
|---|---|
| Recall | **98.7%** |
| False positives | **0** |
| Bills below 90% recall | 3 |

One methodological note that cost an apparent 12 points: the Citations tab
reflects a bill's **latest** version, so comparing it against a parse of the
*Filed* version compares two different documents. Measured against the matching
version, recall went from 86.1% to 98.4%, and the remaining fixes took it to
98.7%.

### What the index shows

Most-touched statutes of the 2026 session:

| statute | bills | subject |
|---|---|---|
| s. 921.0022 | 27 | Criminal Punishment Code offense severity ranking |
| s. 119.071 | 23 | Public records exemptions |
| s. 1001.42 | 22 | School district powers and duties |
| s. 212.08 | 13 | Sales tax exemptions |
| s. 627.351 | 13 | Insurance risk apportionment |

That reverse index is a product feature in itself: *"what is happening to the
public records law this session?"* is one query, and it is exactly the question
an activist or a reporter arrives with.
