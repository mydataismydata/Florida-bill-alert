"""Turn a bill's sponsor into a link to who funds them.

PAC Tracker (https://pactrack.sjcrlc.org) holds Florida Division of Elections
filings and resolves a person from surname plus given name. The Legislature
publishes neither: the Senate gives a surname and a link to a member page, and
the House gives a surname alone. So the join runs in two steps -- surname to
sitting member via each chamber's roster, then full name to filings via PAC
Tracker -- and both happen here, at build time, on this machine. The public
site ships nothing but the finished links.

Why the surname alone will not do: 558 people have filed for a House seat
under 501 distinct surnames. Six of them filed as Smith. Linking on a surname
would silently attribute one person's donors to another, which is a worse
failure than showing no link at all, because nothing about the page would look
wrong. Every match here therefore carries a given name, and anything that
stays ambiguous is dropped.

Name forms that had to be handled, all of them real in the 2026 rosters:

    Mooney Jr., James Vernon "Jim"   suffix on the surname, not the end
    Eskamani, Dr. Anna  V.           honorific
    Basabe, Fabián                   filings are unaccented
    Gossett-Seidman, Peggy           filed under either half
    Hart-Lowman, Dianne "Ms Dee"     filed under a former name
    Alvarez, D. / Alvarez, J.        the House's own disambiguation

Coverage in the 2026 session is 112 of 114 House sponsors and 33 of 37
senators. The remainder have no aggregated filing -- they appear in the data
as donors but never filed as a candidate or committee -- which PAC Tracker
reports as a 404 and this module treats as "no link", not as an error.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://pactrack.sjcrlc.org"


class TrackerError(RuntimeError):
    """PAC Tracker could not answer. Distinct from "this person has no
    filings", which is a 404 and an ordinary result."""


# Suffixes attach to either name part: "Mooney Jr., James" and "Massullo,
# Ralph E., Jr." are both in use.
_SUFFIX = re.compile(r"\b(?:Jr|Sr|II|III|IV|V)\.?(?=\s|,|$)", re.I)
_HONORIFIC = re.compile(r"^(?:Dr|Mr|Mrs|Ms|Rev|Hon)\.?\s+", re.I)
_NICKNAME = re.compile(r'"([^"]*)"')
_INITIAL = re.compile(r"^[A-Z]\.?$")
_COMMITTEE = re.compile(r"Committee|Subcommittee|Council|Caucus|Delegation", re.I)


def fold(text: str) -> str:
    """Drop accents. The state's filings are plain ASCII: Valdés is VALDES."""
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


def parse_name(raw: str) -> tuple[str, list[str]]:
    """Split a roster name into a surname and the given names worth trying.

    Returns the formal given name first and any nickname after it, because a
    filing may carry either -- Andrade filed as Alex, not Robert.

        'Mooney Jr., James Vernon "Jim"' -> ('Mooney', ['James', 'Jim'])
        'Don Gaetz'                      -> ('Gaetz', ['Don'])
    """
    nicknames = [n.strip() for n in _NICKNAME.findall(raw) if n.strip()]
    text = _NICKNAME.sub(" ", raw)

    head, _, tail = text.partition(",")
    # The comma means "surname first" in a roster listing, but the Senate
    # writes "Ralph E. Massullo, Jr." where all it separates is the suffix.
    if tail and _SUFFIX.sub("", tail).strip():
        surname, given = head, tail
    else:
        words = _SUFFIX.sub("", head).split()
        surname = words[-1] if words else ""
        given = " ".join(words[:-1])

    surname = _SUFFIX.sub("", surname).strip()
    given = _HONORIFIC.sub("", _SUFFIX.sub("", given).strip()).strip()

    formal = [w for w in given.split() if not _INITIAL.match(w)]
    names = ([formal[0]] if formal else []) + nicknames
    return surname, list(dict.fromkeys(n for n in names if n))


def display_name(raw: str) -> str:
    """A roster name written the way a reader expects to see it.

    The chamber's spelling is the one to publish. PAC Tracker's `name` is a
    normalised search key -- it lowercases LaMarca and shortens LaVon Bracy
    Davis to "Lavon Davis" -- so it is used for the link and never shown.

        'Mooney Jr., James Vernon "Jim"' -> 'James Mooney'
        'LaVon Bracy Davis'              -> 'LaVon Bracy Davis'
    """
    text = _NICKNAME.sub(" ", raw)
    head, _, tail = text.partition(",")
    if not (tail and _SUFFIX.sub("", tail).strip()):
        return re.sub(r"\s+", " ", text.strip())     # already "First Last"
    surname = _SUFFIX.sub("", head).strip()
    given = _HONORIFIC.sub("", _SUFFIX.sub("", tail).strip()).strip()
    first = next((w for w in given.split() if not _INITIAL.match(w)), "")
    return re.sub(r"\s+", " ", f"{first} {surname}".strip())


