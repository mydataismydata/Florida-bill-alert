"""Who voted which way, read from the roll-call PDFs flsenate.gov links.

The bill page's Vote History tab gives the totals ("11 Yeas - 0 Nays") and a
link to a PDF. The names are only in the PDF, and it comes in two layouts.

Floor votes, both chambers (`HouseVote_*.PDF`, `SenateVote_*.PDF`), are
typed text. Each member reads "<code> <surname>-<district>", three to a line:

    Y Abbott-5 Y Eskamani-42 Y Oliver-76
    Barnaby-29 Y Gossett-Seidman-91 Y Redondo-118

The codes are Y (yea), N (nay), EX (excused) and AV (abstain). A member with
no code did not vote. A Senate record can close with "Votes after roll
call:" and "Wright—Yea". Those do not change the tally, so the member stays
"not voting" and the late vote is kept beside it.

Senate committee votes (`... Vote Record.PDF`) are a printed form. A mark
sits in the Yea or the Nay column beside each senator's name, so which way
they voted is a matter of where the mark is, not what it says. The text
layer loses that, so the form is read by position. The first Yea/Nay pair is
the final vote. The pairs to its right are for amendments and are blank on
the records seen so far. A mark is "X", or "VA" for a vote after roll call,
which the committee's printed total does count.

House committee votes are not published on flsenate.gov, so a House bill's
committee stops have totals only where the Senate heard it.
"""
from __future__ import annotations

import re
from pathlib import Path

YEA, NAY, ABSTAIN, NOT_VOTING = "Y", "N", "AV", "NV"

# "Yeas - 112 Nays - 1 Not Voting - 4"
_TOTALS = re.compile(r"Yeas\s*-\s*(\d+)\s+Nays\s*-\s*(\d+)"
                     r"(?:\s+Not\s+Voting\s*-\s*(\d+))?", re.I)
# One member on a floor record. The surname may hold spaces, commas,
# periods, apostrophes and hyphens ("Gonzalez Pittman", "Alvarez, D.",
# "Persons-Mulicka"). The district is the last hyphen-number, so the
# non-greedy name stops at the first "-<digits>" that ends a word.
_MEMBER = re.compile(r"(?:(?<!\S)(Y|N|EX|AV)\s+)?"
                     r"(?<!\S)([^\s\d][^\d]*?)-(\d{1,3})(?=\s|$)")
_AFTER = re.compile(r"^(.+?)\s*[—–-]+\s*(Yea|Nay)\s*$")
_LEGEND = re.compile(r"^Y=Yea\b")
# The bill's own line on a floor record ("HB 11", "CS/CS/SB 7016"). What the
# vote was on sits between it and the totals: "Passage", "Third Reading".
_LABEL = re.compile(r"^(?:CS/)*[HS][A-Z]{0,3}\s+\d+\b")
_FINAL_ACTION = re.compile(r"Final Action:\s*(.+)$", re.M)
# Officers are named on the record itself. A committee writes "Burton, VICE
# CHAIR" and "Yarborough, CHAIR". The Senate floor writes "President
# Albritton". The House floor names no officer, the Speaker included.
_OFFICER = re.compile(r",\s*((?:VICE\s+)?CHAIR)\s*$", re.I)
_PRESIDENT = re.compile(r"^(President)\s+(.+)$")


def parse(path: Path) -> dict:
    """Read one roll-call PDF.

    Returns {"layout", "motion", "printed": {"yeas", "nays", "not_voting"},
    "members": [{"name", "district", "position", "code", "excused", "after"}]}. "printed" is what
    the record itself says the totals are, so a caller can check the names
    add up to it.
    """
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        pages = list(pdf.pages)
        if "Vote Record" in Path(path).name or _is_committee(pages[0]):
            return _committee(pages)
        return _floor("\n\f\n".join(p.extract_text() or "" for p in pages))


def _is_committee(page) -> bool:
    return "COMMITTEE VOTE RECORD" in (page.extract_text() or "")[:400]


