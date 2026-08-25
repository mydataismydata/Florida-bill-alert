"""Tests for the static site build.

Builds a small real site into a temp directory and checks the output, rather
than asserting on template strings.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DB = ROOT / "data" / "index.sqlite"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="no ingested corpus")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from flba.site import build
    out = tmp_path_factory.mktemp("site")
    stats = build(DB, out, "2026", built="2026-01-01", limit=40)
    return out, stats


SUBPAGES = ("-text", "-prompt")


def test_it_writes_a_summary_and_a_full_text_page_per_bill(built):
    out, stats = built
    assert stats["bills"] == 40
    summaries = [p for p in (out / "bills").glob("*.html")
                 if not p.stem.endswith(SUBPAGES)]
    assert len(summaries) == 40
    # a full-text page exists wherever the bill's text was cached
    assert list((out / "bills").glob("*-text.html"))


def test_a_prompt_page_accompanies_every_bill_that_has_text(built):
    """What the model is asked is published beside what it answered."""
    out, _ = built
    texts = {p.stem[: -len("-text")] for p in (out / "bills").glob("*-text.html")}
    prompts = {p.stem[: -len("-prompt")] for p in (out / "bills").glob("*-prompt.html")}
    assert texts and texts == prompts


def test_the_prompt_page_shows_the_prompt_actually_sent(built):
    out, _ = built
    page = next((out / "bills").glob("*-prompt.html")).read_text(encoding="utf-8")
    from flba.analysis import passes as P
    # the system message is reproduced, not paraphrased
    for line in P.SYSTEM.splitlines():
        if len(line) > 40:
            assert escape(line) in page, line[:60]
    for name in P.ORDER:
        assert f"TASK {name}" in page


def escape(text):
    from markupsafe import escape as e
    return str(e(text))


def test_a_plain_build_carries_no_operator_controls(built):
    """The public host has no model and no pipeline, so a default build must
    not ship a button that runs one."""
    out, _ = built
    assert not (out / ".local-build").exists()
    for page in out.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert "_reanalyze" not in text, page
        assert 'class="repull' not in text, page


def test_a_local_build_carries_them_and_is_marked(tmp_path_factory):
    from flba.site import build
    out = tmp_path_factory.mktemp("localsite")
    build(DB, out, "2026", built="2026-01-01", limit=40, local=True)
    assert (out / ".local-build").exists()
    pages = [p for p in (out / "bills").glob("*.html")
             if not p.stem.endswith(SUBPAGES)]
    assert any("_reanalyze" in p.read_text(encoding="utf-8") for p in pages)


def test_no_template_syntax_escapes_into_the_output(built):
    out, _ = built
    for page in out.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert "{{" not in text and "{%" not in text, page


def test_every_internal_link_resolves(built):
    from bs4 import BeautifulSoup
    from urllib.parse import urlparse
    out, _ = built
    checked = 0
    for page in out.rglob("*.html"):
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "lxml")
        for a in soup.find_all(["a", "link"], href=True):
            href = urlparse(a["href"])
            if href.scheme or not href.path:
                continue          # external, or a same-page anchor
            href = href.path
            checked += 1
            assert (page.parent / href).resolve().exists(), f"{page} -> {href}"
    assert checked > 50


def test_the_site_is_self_contained(built):
    """A strict host, an offline reader, and a privacy-conscious one all need
    the page to work with no third-party requests."""
    import re
    out, _ = built
    external = re.compile(r'<(?:script|link|img)[^>]+(?:src|href)="https?://')
    for page in out.rglob("*.html"):
        assert not external.search(page.read_text(encoding="utf-8")), page


def test_search_index_matches_the_pages_written(built):
    out, _ = built
    idx = json.loads((out / "search-index.json").read_text())
    assert len(idx) == 40
    for row in idx:
        assert (out / "bills" / f"{row['n']}.html").exists()
        assert {"n", "l", "t", "o", "d", "s"} <= set(row)


def test_changes_are_shown_inside_their_sentence(built):
    """An isolated fragment is useless -- "does" or "shall be assessed" tells
    a reader nothing without the sentence around it."""
    from bs4 import BeautifulSoup
    out, _ = built
    for page in (out / "bills").glob("*.html"):
        if page.stem.endswith("-text"):
            continue
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "lxml")
        passages = soup.select(".passage")
        if not passages:
            continue
        marks = passages[0].select("ins, del")
        assert marks, "a changed passage must mark what changed"
        body = passages[0].find("p").get_text(" ", strip=True)
        changed = sum(len(m.get_text()) for m in marks)
        assert len(body) > changed, "the passage must carry surrounding context"
        return
    pytest.skip("no changed passages in this slice")


def test_full_text_page_keeps_the_legislatures_line_numbers(built):
    from bs4 import BeautifulSoup
    out, _ = built
    page = next(iter((out / "bills").glob("*-text.html")))
    soup = BeautifulSoup(page.read_text(encoding="utf-8"), "lxml")
    numbered = [d for d in soup.select(".doc .ln") if d.get("id")]
    assert numbered, "lines must be anchorable for citation"
    assert numbered[0]["id"].startswith("L")


def test_home_page_tiles_filter_the_bill_list(built):
    """Each tile must name an outcome the index can actually filter on, or
    clicking it silently yields nothing."""
    from bs4 import BeautifulSoup
    out, _ = built
    soup = BeautifulSoup((out / "index.html").read_text(encoding="utf-8"), "lxml")
    tiles = soup.select("#tiles a[data-outcome]")
    assert tiles, "no filter tiles rendered"
    keys = {t["data-outcome"] for t in tiles}
    assert "all" in keys
    idx = json.loads((out / "search-index.json").read_text())
    present = {b["o"] for b in idx}
    assert (keys - {"all"}) <= present, "a tile filters on an unknown outcome"
    assert len(soup.select("#tiles a.on")) == 1, "exactly one tile starts active"


def test_bill_list_renders_without_javascript(built):
    from bs4 import BeautifulSoup
    out, _ = built
    soup = BeautifulSoup((out / "index.html").read_text(encoding="utf-8"), "lxml")
    rows = soup.select("#bills tbody tr")
    assert rows, "the default list must be server-rendered"
    assert rows[0].find("a")["href"].startswith("bills/")


def test_a_bill_page_carries_its_outcome_and_pathway(built):
    from bs4 import BeautifulSoup
    out, _ = built
    page = next(p for p in (out / "bills").glob("*.html")
                if not p.stem.endswith("-text"))
    soup = BeautifulSoup(page.read_text(encoding="utf-8"), "lxml")
    assert soup.find("h1")
    assert soup.find(class_="stamp"), "outcome stamp missing"
    # The redesign replaced the eleven-rung stage ladder with a dated
    # timeline of what actually happened. The stage is still computed and
    # still shown, as the stamp above -- see stages.py.
    assert soup.select(".side .trow"), "timeline missing"
    assert "flsenate.gov" in page.read_text(encoding="utf-8"), \
        "every page must link back to the official record"


def test_statute_citations_cannot_escape_the_output_directory(built):
    """Citations become filenames, so anything odd must be refused rather
    than written outside the tree."""
    out, _ = built
    for page in (out / "statutes").glob("*.html"):
        assert ".." not in page.name


def test_only_rebuilds_one_bill_over_an_existing_site(built):
    """The button's rebuild path. It must refresh the bill and leave the rest
    of the tree standing -- including links out to bills it did not render."""
    from bs4 import BeautifulSoup
    from flba.site import build
    out, _ = built
    other = sorted(p.name for p in (out / "bills").glob("*.html")
                   if not p.stem.endswith(SUBPAGES))
    num = int(Path(other[-1]).stem)

    stats = build(DB, out, "2026", built="2026-01-01", limit=40, only=num)
    assert stats["partial"] and stats["bills"] == 1
    # every sibling page survived
    assert sorted(p.name for p in (out / "bills").glob("*.html")
                  if not p.stem.endswith(SUBPAGES)) == other

    # and the rebuilt page still links only to pages that exist
    soup = BeautifulSoup((out / "bills" / f"{num}.html").read_text(), "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#")[0].split("?")[0]
        if href.startswith(("http", "mailto:")) or not href:
            continue
        assert (out / "bills" / href).resolve().exists(), href


def test_only_rejects_a_bill_that_is_not_there(built):
    from flba.site import build
    out, _ = built
    with pytest.raises(SystemExit):
        build(DB, out, "2026", built="2026-01-01", only=999999)


def test_the_published_prompt_is_the_prompt_that_gets_sent(built):
    """The page is only worth publishing if it cannot drift from the request.
    Both are built from passes.SYSTEM and brief.build, so compare the rendered
    page against a freshly assembled message."""
    import html as _html
    import re as _re
    import sqlite3
    from flba.analysis import passes as P
    from flba.analysis.analyze import load_bill
    from flba.analysis.brief import build as build_brief

    out, _ = built
    page_path = sorted((out / "bills").glob("*-prompt.html"))[0]
    num = int(page_path.stem[: -len("-prompt")])

    db = sqlite3.connect(str(DB))
    db.row_factory = sqlite3.Row
    bill, _version, diff, refs = load_bill(db, "2026", num)
    sent = P.messages(build_brief(bill, diff, refs)["text"], "summary")

    blocks = _re.findall(r'<pre class="prompt">(.*?)</pre>',
                         page_path.read_text(encoding="utf-8"), _re.S)
    assert len(blocks) == 2, "expected a system block and a user block"
    assert _html.unescape(blocks[0]) == sent[0]["content"]
    shown = _html.unescape(_re.sub(r"<[^>]+>", "", blocks[1]))
    assert shown.rsplit("\n\nTASK", 1)[0] == sent[1]["content"].rsplit("\n\nTASK", 1)[0]


# --- pulling the member out of a committee chain ---------------------------

COMMITTEES = {"Rules", "Judiciary", "Community Affairs", "Criminal Justice",
              "Appropriations"}


def test_the_member_is_separated_from_the_committees():
    """A bill through three committees reads "Rules; Judiciary; Community
    Affairs; McClain", and only the last is someone to write to."""
    from flba.site import split_sponsor
    panels, people = split_sponsor(
        "Rules; Judiciary; Community Affairs; McClain", COMMITTEES)
    assert panels == ["Rules", "Judiciary", "Community Affairs"]
    assert people == ["McClain"]


