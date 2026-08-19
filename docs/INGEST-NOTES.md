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
