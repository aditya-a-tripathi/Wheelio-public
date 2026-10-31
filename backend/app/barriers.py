"""Barrier reports: trust rules, expiry, and the hook routing uses (Backend-owned).

Rules (see "Barrier reports" in the README):
- Trust levels (v6): anyone may report without an account (warning + small penalty, never a
  block); signed-in students weigh more and two independent signed-in people block a spot;
  facilities reports start confirmed. At most RATE_LIMIT reports per account (or per network
  address, without an account) per hour.
- The photo must be new (same bytes twice is rejected), the spot must be within NEAR_M of a path
  we know, and the phone must have been within NEAR_M of the spot when the photo was taken.
- Claude unsure (confidence < MIN_CONFIDENCE) or "not_a_barrier" -> needs_confirm: shown only.
- One confident report -> reported: warning + effort penalty on that path, never a block.
- A second student (a "still there" vote, or their own report of the same spot) or facilities
  -> active: blocks the path if not passable for a manual chair.
- A facilities staff account's own report starts active and verified, whatever Claude's
  confidence: the role is the credential (real version: staff directory via single sign-on).
- Two "it's gone" votes, or facilities reject / fixed -> cleared.
- Reports expire EXPIRY_H after the last confirmation (construction: CONSTRUCTION_EXPIRY_H).

Storage is in memory. The reporter's account is kept for accountability and only shown to
facilities. Reports never store anyone's trips or location history; the phone location sent
with a report is used for the distance check and then discarded.

Frontend+AI's POST /api/reports calls submit_report(); routing calls get_edge_modifiers().
"""
import hashlib
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .schemas import Account, Report, VisionResult

RATE_LIMIT = 3  # reports per account per hour
NEAR_M = 30.0
SAME_SPOT_M = 15.0  # two reports this close are the same barrier
MIN_CONFIDENCE = 0.6
EXPIRY_H = 12
CONSTRUCTION_EXPIRY_H = 48
CLEAR_VOTES = 2
# Testing from off campus only: skip the "phone was near the spot" check.
ALLOW_REMOTE = os.environ.get("WHEELIO_ALLOW_REMOTE_REPORTS") == "1"

# Effort multipliers. Unconfirmed reports only ever add caution; only confirmed impassable
# barriers block (math.inf).
REPORTED_MULT = {"low": 1.3, "medium": 1.6, "high": 2.0}
ANON_MULT = {"low": 1.15, "medium": 1.3, "high": 1.5}  # no account yet: lighter
ANON_IMPASSABLE_MULT = 1.8
VERIFIED_TO_BLOCK = 1  # one signed-in person (with a confident photo check) confirms it
REPORTS_TO_BLOCK = 3  # or this many independent reports of the same barrier, accounts or not
REPORTED_IMPASSABLE_MULT = 3.0
ACTIVE_MULT = {"low": 1.5, "medium": 2.5, "high": 4.0}
SEVERITY_MULTIPLIER = ACTIVE_MULT  # v1 name, kept for compatibility


