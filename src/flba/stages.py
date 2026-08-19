"""Where a bill actually is in the process, derived from its history log.

Deterministic: a state machine over recorded actions, no model involved.

Two axes, because one is not enough. **Stage** is how far the bill travelled;
**outcome** is what became of it. A bill can reach the Governor and be vetoed,
or die in its first committee while its identical companion becomes law. A
single "progress" number cannot express either.

The vocabulary is treacherous in three specific ways, each verified against the
complete 2026 session:

1. "Laid on Table under Rule 7.18(a)" is **not** death. It is how a committee
   substitute replaces the version it supersedes -- the original is tabled and
   the CS is filed in its place, and the bill carries on. 107 bills with this
   action in their history became law. Reading it as death is the single most
   damaging mistake this module could make.
2. "Withdrawn from <committee>" is **not** death either. It pulls a bill out of
   committee to put it straight on the calendar; in 2026 it was followed by
   "Placed on Calendar" 157 times out of 164. Only "Withdrawn prior to
   introduction" and "Withdrawn from further consideration" end a bill.
3. "Died ... companion bill(s) passed, see X" means the *policy* was enacted
   through the twin bill. Reporting that as a plain failure misinforms
   precisely the reader this project exists to serve.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ------------------------------------------------------------- bill kinds ---
# Not everything the Legislature files is a bill, and the others do not end at
# the Governor's desk. Joint resolutions propose constitutional amendments and
# go to the **voters**; concurrent resolutions and memorials are filed with the
# Secretary of State; simple resolutions bind only the chamber that adopts one.
# Scoring all of these against "did it become law?" would misreport every one.

BILL, RESOLUTION, JOINT, CONCURRENT, MEMORIAL = (
    "bill", "resolution", "joint_resolution", "concurrent_resolution", "memorial")

KIND_LABEL = {
    BILL:       "Bill",
    RESOLUTION: "Resolution",
    JOINT:      "Joint resolution (constitutional amendment)",
    CONCURRENT: "Concurrent resolution",
    MEMORIAL:   "Memorial",
}


def kind_of(label: str) -> str:
    """Classify from the bill label, e.g. 'CS/CS/HJR 203' -> joint_resolution."""
    base = re.sub(r"^(?:CS/)+", "", label or "").strip().upper()
    prefix = re.match(r"([A-Z]+)", base)
    prefix = prefix.group(1) if prefix else ""
    return {
        "HJR": JOINT, "SJR": JOINT,
        "HCR": CONCURRENT, "SCR": CONCURRENT,
        "HM": MEMORIAL, "SM": MEMORIAL,
        "HR": RESOLUTION, "SR": RESOLUTION,
    }.get(prefix, BILL)


# ---------------------------------------------------------------- stages ---

STAGES: list[tuple[str, str]] = [
    ("filed",           "Filed"),
    ("referred",        "Referred to committee"),
    ("in_committee",    "Heard in committee"),
    ("reported",        "Reported out of committee"),
    ("on_calendar",     "Placed on calendar"),
    ("floor",           "Floor reading"),
    ("passed_origin",   "Passed originating chamber"),
    ("second_chamber",  "Sent to second chamber"),
    ("enrolled",        "Passed both chambers"),
    ("to_governor",     "Sent to the Governor"),
    ("law",             "Became law"),
]
# Resolutions travel a shorter road that ends in adoption, not enactment.
RES_STAGES: list[tuple[str, str]] = [
    ("filed",           "Filed"),
    ("referred",        "Referred to committee"),
    ("in_committee",    "Heard in committee"),
    ("reported",        "Reported out of committee"),
    ("on_calendar",     "Placed on calendar"),
    ("floor",           "Floor reading"),
    ("passed_origin",   "Adopted"),
    ("second_chamber",  "Sent to second chamber"),
    ("enrolled",        "Adopted by both chambers"),
    ("to_governor",     "Signed by officers"),
    ("law",             "Filed with the Secretary of State"),
]

RANK = {key: i for i, (key, _) in enumerate(STAGES)}
LABEL = dict(STAGES)
RES_LABEL = dict(RES_STAGES)


def ladder(kind: str) -> list[tuple[str, str]]:
    return STAGES if kind == BILL else RES_STAGES


def label_for(stage: str, kind: str) -> str:
    return (LABEL if kind == BILL else RES_LABEL)[stage]

# Ordered; first match wins, so the specific patterns come first.
STAGE_RULES: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), s) for p, s in [
    (r"^Chapter No\.",                                    "law"),
    (r"filed with Secretary of State",                    "law"),
    (r"^Adopted",                                         "passed_origin"),
    (r"^Approved by Governor",                            "law"),
    (r"^Became Law without",                              "law"),
    (r"^Vetoed by Governor",                              "to_governor"),
    (r"^Signed by Officers and presented to Governor",    "to_governor"),
    (r"^Ordered enrolled",                                "enrolled"),
    (r"^Enrolled",                                        "enrolled"),
    (r"^(In Messages|Immediately certified|Received)",    "second_chamber"),
    (r"^(CS )?[Pp]assed",                                 "passed_origin"),
    (r"^Read \d+(st|nd|rd|th) time",                      "floor"),
    (r"Calendar",                                         "on_calendar"),
    (r"^(Favorable|Reported out|CS by|Unfavorable)",      "reported"),
    (r"^(On Committee agenda|Added to .* agenda|Now in )", "in_committee"),
    (r"^(Referred to|Pending reference review)",          "referred"),
    (r"^(Filed|Introduced|CS Filed|\d+(st|nd|rd|th) Reading)", "filed"),
]]

# --------------------------------------------------------------- outcomes ---

PENDING, LAW, VETOED, DIED, SUPERSEDED, ADOPTED, TO_BALLOT = (
    "pending", "became_law", "vetoed", "died", "superseded",
    "adopted", "to_ballot")

# Once a bill has been enacted, vetoed, adopted, or sent to the ballot, that
# is settled. Later procedural entries -- a companion being tabled, say -- must
# not downgrade it.
SETTLED = ("became_law", "vetoed", "adopted", "to_ballot")

OUTCOME_LABEL = {
    PENDING:    "Still moving",
    LAW:        "Became law",
    VETOED:     "Vetoed by the Governor",
    DIED:       "Died",
    SUPERSEDED: "Superseded — its companion passed",
    ADOPTED:    "Adopted",
    TO_BALLOT:  "Goes to the voters as a constitutional amendment",
}

# Terse forms for tiles and filter chips, where the full sentence is too long.
OUTCOME_SHORT = {
    PENDING:    "still moving",
    LAW:        "became law",
    VETOED:     "vetoed",
    DIED:       "died",
    SUPERSEDED: "superseded by companion",
    ADOPTED:    "adopted",
    TO_BALLOT:  "went to the ballot",
}

# "companion bill(s) passed, see CS/CS/HB 123 (Ch. 2026-45)"
COMPANION = re.compile(
    r"companion bill\(s\) passed,\s*see\s+([A-Z/]*\s?[A-Z]{1,4}\s*\d+)", re.I)

# Real endings. Note what is deliberately absent: a bare "Laid on Table" and
# "Withdrawn from <committee>" are both procedural, not terminal.
DEATH_RULES = [re.compile(p, re.I) for p in [
    r"^Died\b",
    r"^Withdrawn prior to introduction",
    r"^Withdrawn from further consideration",
    r"^Failed to pass",
    r"^Indefinitely postponed",
    r"^Laid on Table[,;]\s*[Cc]ompanion bill",   # tabled for its twin
    r"^Laid on Table\s*(?:-+\s*[SH]J\s*\d+)?\s*$",   # bare tabling, no rule cited
    r"^Original bill laid on Table",             # merged into a combined bill
]]

# Tabling that merely swaps in a committee substitute.
SUBSTITUTION = re.compile(r"^Laid on Table under Rule", re.I)


@dataclass
class Milestone:
    stage: str
    label: str
    date: str
    chamber: str
    action: str


@dataclass
class BillProgress:
    stage: str = "filed"
    outcome: str = PENDING
    kind: str = BILL
    milestones: list[Milestone] = field(default_factory=list)
    died_in: str | None = None
    companion: str | None = None
    chapter_law: str | None = None
    events: int = 0

    @property
    def kind_label(self) -> str:
        return KIND_LABEL[self.kind]

    @property
    def stage_label(self) -> str:
        return label_for(self.stage, self.kind)

    @property
    def outcome_label(self) -> str:
        return OUTCOME_LABEL[self.outcome]

    @property
    def percent(self) -> int:
        """How far along its own ladder -- a resolution's road is shorter."""
        return round(100 * RANK[self.stage] / (len(ladder(self.kind)) - 1))

    def reached(self, stage: str) -> bool:
        return RANK[self.stage] >= RANK[stage]

    def as_dict(self) -> dict:
        return {
            "kind": self.kind, "kind_label": KIND_LABEL[self.kind],
            "stage": self.stage, "stage_label": self.stage_label,
            "percent": self.percent,
            "outcome": self.outcome, "outcome_label": self.outcome_label,
            "died_in": self.died_in, "companion": self.companion,
            "chapter_law": self.chapter_law, "events": self.events,
            "milestones": [m.__dict__ for m in self.milestones],
        }