def surname_forms(surname: str) -> list[str]:
    """Every spelling of a surname worth asking about, most exact first.

    A hyphenated name is filed under the whole or under either half, and both
    halves resolve to the same record -- Gossett and Seidman each return the
    single entity filed as "Gossett-Seidman, Peggy". Splitting is therefore
    safe here, where it would not be if the halves were separate people.
    """
    forms = [surname, fold(surname)]
    if "-" in surname:
        forms += [p for half in surname.split("-")
                  for p in (half.strip(), fold(half.strip())) if p]
    return list(dict.fromkeys(f for f in forms if f))


def sponsor_names(sponsor: str, committees: set[str] = frozenset()) -> list[str]:
    """The people in a sponsor field, dropping the committees.

    "Rules; Judiciary; McClain" is three committees and one person; only the
    person has donors. The Senate names its committees without the word --
    "Agriculture", "Rules" -- so the caller passes the list the session
    actually used rather than relying on the word appearing.
    """
    return [p for p in (x.strip() for x in (sponsor or "").split(";"))
            if p and p not in committees and not _COMMITTEE.search(p)]


def split_token(token: str) -> tuple[str, str]:
    """A sponsor token into surname and the House's disambiguating initial.

    'Alvarez, J.' -> ('Alvarez', 'J');  'Hunschofsky' -> ('Hunschofsky', '')
    """
    surname, _, initial = token.partition(",")
    return surname.strip(), initial.strip().rstrip(".")


class Tracker:
    """The public half of PAC Tracker's API.

    `/api/people/*` and `/person/*` are open; everything else needs an
    account. Only these two are used, so nothing here holds a credential.
    """

    def __init__(self, base: str = BASE, public: str = BASE, timeout: int = 20):
        # Where the answers come from and where the reader is sent are
        # separate: a build run against a local instance must still publish
        # links a stranger can open.
        self.base = base.rstrip("/")
        self.public = public.rstrip("/")
        self.timeout = timeout

    def person_url(self, surname: str, given: str) -> str:
        return (f"{self.public}/person/{urllib.parse.quote(surname.lower())}"
                f"/{urllib.parse.quote(given.lower())}")

    def lookup(self, surname: str, given: str) -> dict | None:
        """Totals for one person, or None when nothing has been filed.

        `top=0` skips the donor ledger, which is the slow half of the query.

        Raises TrackerError on anything but a 404, so a rate limit or a bad
        gateway is reported rather than filed away as "this person has no
        donors". The two look identical in the output otherwise.
        """
        url = (f"{self.base}/api/people/{urllib.parse.quote(surname.lower())}"
               f"/{urllib.parse.quote(given.lower())}?top=0")
        why: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=self.timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None             # no filing is not a failure
                if e.code == 429 or e.code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise TrackerError(f"{url} -> HTTP {e.code}") from e
            except (urllib.error.URLError, TimeoutError,
                    json.JSONDecodeError) as e:
                why = e
                time.sleep(2 ** attempt)
        raise TrackerError(f"{url} -> gave up after 3 attempts ({why})")

    def resolve(self, name: str) -> dict | None:
        """Find the filings for a roster name, trying each spelling in turn.

        Stops at the first hit: the forms are ordered exact-first, so the
        earliest match is the closest one.
        """
        surname, givens = parse_name(name)
        if not (surname and givens):
            return None
        for given in givens:
            for form in surname_forms(surname):
                for spelling in dict.fromkeys([given, fold(given)]):
                    found = self.lookup(form, spelling)
                    if not found:
                        continue
                    person = found["person"]
                    return {
                        "name": person["name"],
                        "last": person["last"],
                        "first": person["first"],
                        "url": self.person_url(person["last"], person["first"]),
                        "total_received": person.get("totalReceived") or "0",
                        "total_given": person.get("totalGiven") or "0",
                        "filings": len(person.get("parts") or []),
                        "same_surname": len(person.get("sameSurname") or []),
                    }
        return None


def roster_index(members: list[dict]) -> dict[str, list[dict]]:
    """Group roster rows by folded surname, ready to match a sponsor token."""
    index: dict[str, list[dict]] = {}
    for m in members:
        surname, givens = parse_name(m["name"])
        if not surname:
            continue
        row = dict(m, surname=surname, givens=givens)
        index.setdefault(fold(surname).lower(), []).append(row)
    return index


def match(token: str, index: dict[str, list[dict]]) -> dict | None:
    """The one member a sponsor token names, or None if it is not certain.

    Two members can share a surname. The House distinguishes them with an
    initial and this follows suit; where that still leaves more than one
    person the token is dropped, because a wrong link reads exactly like a
    right one.
    """
    surname, initial = split_token(token)
    rows = index.get(fold(surname).lower(), [])
    if initial:
        rows = [r for r in rows
                if any(g.upper().startswith(initial.upper()) for g in r["givens"])]
    if len({(r["givens"] or [""])[0].lower() for r in rows}) != 1:
        return None
    return rows[0]
