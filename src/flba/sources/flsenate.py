"""Parsers for flsenate.gov.

The Senate site carries bills, bill text, and committee staff analyses for
BOTH chambers -- House analyses appear here with an `h` filename prefix -- so
this one source covers the whole Legislature.

Markup notes (verified 2026-08): every tab is a `div.tabbody` with a stable
id (tabBodyBillHistory, tabBodyAnalyses, ...) containing plain
`table.tbl` elements with thead/tbody. No JavaScript is required.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE = "https://www.flsenate.gov"
PAGE_SIZE = 50


def _txt(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _abs(href: str | None) -> str:
    return urljoin(BASE, href) if href else ""


def list_page_url(session: str, page: int) -> str:
    return (f"{BASE}/Session/Bills/{session}"
            f"?Chamber=both&PageNumber={page}&ExpandedView=False")


def parse_bill_list(html: str) -> tuple[list[dict], int]:
    """Return (rows, total_bills_reported_by_site)."""
    soup = BeautifulSoup(html, "lxml")

    total = 0
    m = re.search(r"([\d,]+)\s+Bills?\s+Found", soup.get_text(" ", strip=True), re.I)
    if m:
        total = int(m.group(1).replace(",", ""))

    rows = []
    table = soup.select_one("table.clickableRows")
    if table:
        for tr in table.select("tbody tr"):
            link = tr.select_one("th a[href*='/Session/Bill/']")
            if not link:
                continue
            cells = tr.find_all("td")
            href = link["href"]
            num = re.search(r"/Session/Bill/[^/]+/(\d+)", href)
            rows.append({
                "label": _txt(link),
                "num": int(num.group(1)) if num else None,
                "url": _abs(href),
                "title": _txt(cells[0]) if len(cells) > 0 else "",
                "filed_by": _txt(cells[1]) if len(cells) > 1 else "",
                "last_action": re.sub(r"^Last Action:\s*", "", _txt(cells[2]))
                               if len(cells) > 2 else "",
            })
    return rows, total


def _tables_with_headings(tabbody):
    """Yield (nearest preceding h4/h5 text, table) inside a tab body."""
    if tabbody is None:
        return
    heading = ""
    for el in tabbody.find_all(["h4", "h5", "table"]):
        if el.name in ("h4", "h5"):
            heading = _txt(el)
        else:
            yield heading, el


def _rows(table):
    """Yield lists of cell-nodes for each body row (skips header rows)."""
    body = table.find("tbody") or table
    for tr in body.find_all("tr", recursive=False) or body.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if cells and not all(c.name == "th" for c in cells):
            yield cells
        elif cells and tr.find_parent("thead") is None and len(cells) > 1:
            yield cells


def parse_bill_page(html: str, session: str, num: int) -> dict:
    soup = BeautifulSoup(html, "lxml")
    rec: dict = {
        "session": session,
        "num": num,
        "url": f"{BASE}/Session/Bill/{session}/{num}",
    }

    # --- header: "CS/HB 33: Title" then "GENERAL BILL by A; B; (CO-INTRODUCERS) C" ---
    h2 = soup.find("h2")
    head = _txt(h2)
    if ":" in head:
        rec["label"], rec["title"] = (p.strip() for p in head.split(":", 1))
    else:
        rec["label"], rec["title"] = head, head

    # Labels carry committee-substitute prefixes: "CS/HB 33", "CS/CS/SB 7016".
    base = re.sub(r"^(?:CS/)+", "", rec["label"]).strip()
    rec["chamber"] = ("Senate" if base[:1].upper() == "S"
                      else "House" if base[:1].upper() == "H" else "")

    rec["sponsor"] = rec["cosponsors"] = rec["subject"] = ""
    rec["sponsor_url"] = ""
    if h2:
        byline = h2.find_next_sibling("p")
        if byline:
            bt = _txt(byline)
            # Senate sponsors are links; House sponsors are bare text. Both
            # render as "<SUBJECT> by <names>", names separated by semicolons.
            m = re.match(r"(.*?)\s+by\s+(.*)$", bt, re.S)
            if m:
                rec["subject"], names = m.group(1).strip(), m.group(2)
            else:
                rec["subject"], names = bt, ""
            primary, _, co = names.partition("(CO-INTRODUCERS)")
            clean = lambda v: "; ".join(
                x.strip() for x in v.split(";") if x.strip())
            rec["sponsor"] = clean(primary)
            rec["cosponsors"] = clean(co)
            sp = byline.find("a", href=re.compile(r"/(Senators|Representatives)/"))
            rec["sponsor_url"] = _abs(sp["href"]) if sp else ""
        long_p = soup.select_one("p.width80")
        rec["long_title"] = _txt(long_p)

    # --- snapshot: effective date, last action, committee references ---
    snap = soup.select_one("#snapshot")
    snap_text = _txt(snap)
    m = re.search(r"Effective Date:\s*(.*?)\s*Last Action:", snap_text)
    rec["effective_date"] = m.group(1).strip() if m else ""
    m = re.search(r"Last Action:\s*(.*?)(?:\s*Bill Text:|$)", snap_text)
    rec["last_action"] = m.group(1).strip() if m else ""

    rec["chapter_law"] = rec["chapter_law_url"] = ""
    if snap:
        cm = re.search(r"Chapter No\.\s*([\d-]+)", snap_text)
        if cm:
            rec["chapter_law"] = cm.group(1)
            a = snap.find("a", href=re.compile(r"laws\.flrules\.org"))
            if a:
                rec["chapter_law_url"] = a["href"].strip()

    refs = []
    if snap:
        for span in snap.find_all("span", class_="bold"):
            label = _txt(span)
            cm = re.match(r"(Senate|House) Committee References", label)
            if not cm:
                continue
            ol = span.find_next("ol")
            if not ol:
                continue
            for i, li in enumerate(ol.find_all("li")):
                t = _txt(li)
                code = re.search(r"\(([A-Z0-9]+)\)\s*$", t)
                refs.append({
                    "seq": i,
                    "chamber": cm.group(1),
                    "name": re.sub(r"\s*\([A-Z0-9]+\)\s*$", "", t),
                    "code": code.group(1) if code else "",
                })
    rec["committee_refs"] = refs

    # --- bill history ---
    history = []
    for _, table in _tables_with_headings(soup.select_one("#tabBodyBillHistory")):
        for cells in _rows(table):
            if len(cells) >= 3:
                history.append({
                    "date": _txt(cells[0]),
                    "chamber": _txt(cells[1]),
                    "action": re.sub(r"^[••]\s*", "", _txt(cells[2])),
                })
    rec["history"] = history

    # --- bill text versions ---
    texts = []
    for _, table in _tables_with_headings(soup.select_one("#tabBodyBillText")):
        for cells in _rows(table):
            if len(cells) < 3:
                continue
            version, posted = _txt(cells[0]), _txt(cells[1])
            for a in cells[2].find_all("a", href=True):
                texts.append({
                    "version": version,
                    "posted": posted,
                    "fmt": "HTML" if "HTML" in a["href"].upper() else "PDF",
                    "url": _abs(a["href"]),
                })
    rec["texts"] = texts

    # --- analyses (both chambers) ---
    analyses = []
    for _, table in _tables_with_headings(soup.select_one("#tabBodyAnalyses")):
        for cells in _rows(table):
            if len(cells) < 5:
                continue
            for a in cells[4].find_all("a", href=True):
                analyses.append({
                    "kind": _txt(cells[0]), "analysis": _txt(cells[1]),
                    "author": _txt(cells[2]), "posted": _txt(cells[3]),
                    "url": _abs(a["href"]),
                })
    rec["analyses"] = analyses

    # --- votes ---
    votes = []
    for heading, table in _tables_with_headings(soup.select_one("#tabBodyVoteHistory")):
        scope = "Floor" if "floor" in heading.lower() else "Committee"
        for cells in _rows(table):
            if len(cells) < 3:
                continue
            a = cells[-1].find("a", href=True)
            if scope == "Committee" and len(cells) >= 4:
                v, com, date = _txt(cells[0]), _txt(cells[1]), _txt(cells[2])
            else:
                v, com, date = _txt(cells[0]), "", _txt(cells[1])
            votes.append({
                "scope": scope, "version": v, "committee": com, "date": date,
                "result": _txt(cells[-1]), "url": _abs(a["href"]) if a else "",
            })
    rec["votes"] = votes

    # --- amendments (tables keyed by a <caption> naming the bill version) ---
    amendments = []
    for div_id, scope in (("CommitteeAmendmentASC", "Committee"),
                          ("FloorAmendmentASC", "Floor")):
        block = soup.select_one(f"#{div_id}")
        if not block:
            continue
        for table in block.find_all("table"):
            version = _txt(table.find("caption"))
            for cells in _rows(table):
                if len(cells) < 5:
                    continue
                raw = _txt(cells[0])
                am = re.match(r"(\d+)\s*-\s*(.*)$", raw)
                number, rest = (am.group(1), am.group(2)) if am else ("", raw)
                kind = "Delete All" if "delete all" in rest.lower() else "Amendment"
                links = [_abs(a["href"]) for a in cells[4].find_all("a", href=True)]
                amendments.append({
                    "scope": scope,
                    "version": version,
                    "number": number,
                    "kind": kind,
                    "description": rest,
                    "filed_by": _txt(cells[1]),
                    "posted": _txt(cells[2]),
                    "action": _txt(cells[3]),
                    "url": links[0] if links else "",
                })
    rec["amendments"] = amendments

    # --- citations ---
    citations = []
    for heading, table in _tables_with_headings(soup.select_one("#tabBodyCitations")):
        kind = re.sub(r"^Citations\s*-\s*", "", heading or "Unknown")
        kind = re.sub(r"\s*\(\d+\)\s*$", "", kind).strip() or "Unknown"
        for cells in _rows(table):
            cite = _txt(cells[0])
            if not cite:
                continue
            a = cells[0].find("a", href=True)
            citations.append({"kind": kind, "cite": cite,
                              "url": _abs(a["href"]) if a else ""})
    rec["citations"] = citations

    # --- related / companion bills ---
    related = []
    for _, table in _tables_with_headings(soup.select_one("#tabBodyRelatedBills")):
        for cells in _rows(table):
            if len(cells) < 4:
                continue
            a = cells[0].find("a", href=True)
            related.append({
                "label": _txt(cells[0]),
                "subject": _txt(cells[1]),
                "filed_by": _txt(cells[2]),
                "relationship": _txt(cells[3]) if len(cells) > 3 else "",
                "status": _txt(cells[4]) if len(cells) > 4 else "",
                "url": _abs(a["href"]) if a else "",
            })
    rec["related"] = related

    return rec