def test_house_committees_are_recognised_by_their_name():
    """House panels carry the word; the Senate vocabulary does not cover them."""
    from flba.site import split_sponsor
    panels, people = split_sponsor("Criminal Justice Subcommittee; Baker", set())
    assert panels == ["Criminal Justice Subcommittee"] and people == ["Baker"]


def test_two_members_and_no_committee():
    from flba.site import split_sponsor
    panels, people = split_sponsor("Mooney; LaMarca", COMMITTEES)
    assert panels == [] and people == ["Mooney", "LaMarca"]


def test_a_committee_bill_has_no_member_to_name():
    """The appropriations bills are filed by the committee itself."""
    from flba.site import split_sponsor
    panels, people = split_sponsor("Appropriations", COMMITTEES)
    assert panels == ["Appropriations"] and people == []


def test_no_committee_ever_trails_a_member_in_the_corpus():
    """The split names them rather than relying on order, but if the order
    ever broke it would mean the field means something else."""
    import sqlite3
    from flba.site import split_sponsor
    db = sqlite3.connect(str(DB))
    db.row_factory = sqlite3.Row
    known = {r["name"] for r in db.execute("SELECT DISTINCT name FROM committee_ref")}
    for r in db.execute("SELECT label,sponsor FROM bill"
                        " WHERE session='2026' AND sponsor<>''"):
        panels, people = split_sponsor(r["sponsor"], known)
        assert r["sponsor"].strip().startswith(tuple(panels) or ("",)), r["label"]
        assert len(panels) + len(people) == len(
            [p for p in r["sponsor"].split(";") if p.strip()]), r["label"]


