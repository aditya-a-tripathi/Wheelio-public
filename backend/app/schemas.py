"""API contract v6. Shared file: additive changes only, mirror every change in docs/api.md."""
from typing import Literal

from pydantic import BaseModel, Field

Profile = Literal["manual_chair", "power_chair", "walker"]
# Plain-language steepness: gentle < 5%, moderate 5-8.33%, steep 8.33-12%, very steep > 12%.
Steepness = Literal["gentle", "moderate", "steep", "very steep"]
BarrierType = Literal[
    "snow_at_curb_cut", "ice", "construction", "blocked_path", "elevator_out", "stairs", "other",
    "not_a_barrier",  # v3: Claude sees no barrier (or no path) in the photo
]
# v3 report lifecycle. needs_confirm: Claude unsure, no effect on routes. reported: one confident
# report, routes get a warning and a penalty. active: confirmed by a second student or facilities,
# blocks the path if not passable. cleared: gone (votes or facilities). expired: timed out.
ReportStatus = Literal["needs_confirm", "reported", "active", "cleared", "expired"]


class Place(BaseModel):
    id: str
    name: str
    kind: Literal["building", "bus_stop"]
    lat: float
    lon: float
    aliases: list[str] = []  # v2: other names to search by ("cafe at baker")


class Endpoint(BaseModel):
    place_id: str | None = None
    lat: float | None = None
    lon: float | None = None


class RouteRequest(BaseModel):
    from_: Endpoint = Field(alias="from")
    to: Endpoint
    profile: Profile = "manual_chair"
    max_grade_pct: float | None = None
    avoid_surfaces: list[str] = []

    model_config = {"populate_by_name": True}


class Segment(BaseModel):
    edge_id: str
    effort: float
    grade_pct: float
    coordinates: list[list[float]]
    material: str = ""  # v2: Concrete, Pavement, Brick, Gravel, Crosswalk
    steepness: Steepness = "gentle"  # v2


class Entrance(BaseModel):
    id: str
    name: str
    lat: float
    lon: float


class Hazard(BaseModel):  # v5: something on a route a wheelchair user should know about
    kind: Literal["very_steep", "steep", "gravel", "brick", "unverified", "barrier"]
    label: str  # plain language, ready to show ("Very steep hill near Reed Hall")
    length_m: float = 0.0
    lon: float
    lat: float


class Route(BaseModel):
    id: Literal["least_effort", "fastest"]
    geojson: dict
    distance_m: float
    time_min: float
    effort: float
    max_grade_pct: float
    elevation_profile: list[list[float]]
    segments: list[Segment]
    warnings: list[str] = []  # plain language, ready to show
    climb_m: float = 0.0  # v2: total meters climbed
    steepness: Steepness = "gentle"  # v2: steepest part of the route, in words
    entrance: Entrance | None = None  # v2: the ADA entrance this route ends at
    report_ids: list[str] = []  # v3: barrier reports on this route (reported or active)
    hazards: list[Hazard] = []  # v5: in route order; show them up front on the card and map


class RouteResponse(BaseModel):
    routes: list[Route]
    tradeoff_text: str  # plain language
    start_snap_m: float = 0.0  # v2: distance from the start to the nearest accessible path


class Report(BaseModel):
    id: str
    barrier_type: BarrierType
    severity: Literal["low", "medium", "high"]
    passable_for_manual_chair: bool
    confidence: float
    description: str
    lat: float
    lon: float
    edge_id: str | None = None
    status: ReportStatus
    created_at: str
    expires_at: str
    # v3
    confirmations: int = 0  # other students who said it's there (vote or a matching report)
    verified_by_facilities: bool = False
    still_there_yes: int = 0
    still_there_no: int = 0
    last_confirmed_at: str | None = None
    photo_url: str | None = None
    status_text: str = ""  # plain language, ready to show ("Reported 20 min ago, not yet confirmed")
    reported_by: str | None = None  # display name; only filled in for facilities accounts
    reporter_kind: Literal["anonymous", "student", "facilities"] | None = None  # v6: trust level of the first report
    verified_reports: int = 0  # v6: signed-in people (reports or "still there") behind it; 1 blocks it (v7)
    path_lat: float | None = None  # v8: the point on the accessible path the report is attached to
    path_lon: float | None = None
    path_coords: list[list[float]] | None = None  # v8: the stretch of path it affects ([lon, lat] pairs)


class Account(BaseModel):  # v3: demo sign-in
    id: str
    name: str
    role: Literal["student", "facilities"]


class SignInRequest(BaseModel):  # v3
    account_id: str


class SignInResponse(BaseModel):  # v3
    token: str
    account: Account


class VisionResult(BaseModel):  # v3: what ai/vision.py returns to barriers.submit_report()
    barrier_type: BarrierType
    severity: Literal["low", "medium", "high"]
    passable_for_manual_chair: bool
    confidence: float
    description: str


class VoteRequest(BaseModel):  # v3
    still_there: bool


class VerifyRequest(BaseModel):  # v3: facilities only
    action: Literal["confirm", "reject", "fixed"]


class ConfirmRequest(BaseModel):
    still_there: bool


class ParseRequest(BaseModel):
    text: str


class ParseResponse(RouteRequest):
    notes: str = ""


class ExplainRequest(BaseModel):
    route: Route
    alternative: Route | None = None


class ExplainResponse(BaseModel):
    text: str


class FixItem(BaseModel):  # v4: one line of the facilities fix list
    report_id: str
    priority: int  # 1 = fix first; order is computed by code, not by Claude
    title: str  # plain language, e.g. "Blocked curb ramp at Main St and Wheelock"
    why: str  # one sentence: who it affects and why it's urgent
    near: str | None = None  # nearest building
    status: ReportStatus
    reported_at: str
    photo_url: str | None = None


class FixList(BaseModel):  # v4
    summary: str  # 1-2 sentences for the top of the list
    items: list[FixItem]
    drafted_by: Literal["claude", "template"]  # shown in the UI: an AI draft for staff to check