def stage_of(action: str) -> str | None:
    if SUBSTITUTION.match(action):
        return None                      # procedural; carries no stage meaning
    for pattern, stage in STAGE_RULES:
        if pattern.search(action):
            return stage
    return None


def is_terminal(action: str) -> bool:
    return any(p.match(action) for p in DEATH_RULES)


def track(events: list[dict], chapter_law: str | None = None,
          kind: str = BILL) -> BillProgress:
    """Fold a bill's history into its furthest stage and its outcome.

    `events` are dicts with date/chamber/action, oldest first.
    """
    prog = BillProgress(events=len(events), kind=kind)
    best = -1

    for ev in events:
        action = (ev.get("action") or "").strip()
        if not action:
            continue

        stage = stage_of(action)
        if stage is not None and RANK[stage] > best:
            best = RANK[stage]
            prog.stage = stage
            prog.milestones.append(Milestone(
                stage, label_for(stage, kind), ev.get("date", ""),
                ev.get("chamber", ""), action))

        if re.match(r"^Vetoed by Governor", action, re.I):
            prog.outcome = VETOED
        elif re.match(r"^(Approved by Governor|Became Law without|Chapter No\.)",
                      action, re.I):
            prog.outcome = LAW
        elif re.match(r"^Adopted", action, re.I) and kind != BILL:
            prog.outcome = ADOPTED
            m = COMPANION.search(action)
            if m:
                prog.companion = re.sub(r"\s+", " ", m.group(1)).strip()
        elif re.search(r"filed with Secretary of State", action, re.I):
            prog.outcome = TO_BALLOT if kind == JOINT else ADOPTED
        elif is_terminal(action):
            m = COMPANION.search(action)
            if m:
                prog.companion = re.sub(r"\s+", " ", m.group(1)).strip()
                if prog.outcome not in SETTLED:
                    prog.outcome = SUPERSEDED
            elif prog.outcome not in SETTLED:
                prog.outcome = DIED
                d = re.match(r"^Died (?:in|on) (.+?)(?:[,;]|$)", action, re.I)
                if d:
                    prog.died_in = d.group(1).strip()

    # The chapter number is the authoritative record that it became law.
    if chapter_law:
        prog.chapter_law = chapter_law
        prog.outcome = LAW
        if RANK[prog.stage] < RANK["law"]:
            prog.stage = "law"

    return prog


def pathway(prog: BillProgress) -> list[dict]:
    """The full ladder with each rung marked, for rendering a progress track."""
    hit = {m.stage: m for m in prog.milestones}
    out = []
    for key, label in ladder(prog.kind):
        m = hit.get(key)
        out.append({
            "stage": key, "label": label,
            "reached": RANK[key] <= RANK[prog.stage],
            "date": m.date if m else None,
            "current": key == prog.stage,
        })
    return out