def test_the_filed_by_row_only_appears_when_it_adds_something(built):
    """Repeating the Sponsor row verbatim would be noise on 1,230 bills."""
    from bs4 import BeautifulSoup
    out, _ = built
    seen_plain = seen_chain = 0
    for page in (out / "bills").glob("*.html"):
        if page.stem.endswith(SUBPAGES):
            continue
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        cells = {r.select_one(".k").get_text(strip=True):
                 r.select_one(".v").get_text(" ", strip=True)
                 for r in soup.select(".factledger .row")}
        if "FILED BY" in cells:
            # It earns its place either by digging the member out of a
            # committee chain, or by carrying detail the Sponsor row lacks.
            digs_out = ";" in cells["SPONSOR"]
            adds_detail = "District" in cells["FILED BY"]
            assert digs_out or adds_detail, page
            assert cells["FILED BY"] != cells["SPONSOR"], page
            seen_chain += 1
        elif "SPONSOR" in cells:
            seen_plain += 1
    assert seen_chain

    # The suppression path needs a bill filed by a committee and nobody else,
    # which the sample above does not always reach. The appropriations bills
    # are the standing example: there is no person to name.
    import sqlite3

    from flba.site import split_sponsor
    db = sqlite3.connect(str(DB))
    db.row_factory = sqlite3.Row
    known = {r["name"] for r in db.execute("SELECT DISTINCT name FROM committee_ref")}
    faceless = [r["label"] for r in
                db.execute("SELECT label,sponsor FROM bill WHERE session='2026'")
                if (lambda s: s[0] and not s[1])(split_sponsor(r["sponsor"], known))]
    assert faceless, "expected bills filed by a committee alone"