class ReportRejected(Exception):
    """Raised by submit_report; the endpoint turns it into HTTPException(status_code, detail)."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class _Private:  # what the public Report must not show
    reporter: str | None = None
    votes: dict[str, bool] = field(default_factory=dict)  # account id -> still there?
    matching_reporters: set[str] = field(default_factory=set)
    verified: set[str] = field(default_factory=set)  # signed-in accounts who reported or said "still there"
    cleared_reason: str = ""


_reports: dict[str, Report] = {}
_private: dict[str, _Private] = {}
_photo_hashes: dict[str, str] = {}  # sha256 -> report id
_submissions: dict[str, list[datetime]] = {}  # account id -> times reported


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _ago(s: str) -> str:
    mins = int((_now() - _parse(s)).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    hours = mins // 60
    return f"{hours} hour{'s' if hours > 1 else ''} ago"


def _meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * 111_132.0
    dx = (lon2 - lon1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def _expiry(report: Report, start: datetime) -> str:
    hours = CONSTRUCTION_EXPIRY_H if report.barrier_type == "construction" else EXPIRY_H
    return _iso(start + timedelta(hours=hours))


def _refresh(r: Report) -> Report:
    """Apply expiry, recount votes and write the plain-language status line."""
    p = _private.setdefault(r.id, _Private())
    if r.status not in ("cleared", "expired") and _parse(r.expires_at) < _now():
        r.status = "expired"
    r.still_there_yes = sum(v for v in p.votes.values())
    r.still_there_no = sum(not v for v in p.votes.values())
    others_yes = {a for a, v in p.votes.items() if v and a != p.reporter}
    r.confirmations = len(others_yes | p.matching_reporters)
    r.verified_reports = len(p.verified)
    if r.status == "needs_confirm":
        r.status_text = (f"Reported {_ago(r.created_at)}. Our photo check wasn't sure, so it isn't "
                         "changing routes until someone confirms it.")
    elif r.status == "reported" and not p.verified:
        r.status_text = (f"Reported without an account {_ago(r.created_at)}. Not confirmed yet: a campus "
                         "account or two more reports confirm it.")
    elif r.status == "reported":
        r.status_text = f"Reported {_ago(r.created_at)}. One more campus report confirms it."
    elif r.status == "active":
        who = ("by facilities staff" if r.verified_by_facilities else "by a student" if p.verified
               else "by several reports")
        r.status_text = f"Confirmed {who}, {_ago(r.last_confirmed_at or r.created_at)}."
    elif r.status == "cleared":
        r.status_text = p.cleared_reason or "Cleared."
    else:
        r.status_text = "This report has expired."
    return r


# ---- used by Frontend+AI's POST /api/reports -------------------------------------------------

def submit_report(account: Account | None, photo: bytes, lat: float, lon: float,
                  device_lat: float | None, device_lon: float | None, vision: VisionResult,
                  photo_url: str | None = None, client_key: str = "unknown") -> Report:
    """Apply every rule and store the report. Raises ReportRejected with a plain-language detail.

    account None = reported without signing in (keyed by network address for limits)."""
    from .routing.graph import load, snap  # late import: routing loads the graph on first use

    if account is None:
        account = Account(id=f"anon:{client_key}", name="Anonymous", role="student")
        signed_in = False
    else:
        signed_in = True
    now = _now()
    recent = [t for t in _submissions.get(account.id, []) if now - t < timedelta(hours=1)]
    if len(recent) >= RATE_LIMIT:
        raise ReportRejected(429, "You've sent 3 reports in the last hour. Please try again later.")

    digest = hashlib.sha256(photo).hexdigest()
    if digest in _photo_hashes:
        raise ReportRejected(409, "This photo was already sent. Please take a new one.")

    graph = load()
    edge_id, along_m, snap_m = snap(graph, lon, lat)
    if snap_m > NEAR_M:
        raise ReportRejected(422, "That spot isn't near a path we know. Move the pin onto the path "
                                  "where the barrier is.")
    if not ALLOW_REMOTE:
        if device_lat is None or device_lon is None:
            raise ReportRejected(422, "We need your phone's location to check you're at the barrier. "
                                      "Please allow location and try again.")
        if _meters(device_lat, device_lon, lat, lon) > NEAR_M:
            raise ReportRejected(422, "You need to be at the barrier to report it. Take the photo "
                                      "within about 30 m of the spot.")

    confident = vision.barrier_type != "not_a_barrier" and vision.confidence >= MIN_CONFIDENCE
    staff = signed_in and account.role == "facilities"

    # Same spot already reported? A different student's confident report confirms it.
    for r in list_reports():
        # Same barrier only if it's really the same spot. (Matching on the path segment merged a
        # report into an older one 50 m away on a long segment, and the map showed the old spot.)
        if _meters(r.lat, r.lon, lat, lon) > SAME_SPOT_M:
            continue
        p = _private[r.id]
        if account.id == p.reporter or account.id in p.matching_reporters:
            raise ReportRejected(409, "You already reported this spot. Thanks!")
        if staff:
            r.verified_by_facilities = True
            _photo_hashes[digest] = r.id
            _confirm(r, now)
            return _refresh(r)
        if confident and r.barrier_type != "not_a_barrier":
            p.matching_reporters.add(account.id)
            if signed_in:
                p.verified.add(account.id)
            _photo_hashes[digest] = r.id
            _submissions[account.id] = recent + [now]
            if len(p.verified) >= VERIFIED_TO_BLOCK or 1 + len(p.matching_reporters) >= REPORTS_TO_BLOCK:
                _confirm(r, now)
            elif r.status == "needs_confirm":
                r.status = "reported"
                r.expires_at = _expiry(r, now)
            return _refresh(r)

    fields = vision.model_dump()
    if staff and fields["barrier_type"] == "not_a_barrier":
        fields["barrier_type"] = "other"  # staff say it's a barrier; trust them over the photo check
    if staff and fields["confidence"] == 0.0:
        # Photo unchecked (no AI): staff reported a barrier, so assume it blocks until they say otherwise.
        fields.update(passable_for_manual_chair=False, severity="high",
                      description="Facilities staff reported a barrier here.")
    report = Report(
        id=f"r{len(_reports) + 1}",
        **fields,
        lat=lat, lon=lon, edge_id=edge_id,
        **_path_point(graph, edge_id, along_m),
        status="active" if staff or (signed_in and confident) else ("reported" if confident else "needs_confirm"),
        verified_by_facilities=staff,
        reporter_kind="facilities" if staff else "student" if signed_in else "anonymous",
        created_at=_iso(now), expires_at=_iso(now),
        photo_url=photo_url,
    )
    report.expires_at = _expiry(report, now)
    if staff:
        report.last_confirmed_at = report.created_at
    _reports[report.id] = report
    _private[report.id] = _Private(reporter=account.id, verified={account.id} if signed_in else set())
    if report.status == "active":
        report.last_confirmed_at = report.created_at
    _photo_hashes[digest] = report.id
    _submissions[account.id] = recent + [now]
    return _refresh(report)


def _path_point(graph, edge_id: str, along_m: float) -> dict:
    """The exact point on the path the report is attached to, and the stretch of path it affects
    (the whole segment: that's what routing avoids), so the map draws both where they really are."""
    from .routing.graph import to_lonlat

    line = graph.lines_m[graph.line_ids.index(edge_id)]
    lon, lat = to_lonlat(*line.interpolate(along_m).coords[0])
    return {"path_lat": round(lat, 7), "path_lon": round(lon, 7), "path_coords": graph.edges[edge_id]["coordinates"]}


def _confirm(r: Report, now: datetime) -> None:
    r.status = "active"
    r.last_confirmed_at = _iso(now)
    r.expires_at = _expiry(r, now)


# ---- votes and facilities (Backend's routers/trust.py) ----------------------------------------

def vote(report_id: str, account: Account, still_there: bool) -> Report:
    r = _reports.get(report_id)
    if r is None or r.status in ("cleared", "expired"):
        raise ReportRejected(404, "That report is no longer open.")
    p = _private[report_id]
    p.votes[account.id] = still_there
    now = _now()
    if still_there:
        if account.id != p.reporter:
            p.verified.add(account.id)  # a second signed-in person saw it
            if len(p.verified) >= VERIFIED_TO_BLOCK or account.role == "facilities":
                _confirm(r, now)
            elif r.status == "needs_confirm":
                r.status = "reported"
            r.expires_at = _expiry(r, now)
        else:
            r.expires_at = _expiry(r, now)  # reporter says it's still there: keep it alive
    else:
        no = sum(not v for v in p.votes.values())
        yes = sum(v for v in p.votes.values())
        if no >= CLEAR_VOTES and no > yes:
            r.status = "cleared"
            p.cleared_reason = "Cleared: people passing by said it's gone."
    return _refresh(r)


def verify(report_id: str, action: str) -> Report:
    r = _reports.get(report_id)
    if r is None:
        raise ReportRejected(404, "We couldn't find that report.")
    p = _private[report_id]
    if action == "confirm":
        r.verified_by_facilities = True
        _confirm(r, _now())
    elif action == "reject":
        r.status = "cleared"
        p.cleared_reason = "Cleared: facilities staff checked and found no barrier."
    else:
        r.status = "cleared"
        p.cleared_reason = "Cleared: facilities staff fixed it."
    return _refresh(r)


def facilities_view() -> list[Report]:
    """Every report, newest first, with the reporter's name (facilities accounts only)."""
    from .auth import display_name

    out = []
    for r in sorted(_reports.values(), key=lambda r: r.created_at, reverse=True):
        _refresh(r)
        reporter = _private[r.id].reporter
        name = "Anonymous (no account)" if reporter and reporter.startswith("anon:") else display_name(reporter) if reporter else None
        out.append(r.model_copy(update={"reported_by": name}))
    return out


# ---- v1 functions (Frontend+AI's stub calls these) --------------------------------------------

def add_report(report: Report) -> Report:
    _reports[report.id] = report
    _private.setdefault(report.id, _Private())
    return report


def get_report(report_id: str) -> Report | None:
    r = _reports.get(report_id)
    return _refresh(r) if r else None


def list_reports() -> list[Report]:
    """Open reports (not cleared or expired), for everyone. Never includes who reported."""
    return [r for r in (_refresh(r) for r in _reports.values()) if r.status not in ("cleared", "expired")]


# ---- routing hooks ----------------------------------------------------------------------------

def get_edge_modifiers() -> dict[str, float]:
    """edge_id -> effort multiplier (math.inf = blocked). needs_confirm reports don't count."""
    mods: dict[str, float] = {}
    for r in list_reports():
        if r.edge_id is None or r.status not in ("reported", "active"):
            continue
        if r.status == "active":
            m = math.inf if not r.passable_for_manual_chair else ACTIVE_MULT[r.severity]
        elif not _private.get(r.id, _Private()).verified:
            m = ANON_IMPASSABLE_MULT if not r.passable_for_manual_chair else ANON_MULT[r.severity]
        else:
            m = REPORTED_IMPASSABLE_MULT if not r.passable_for_manual_chair else REPORTED_MULT[r.severity]
        mods[r.edge_id] = max(mods.get(r.edge_id, 1.0), m)
    return mods


def blocked_points() -> list[tuple[float, float]]:
    """(lon, lat) of confirmed impassable barriers, for routing a start on the same segment."""
    return [(r.lon, r.lat) for r in list_reports()
            if r.status == "active" and not r.passable_for_manual_chair]


def annotate_route(route: dict) -> dict:
    """Add report_ids and plain-language warnings for reports on this route's paths."""
    edges = {s["edge_id"] for s in route["segments"]}
    route.setdefault("report_ids", [])
    for r in list_reports():
        if r.edge_id not in edges or r.status not in ("reported", "active"):
            continue
        route["report_ids"].append(r.id)
        route.setdefault("hazards", []).append({
            "kind": "barrier", "lon": r.lon, "lat": r.lat, "length_m": 0.0,
            "label": ("Confirmed barrier: " if r.status == "active" else "Reported barrier: ") + r.description})
        if r.status == "reported":
            route["warnings"].append(f"Someone reported a barrier on this route {_ago(r.created_at)}, "
                                     f"not confirmed yet: {r.description}")
        else:
            route["warnings"].append(f"Confirmed barrier on this route: {r.description}")
    return route
