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


def test_it_writes_a_page_per_bill(built):
    out, stats = built
    assert stats["bills"] == 40
    assert len(list((out / "bills").glob("*.html"))) == 40


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
            href = a["href"]
            if urlparse(href).scheme or href.startswith("#"):
                continue
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
        assert set(row) == {"n", "l", "t", "o", "s"}


def test_a_bill_page_carries_its_outcome_and_pathway(built):
    from bs4 import BeautifulSoup
    out, _ = built
    page = next((out / "bills").glob("*.html"))
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