def test_the_official_record_shows_the_whole_url(built):
    from bs4 import BeautifulSoup
    out, _ = built
    page = sorted(p for p in (out / "bills").glob("*.html")
                  if not p.stem.endswith(SUBPAGES))[0]
    soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
    a = soup.select_one(".factledger .row:last-child a")
    assert a and a["href"].startswith("https://www.flsenate.gov/")
    # the scheme and www are trimmed for reading; the href stays whole
    assert a.get_text(strip=True) == a["href"].replace("https://www.", "")


# --- links off the site ----------------------------------------------------

def test_external_links_open_in_a_new_tab_and_internal_ones_do_not(built):
    """Enumerating the templates by hand misses the next link somebody adds,
    so the invariant is asserted over the built pages instead."""
    from urllib.parse import urlparse
    from bs4 import BeautifulSoup
    out, _ = built
    external, missing, wrong = 0, [], []
    for page in out.rglob("*.html"):
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        for a in soup.find_all("a", href=True):
            if urlparse(a["href"]).scheme in ("http", "https"):
                external += 1
                if a.get("target") != "_blank" or "noopener" not in (a.get("rel") or []):
                    missing.append((page.name, a["href"]))
            elif a.get("target") == "_blank":
                wrong.append((page.name, a["href"]))
    assert external > 0
    assert not missing, missing[:5]
    assert not wrong, wrong[:5]


def test_a_chapter_pill_without_a_url_is_not_a_link(built):
    """A link to "#" that opens a blank tab is worse than plain text."""
    from bs4 import BeautifulSoup
    out, _ = built
    for page in (out / "bills").glob("*.html"):
        if page.stem.endswith(SUBPAGES):
            continue
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        for a in soup.select(".billhead .meta a"):
            assert a["href"] != "#", page.name


# --- who filed it ----------------------------------------------------------

def test_a_sponsor_links_to_their_own_record_not_off_the_site(built):
    from bs4 import BeautifulSoup
    out, _ = built
    found = 0
    for page in (out / "bills").glob("*.html"):
        if page.stem.endswith(SUBPAGES):
            continue
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        row = next((r for r in soup.select(".factledger .row")
                    if r.select_one(".k").get_text(strip=True) == "FILED BY"), None)
        if not row:
            continue
        # The member's own chamber page, as distinct from the campaign-finance
        # link that sits beside it.
        a = next((x for x in row.select("a")
                  if "donations" not in (x.get("class") or [])), None)
        if not a:
            continue
        # It used to open the chamber's own page. What a reader wants next is
        # the rest of what this person filed, and the chamber's page is one
        # click on from there.
        assert a["href"].startswith("../member/"), a["href"]
        assert "target" not in a.attrs, "a page on this site opens in place"
        assert (page.parent / a["href"]).resolve().exists(), a["href"]
        text = row.select_one(".v").get_text(" ", strip=True)
        assert "District" in text, text
        found += 1
    assert found, "no bill rendered a linked sponsor"


