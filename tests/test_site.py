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


def test_it_writes_a_summary_and_a_full_text_page_per_bill(built):
    out, stats = built
    assert stats["bills"] == 40
    summaries = [p for p in (out / "bills").glob("*.html")
                 if not p.stem.endswith("-text")]
    assert len(summaries) == 40
    # a full-text page exists wherever the bill's text was cached
    assert list((out / "bills").glob("*-text.html"))


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
