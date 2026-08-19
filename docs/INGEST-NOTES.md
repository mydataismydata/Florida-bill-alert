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

This is the highest-value deterministic signal and the mechanism differs by
chamber.

**Senate — HTML, trivial.** Bill text is Word-generated HTML carrying:

```css
.Insert { color: #339933; text-decoration: underline }
.Remove { color: #FF0000; text-decoration: line-through }
```

Additions are `<u class="Insert">`, deletions `<s class="Remove">`.
CS/CS/CS/SB 1452 alone has 1,601 insertions and 977 deletions. The documents
also embed Office metadata (`mso:BillNumber`, `mso:BillFiledDate`,
`mso:BillShortTitle`, `mso:BillVersionName`). **100% of Senate bills** (942/942)
have at least one HTML version.

**House — PDF geometry, recoverable.** Only 1 of 955 House bills has HTML.
Body text is black, so colour is not a signal — only the per-page legend
("words *stricken* are deletions; words <u>underlined</u> are additions") is
coloured. The formatting is drawn as thin rules (`height` ≈ 0.48–0.60) which
`pdfplumber` exposes as `rects`. Classify each rule by where it sits relative
to the characters it overlaps:

| rule position | meaning | measured |
|---|---|---|
| below the baseline | **addition** (underline) | frac ≈ 1.04 |
| through the glyph middle | **deletion** (strikethrough) | frac ≈ 0.53 |

where `frac = (rect.top - char_top) / (char_bottom - char_top)`. The two
clusters are far apart, so a threshold around 0.78 separates them cleanly. A
prototype over four pages of CS/CS/HB 797 recovered 72 additions and 28
deletions with no ambiguous cases.

Two caveats for the real implementation: filter out the page-footer legend
(it is itself underlined and struck), and reconstruct word spacing from
character x-gaps, since naive character joining yields
`attorney-in-factforamember`.