def test_a_party_is_never_truncated_to_a_single_word_fragment():
    """A member who leaves a party sits as "No Party Affiliation"."""
    from flba.sources.flsenate import parse_senator_page
    html = ('<div class="senator"><h2>Senator Jason W. B. "Jay" Pizzo</h2>'
            '<p class="bold">Party: No Party Affiliation</p></div>')
    rec = parse_senator_page(html, "https://www.flsenate.gov/Senators/S37")
    assert rec["party"] == "No Party Affiliation"
    assert rec["district"] == 37
    assert rec["name"] == "Jason W. B. Pizzo"


def test_a_cited_subsection_links_to_the_statute_page_that_exists():
    """The model cites "1002.33(10)(e)"; pages are keyed on "1002.33"."""
    from flba.site import statute_page
    available = {"1002.33", "1003.42"}
    assert statute_page("1002.33(10)(e)", available) == "1002.33"
    assert statute_page("s. 1003.42(2)(w)", available) == "1003.42"
    assert statute_page("1002.33", available) == "1002.33"
    # nothing to link to rather than a link to nothing
    assert statute_page("999.99(1)", available) == ""
    assert statute_page("", available) == ""


def test_a_cite_with_no_page_renders_without_a_link(built):
    from bs4 import BeautifulSoup
    out, _ = built
    for page in (out / "bills").glob("*.html"):
        if page.stem.endswith(SUBPAGES):
            continue
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        for a in soup.select("a.pill[href*='statutes/']"):
            target = out / "statutes" / a["href"].split("statutes/")[1]
            assert target.exists(), f"{page.name} -> {a['href']}"


def test_every_template_renders_for_every_bill(built):
    """A build that raises leaves a half-written tree. The fixture builds 40
    bills, so this asserts the whole set rendered rather than trusting that
    the first page did."""
    out, stats = built
    assert stats["bills"] == 40
    for suffix in ("", "-text", "-prompt"):
        pages = list((out / "bills").glob(f"*{suffix}.html")) if suffix else [
            p for p in (out / "bills").glob("*.html")
            if not p.stem.endswith(SUBPAGES)]
        assert pages, suffix
        for p in pages:
            assert p.stat().st_size > 500, p


def test_a_bill_with_text_always_links_to_it(built):
    """The link used to sit inside the statutes block, so 268 bills that amend
    no general statute -- local acts, claim bills -- rendered marked-up text
    that nothing on the page pointed at."""
    from bs4 import BeautifulSoup
    out, _ = built
    checked = 0
    for page in (out / "bills").glob("*.html"):
        if page.stem.endswith(SUBPAGES):
            continue
        if not (out / "bills" / f"{page.stem}-text.html").exists():
            continue
        checked += 1
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        link = soup.select_one("p.billtext a")
        assert link, page.name
        assert link["href"].endswith(f"{page.stem}-text.html")
        # the bill's own words come before anyone's reading of them
        assert not link.find_parent(class_="side"), page.name
        box = soup.select_one("section.aibox")
        if box:
            assert link.sourceline < box.sourceline, page.name
    assert checked


def test_the_full_text_page_is_inside_the_page_padding(built):
    """It rendered flush against the left edge: the redesign's <main> adds no
    padding of its own and the template had no container."""
    from bs4 import BeautifulSoup
    out, _ = built
    page = next((out / "bills").glob("*-text.html"))
    soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
    doc = soup.select_one(".doc")
    assert doc, "no document body"
    assert doc.find_parent(class_="wrap"), "document is not inside a padded container"


def test_the_full_text_page_keeps_its_stylesheet(built):
    """Rewriting style.css for the redesign dropped every rule this page used."""
    out, _ = built
    css = (out / "style.css").read_text(encoding="utf-8")
    for rule in (".doc", ".doc .ln", ".doc .no", ".doc .tx", ".doc ins", ".doc del"):
        assert rule in css, rule