def _floor(text: str) -> dict:
    out = {"layout": "floor", "motion": "", "printed": {}, "members": []}
    lines = text.splitlines()
    start, label_at = None, None
    for i, line in enumerate(lines):
        if label_at is None and _LABEL.match(line.strip()):
            label_at = i
        m = _TOTALS.search(line)
        if m:
            if label_at is not None:
                out["motion"] = " · ".join(
                    x.strip() for x in lines[label_at + 1:i] if x.strip())
            out["printed"] = {"yeas": int(m.group(1)), "nays": int(m.group(2)),
                              "not_voting": int(m.group(3) or 0)}
            start = i + 1
            break
    if start is None:
        return out

    after: dict[str, str] = {}
    in_after = False
    page = 0
    for row, line in enumerate(lines[start:]):
        if line == "\f":
            page += 1
            continue
        line = line.strip()
        if not line or line.startswith("Presiding"):
            continue
        if _LEGEND.match(line):
            break
        if line.lower().startswith("votes after roll call"):
            in_after = True
            continue
        if in_after:
            m = _AFTER.match(line)
            if m:
                after[m.group(1).strip()] = YEA if m.group(2) == "Yea" else NAY
            continue
        for col, (code, name, district) in enumerate(_MEMBER.findall(line)):
            name, position = name.strip(), ""
            officer = _PRESIDENT.match(name)
            if officer:
                position, name = officer.group(1), officer.group(2).strip()
            out["members"].append({
                "name": name, "district": int(district), "position": position,
                "code": {"Y": YEA, "N": NAY, "AV": ABSTAIN}.get(code, NOT_VOTING),
                "excused": code == "EX", "after": "", "_at": (page, col, row)})

    # The record is alphabetical down each of three columns, and the text
    # runs across them, so the names are put back in column order.
    out["members"].sort(key=lambda m: m.pop("_at"))
    for m in out["members"]:
        late = after.get(m["name"])
        if late and m["code"] == NOT_VOTING:
            m["after"] = late
    return out


def _lines(words, tol: float = 5.0) -> list[list[dict]]:
    """Words grouped into printed lines by their height on the page."""
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return rows


def _committee(pages) -> dict:
    out = {"layout": "committee", "motion": "", "printed": {}, "members": []}
    m = _FINAL_ACTION.search(pages[0].extract_text() or "")
    if m:
        out["motion"] = m.group(1).strip()
    for page in pages:
        words = page.extract_words()
        head = next((w for w in words if w["text"] == "SENATORS"), None)
        if head is None:
            continue
        top = head["top"]
        cols = sorted((w for w in words if abs(w["top"] - top) < 3
                       and w["text"] in ("Yea", "Nay")), key=lambda w: w["x0"])
        if len(cols) < 2 or cols[0]["text"] != "Yea" or cols[1]["text"] != "Nay":
            continue
        yea, nay = cols[0], cols[1]
        mid = lambda w: (w["x0"] + w["x1"]) / 2
        # Names run from just right of the final vote's Nay column to short
        # of the first amendment column, or to the senator heading's far side
        # when there is none. An amendment's result on the totals line
        # ("PEND", "RCS") starts a few points left of its Yea heading, so the
        # margin is wide.
        left = nay["x1"] + 4
        right = cols[2]["x0"] - 15 if len(cols) > 2 else head["x1"] + 150
        # The totals line is the first line of bare numbers under the names.
        totals_at = None
        for row in _lines([w for w in words if w["top"] > top + 6]):
            text = " ".join(w["text"] for w in row)
            if text.startswith("TOTALS") or text.startswith("CODES"):
                break
            nums = [w for w in row if w["text"].isdigit() and w["x1"] < left]
            if nums and len(nums) == len([w for w in row if w["x1"] < left]):
                if not [w for w in row if left <= w["x0"] < right]:
                    totals_at = row[0]["top"]
                    tally = {(YEA if abs(mid(w) - mid(yea)) < abs(mid(w) - mid(nay))
                              else NAY): int(w["text"]) for w in nums}
                    out["printed"] = {"yeas": tally.get(YEA, 0),
                                      "nays": tally.get(NAY, 0)}
                    break
            name = " ".join(w["text"] for w in row if left <= w["x0"] < right)
            officer = _OFFICER.search(name)
            position = officer.group(1).title() if officer else ""
            name = _OFFICER.sub("", name).strip()
            if not name:
                continue
            code, late = NOT_VOTING, ""
            for w in row:
                if w["x1"] >= left:
                    continue
                side = YEA if abs(mid(w) - mid(yea)) < abs(mid(w) - mid(nay)) else NAY
                if w["text"] == "AV":
                    code = ABSTAIN
                elif w["text"] in ("X", "VA", "VC"):
                    code = side
                    late = "VA" if w["text"] == "VA" else ""
            out["members"].append({"name": name, "district": None, "code": code,
                                   "position": position, "excused": False,
                                   "after": late})
        if totals_at is not None:
            break
    if out["printed"]:
        out["printed"]["not_voting"] = sum(
            1 for m in out["members"] if m["code"] != YEA and m["code"] != NAY)
    return out


def counts(members: list[dict]) -> dict:
    return {"yeas": sum(m["code"] == YEA for m in members),
            "nays": sum(m["code"] == NAY for m in members),
            "not_voting": sum(m["code"] not in (YEA, NAY) for m in members)}
