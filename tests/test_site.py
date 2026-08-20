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
        assert {"n", "l", "t", "o", "k", "s"} <= set(row)


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
    tiles = soup.select("#tiles .stat")
    assert tiles, "no filter tiles rendered"
    keys = {t["data-outcome"] for t in tiles}
    assert "all" in keys
    idx = json.loads((out / "search-index.json").read_text())
    present = {b["k"] for b in idx}
    assert (keys - {"all"}) <= present, "a tile filters on an unknown outcome"
    assert len(soup.select("#tiles .stat.on")) == 1, "exactly one tile starts active"


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
    assert soup.find(class_="badge"), "outcome badge missing"
    assert len(soup.select("ol.path li")) == 11, "pathway ladder missing"
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
        href = a["href"].split("#")[0]
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
        cells = {th.get_text(strip=True): th.find_next("td").get_text(strip=True)
                 for th in soup.select("div.panel th")}
        if "Filed by" in cells:
            assert ";" in cells["Sponsor"], page          # only for chains
            assert cells["Filed by"] != cells["Sponsor"], page
            seen_chain += 1
        elif "Sponsor" in cells:
            seen_plain += 1
    assert seen_chain and seen_plain


def test_the_official_record_shows_the_whole_url(built):
    from bs4 import BeautifulSoup
    out, _ = built
    page = sorted(p for p in (out / "bills").glob("*.html")
                  if not p.stem.endswith(SUBPAGES))[0]
    soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
    a = soup.select_one("div.panel a.url")
    assert a and a.get_text(strip=True) == a["href"]
    assert a["href"].startswith("https://www.flsenate.gov/")