def test_a_bill_number_drops_its_committee_substitutes():
    """CS/CS/SB 36 is SB 36 carried through two substitutes. The prefix is
    history, not identity, and listings ask which bill this is."""
    from flba.site import short_label
    assert short_label("CS/CS/SB 36") == "SB 36"
    assert short_label("CS/CS/CS/CS/HB 1445") == "HB 1445"
    assert short_label("SB 2") == "SB 2"
    assert short_label("") == ""


def test_the_listing_shows_the_bare_number_and_still_finds_the_full_one(built):
    """A reader who pastes "CS/HB 21" off the Legislature's site must land on
    the bill the column shows as "HB 21"."""
    import sqlite3
    out, _ = built
    idx = {r["n"]: r for r in json.loads((out / "search-index.json").read_text())}
    labels = dict(sqlite3.connect(str(DB)).execute(
        "SELECT num, label FROM bill WHERE session='2026'"))
    carried = 0
    for n, r in idx.items():
        full = labels[n]
        assert r["l"] == full.rsplit("/", 1)[-1], full
        assert full.lower() in r["s"], f"{full} is no longer searchable"
        carried += "/" in full
    assert carried, "no committee substitute in this slice to check"


def test_the_browser_tab_names_the_bill_not_its_committee_history(built):
    from bs4 import BeautifulSoup
    out, _ = built
    idx = json.loads((out / "search-index.json").read_text())
    seen = 0
    for r in idx:
        page = out / "bills" / f"{r['n']}.html"
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "lxml")
        title = soup.title.string
        assert "CS/" not in title, title
        assert title.startswith(r["l"] + " — "), title
        seen += 1
    assert seen


def test_every_listed_bill_names_who_filed_it(built):
    """The column is only worth a column if it is never blank: a bill with no
    member behind it was filed by a committee, and that is the answer."""
    out, _ = built
    idx = json.loads((out / "search-index.json").read_text())
    for r in idx:
        assert r["f"], f"bill {r['n']} lists nobody"
        assert r["k"], f"bill {r['n']} has nothing to sort on"


def _listing(path):
    from bs4 import BeautifulSoup
    return BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")


def test_both_listings_offer_the_same_search_and_sorting(built):
    """An area page is the same ledger over fewer bills, so it gets the same
    controls -- a reader who learns one has learned the other."""
    out, _ = built
    widest = lambda d: max(d.glob("*.html"),
                           key=lambda p: len(_listing(p).select("#billtable tbody tr")))
    for page in (out / "index.html", widest(out / "area"), widest(out / "member")):
        soup = _listing(page)
        cols = [th["data-sort"] for th in soup.select("#billtable thead th[data-sort]")]
        assert cols == ["n", "t", "f", "d"], f"{page.name}: {cols}"
        assert soup.select_one("#q"), f"{page.name} has no search box"
        assert soup.select_one("#rows"), f"{page.name} ships no rows to sort"
        rows = soup.select("#billtable tbody tr")
        assert rows, f"{page.name} renders no bills without javascript"
        assert all(len(r.find_all("td")) == 4 for r in rows), page.name
        assert rows[0].select_one("td.filed").get_text(strip=True), page.name


def test_an_area_listing_carries_only_that_areas_bills(built):
    out, _ = built
    idx = {r["n"]: r for r in json.loads((out / "search-index.json").read_text())}
    for page in (out / "area").glob("*.html"):
        soup = _listing(page)
        area = soup.h1.get_text(strip=True)
        rows = json.loads(soup.select_one("#rows").string or "[]")
        assert all(idx[r["n"]]["a"] == area for r in rows), page.name


