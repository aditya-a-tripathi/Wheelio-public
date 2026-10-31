"""Facilities fix list (Backend-owned).

Code decides the ORDER (confirmed impassable first, then severity, confirmations, age) so the
ranking is predictable and explainable; Claude only writes the summary, titles and one-line
reasons for staff, labeled as an AI draft. Without a key, a template writes them.
Staff decide what to fix; this list only helps them see it.
"""
import json
from datetime import datetime, timezone

from pydantic import BaseModel

from .. import barriers
from ..schemas import FixItem, FixList, Report
from . import client
from .explain import _near

SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}
STATUS_RANK = {"active": 3, "reported": 2, "needs_confirm": 1}
TYPE_WORDS = {"snow_at_curb_cut": "Snow at a curb cut", "ice": "Ice on the path", "construction": "Construction",
              "blocked_path": "Something blocking the path", "elevator_out": "Elevator out of service",
              "stairs": "Steps with no ramp", "other": "Barrier", "not_a_barrier": "Possible barrier"}

SYSTEM = """You draft a short fix list for campus facilities staff from barrier reports that \
students and staff sent about paths used by wheelchair users. The list is already in priority \
order; keep the order and the report ids exactly.

For each report write:
- title: under 10 words, what and where (use the nearby building if given).
- why: one sentence on who it affects and why it matters now (impassable, confirmed by others, \
how long it has been open). Use only the facts given.
And a summary: 1-2 sentences for the top of the list.

Plain language, no em dashes, no names of reporters, no invented details or measurements."""


class _Line(BaseModel):
    report_id: str
    title: str
    why: str


class _Draft(BaseModel):
    summary: str
    items: list[_Line]


def _score(r: Report) -> tuple:
    impassable = r.status == "active" and not r.passable_for_manual_chair
    return (impassable, STATUS_RANK.get(r.status, 0), SEVERITY_RANK.get(r.severity, 0),
            r.confirmations + r.verified_by_facilities, -_hours_open(r))


def _hours_open(r: Report) -> float:
    created = datetime.fromisoformat(r.created_at.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - created).total_seconds() / 3600


def _template(r: Report, near: str | None) -> tuple[str, str]:
    what = TYPE_WORDS.get(r.barrier_type, "Barrier")
    title = f"{what} near {near}" if near else what
    if r.status == "active" and not r.passable_for_manual_chair:
        why = "Confirmed and wheelchair users can't get past, so routes go around it."
    elif r.status == "active":
        why = "Confirmed; wheelchair users can get past but it slows them down."
    elif r.status == "reported":
        why = "One report so far, not yet confirmed. Routes treat it with caution."
    else:
        why = "The photo check wasn't sure. Needs someone to look."
    hours = _hours_open(r)
    if hours >= 1:
        why += f" Open for {int(hours)} hour{'s' if hours >= 2 else ''}."
    return title, why


def fix_list() -> FixList:
    reports = sorted(barriers.list_reports(), key=_score, reverse=True)
    items = []
    for i, r in enumerate(reports, 1):
        near = _near(r.lon, r.lat)
        title, why = _template(r, near)
        items.append(FixItem(report_id=r.id, priority=i, title=title, why=why, near=near, status=r.status,
                             reported_at=r.created_at, photo_url=r.photo_url))
    if not items:
        return FixList(summary="No open barrier reports right now.", items=[], drafted_by="template")
    impassable = sum(1 for r in reports if r.status == "active" and not r.passable_for_manual_chair)
    summary = (f"{len(items)} open report{'s' if len(items) > 1 else ''}"
               + (f", {impassable} blocking wheelchair routes right now." if impassable else "."))

    facts = [{"report_id": it.report_id, "priority": it.priority, "type": r.barrier_type,
              "description": r.description, "near_building": it.near, "status": r.status,
              "passable_for_manual_chair": r.passable_for_manual_chair, "severity": r.severity,
              "confirmed_by_others": r.confirmations, "verified_by_facilities": r.verified_by_facilities,
              "hours_open": round(_hours_open(r), 1)} for it, r in zip(items, reports)]
    try:
        draft = client.structured(SYSTEM, "Reports in priority order:\n" + json.dumps(facts, indent=1),
                                  _Draft, effort="low")
    except client.AIUnavailable:
        return FixList(summary=summary, items=items, drafted_by="template")
    lines = {ln.report_id: ln for ln in draft.items}
    if set(lines) != {it.report_id for it in items}:
        return FixList(summary=summary, items=items, drafted_by="template")  # Claude dropped or added one
    for it in items:
        it.title = lines[it.report_id].title.replace("—", ", ")
        it.why = lines[it.report_id].why.replace("—", ", ")
    return FixList(summary=draft.summary.replace("—", ", "), items=items, drafted_by="claude")