def test_a_rebuild_on_another_day_touches_only_the_pages_about_the_day(
        built, tmp_path_factory):
    """The build date used to sit in every footer, so a rebuild the next
    morning rewrote all 9,731 files and turned every deploy into a full one.

    Three pages are about the day by nature -- the calendar, the specimen
    alert on the subscribe page -- and two carry the stamp deliberately.
    Nothing else may move when only the date does.
    """
    from flba.site import build
    out, _ = built
    later = tmp_path_factory.mktemp("site-later")
    build(DB, later, "2026", built="2026-11-30", limit=40)

    def names(root):
        return {p.relative_to(root).as_posix()
                for p in root.rglob("*") if p.is_file()}

    assert names(out) == names(later), "the two builds wrote different files"
    changed = {r for r in names(out)
               if (out / r).read_bytes() != (later / r).read_bytes()}
    assert changed == {"index.html", "about.html", "calendar.html",
                       "subscribe.html"}, changed


def test_every_face_the_stylesheet_asks_for_is_shipped(built):
    """A face the build forgets is six 404s in the reader's console and a page
    silently set in a fallback, with nothing in the build to say so."""
    import re
    out, _ = built
    css = (out / "style.css").read_text(encoding="utf-8")
    asked = [m.strip("'\" ") for m in re.findall(r"url\(([^)]+)\)", css)]
    assert asked, "the stylesheet names no faces -- has it moved?"
    for ref in asked:
        assert not ref.startswith(("http", "//", "data:")), (
            f"{ref} would send a reader to a third party")
        assert (out / ref).exists(), f"style.css asks for {ref}, which is not built"


def test_every_disposition_filter_offered_can_actually_match_something(built):
    """An area holds a different set of endings from the session as a whole.
    A filter that empties the table is worse than an absent one: the reader
    learns nothing from it and cannot tell a bug from a fact."""
    out, _ = built
    pages = [out / "index.html", *sorted((out / "area").glob("*.html")),
             *sorted((out / "member").glob("*.html"))]
    checked = 0
    for page in pages:
        soup = _listing(page)
        strip = soup.select("#filters a[data-outcome]")
        if not strip:
            continue
        present = {r["o"] for r in json.loads(soup.select_one("#rows").string or "[]")}
        for a in strip:
            key = a["data-outcome"]
            assert key == "all" or key in present, (
                f"{page.name}: {key} filters to nothing")
        assert len(soup.select("#filters a.on")) == 1, (
            f"{page.name}: exactly one filter must start active")
        checked += 1
    assert checked > 1, "no area page carried a filter strip"


def _member_links(out):
    """Which bills name which member, read off the bill pages themselves."""
    from bs4 import BeautifulSoup
    filed: dict = {}
    for page in (out / "bills").glob("*.html"):
        if page.stem.endswith(SUBPAGES):
            continue
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        for a in soup.select('.factledger .row .v a[href^="../member/"]'):
            filed.setdefault(a["href"].split("/")[-1], set()).add(int(page.stem))
    return filed


def test_a_member_page_lists_every_bill_that_names_them_and_no_others(built):
    """The link on a bill and the listing on the member's page are two views
    of one fact, so they cannot be allowed to disagree."""
    out, _ = built
    filed = _member_links(out)
    assert filed, "no bill named a member"
    for name, nums in filed.items():
        soup = _listing(out / "member" / name)
        listed = {r["n"] for r in json.loads(soup.select_one("#rows").string)}
        assert listed == nums, f"{name}: {sorted(listed ^ nums)}"


def test_a_member_page_says_whose_it_is_and_where_else_to_look(built):
    out, _ = built
    for name in _member_links(out):
        soup = _listing(out / "member" / name)
        assert soup.h1.get_text(strip=True), f"{name} has no name on it"
        # The chamber's own page is still reachable, one click on rather than
        # in place of the record.
        offsite = [a["href"] for a in soup.select(".offsite a")]
        assert any("flsenate.gov" in h or "flhouse.gov" in h for h in offsite), (
            f"{name} lost the link to the chamber's page: {offsite}")


def test_a_member_name_cannot_walk_out_of_its_directory(built):
    """A name reaches a path here, and some of them carry accents, dots and
    apostrophes."""
    import re
    out, _ = built
    pages = list((out / "member").glob("*.html"))
    assert pages
    for page in pages:
        assert re.fullmatch(r"[a-z0-9-]+", page.stem), page.stem
