# API contract

**Version: v8 (Oct 31: reports carry `path_lat`, `path_lon`, `path_coords`: the point and stretch of accessible path they affect; draw the pin and blocked stretch there). v7: one signed-in report or three reports confirm. v6 (Oct 31): anyone can report; trust levels, `reporter_kind`, `verified_reports`. v5 (Oct 31): route `hazards`. v4 (Oct 31): facilities fix list, report photos, plain-language errors everywhere. v3 (Oct 31): demo sign-in, barrier report trust rules, route `report_ids`. v2 (Oct 30): aliases, steepness words, climb, entrance, start snap.** Additive changes only: never rename or remove a field. Bump this line when you add something, and note it in your status file.

Source of truth in code: `backend/app/schemas.py`. Fixtures: `frontend/mock/`.

All coordinates are WGS84 (`lat`, `lon`). Distances in meters, time in minutes, grades in percent.

## GET /api/places
Buildings (with ADA entrances) and bus stops.
```json
[{"id": "bldg:baker-library", "name": "Baker Library", "kind": "building", "lat": 43.7056, "lon": -72.2886,
  "aliases": ["cafe at baker", "baker-berry"]}]
```
- IDs are `bldg:<slug of name>` and `stop:<slug of stop name>`; treat them as opaque.
- `aliases` (v2): other names from Dartmouth's search field, for search only.
`kind`: `building` | `bus_stop`. For buildings, `lat`/`lon` is the ADA entrance used as the route target.

## POST /api/route
Request:
```json
{
  "from": {"place_id": "stop:hanover-green"},
  "to": {"place_id": "bldg:baker-berry"},
  "profile": "manual_chair",
  "max_grade_pct": 8.33,
  "avoid_surfaces": ["Gravel"]
}
```
- `from` is usually `{"lat": ..., "lon": ...}` (the user's current location or a map click); `{"place_id": ...}` also works. The start snaps to the nearest path point; if that is more than ~30 m away, the route's `warnings` says so.
- `profile`: `manual_chair` (default) | `power_chair` | `walker`.
- `max_grade_pct`, `avoid_surfaces` optional.

Response:
```json
{
  "routes": [
    {
      "id": "least_effort",
      "geojson": {"type": "LineString", "coordinates": [[-72.2887, 43.7033], [-72.2886, 43.7056]]},
      "distance_m": 420.0,
      "time_min": 6.5,
      "effort": 510.0,
      "max_grade_pct": 4.1,
      "elevation_profile": [[0, 165.0], [420, 168.2]],
      "segments": [{"edge_id": "e123", "effort": 40.2, "grade_pct": 3.1, "coordinates": [[-72.2887, 43.7033], [-72.2886, 43.7040]]}],
      "warnings": [],
      "climb_m": 3.2,
      "steepness": "gentle",
      "entrance": {"id": "ent201", "name": "Baker Library", "lat": 43.7056, "lon": -72.2886}
    },
    {"id": "fastest", "...": "same shape"}
  ],
  "tradeoff_text": "Same time, noticeably less effort, avoids a very steep hill.",
  "start_snap_m": 4.2
}
```
- **Steepness words (v2):** `gentle` under 5%, `moderate` 5 to 8.33%, `steep` 8.33 to 12%, `very steep` over 12%. Show these words to users, not the percentages (plain-language rule). `max_grade_pct` and `grade_pct` stay for an optional details view.
- `warnings` and `tradeoff_text` are plain language and ready to display as-is.
- Segments (v2) also carry `material` and `steepness`.
- `least_effort` stays under the user's limit when any such route exists; otherwise it is the gentlest route that exists and its first warning says so. `fastest` ignores the limit (still no stairs) and warns when it is steeper.
Returns 404 with `{"detail": "..."}` if no accessible route exists.

## Demo sign-in (v3)
Reporting a barrier needs a signed-in account; getting a route never does. Accounts are demo accounts (no passwords). The UI must label the screen "Demo sign-in" and must not copy Dartmouth's login page or branding.
- `GET /api/auth/demo-accounts` returns `[{"id": "stu-alex", "name": "Alex (demo student)", "role": "student"}, ..., {"id": "fac-sam", "name": "Sam (demo facilities staff)", "role": "facilities"}]`.
- `POST /api/auth/demo-signin` body `{"account_id": "stu-alex"}` returns `{"token": "...", "account": {...}}`. Send `Authorization: Bearer <token>` on every report call. Tokens live in server memory (a restart signs everyone out).
- `GET /api/auth/me` returns the account, or 401.

## Route hazards (v5)
Each route has `hazards`, in route order: `[{"kind": "very_steep", "label": "Very steep hill near Reed Hall", "length_m": 42.0, "lon": -72.29, "lat": 43.70}]`.
- `kind`: `very_steep` (steeper than a standard wheelchair ramp, over 12%), `steep` (8.33 to 12%), `gravel`, `brick`, `unverified` (sidewalk from OpenStreetMap, curb ramps not confirmed), `barrier` (a reported or confirmed barrier report on the route).
- `label` is plain language; `lon`/`lat` is the middle of the stretch, for a map label.
- Static map layers (not API): `frontend/data/*.geojson` (path network by difficulty and surface, hazard points, building names, stops, entrances, parking), built by `scripts/build_map_layers.py`.

## POST /api/reports
Multipart form: `photo` (image file), `lat`, `lon` (where the barrier is), and v3: `device_lat`, `device_lon` (the phone's location when the photo was taken). Needs `Authorization`.
Response:
```json
{
  "id": "r1",
  "barrier_type": "construction",
  "severity": "high",
  "passable_for_manual_chair": false,
  "confidence": 0.86,
  "description": "Construction fencing blocks the curb ramp.",
  "lat": 43.7040, "lon": -72.2890,
  "edge_id": "e123",
  "status": "reported",
  "created_at": "2026-10-31T08:00:00Z",
  "expires_at": "2026-11-02T08:00:00Z",
  "confirmations": 0, "verified_by_facilities": false,
  "still_there_yes": 0, "still_there_no": 0, "last_confirmed_at": null,
  "photo_url": null,
  "status_text": "Reported just now. Not confirmed yet.",
  "reported_by": null
}
```
- `barrier_type`: `snow_at_curb_cut` | `ice` | `construction` | `blocked_path` | `elevator_out` | `stairs` | `other` | v3 `not_a_barrier`.
- `severity`: `low` | `medium` | `high`.
- v6 trust levels: **anyone** may report without signing in (no `Authorization`): it shows as reported with a warning and a small penalty, never a block. **Signed-in students** (v7): one signed-in report with a confident photo check, or one signed-in "still there", makes it `active` (blocks if not passable); **three independent reports** of the same barrier, with or without accounts, also make it `active`. **Facilities** reports start `active`. Anonymous reports are rate-limited per network address. `reporter_kind` and `verified_reports` describe this.
- `status` (v3 lifecycle):
  - `needs_confirm`: Claude is unsure (confidence under 0.6) or sees no barrier. Shown, no effect on routes.
  - `reported`: one confident report. Routes passing it get a warning and an effort penalty, never a block.
  - `active`: confirmed by a second student (a "still there" vote or their own report of the same spot) or verified by facilities. A report sent by a facilities account starts as `active` with `verified_by_facilities: true`, whatever Claude's confidence. Blocks the path if `passable_for_manual_chair` is false, otherwise a penalty.
  - `cleared`: two students said it's gone, or facilities rejected it or marked it fixed.
  - `expired`: 12 h after the last confirmation (48 h for construction).
- `status_text`: plain language for the UI, ready to show.
- `reported_by`: only filled in for facilities accounts; other students never see who reported.
- Errors are plain language in `detail`: 401 not signed in, 422 photo taken too far from the spot (over 30 m) or no path near the spot, 409 same photo already sent, 429 too many reports this hour (limit 3).
- How the endpoint works (Backend owns it): get the account with `auth.require_account`, call Claude vision to get a `VisionResult`, then `barriers.submit_report(account, photo_bytes, lat, lon, device_lat, device_lon, vision)`, which applies every rule above and returns the `Report` or raises `barriers.ReportRejected(status_code, detail)`.

## GET /api/reports
List of reports (same shape as above) that are not cleared or expired.

## POST /api/reports/{id}/vote (v3)
Body `{"still_there": true}`. Needs `Authorization`. One vote per account per report; the reporter's own vote does not count as a confirmation. "Yes" from another student confirms (status `active`) and resets the expiry; two "no" votes clear it. Returns the updated report.

## POST /api/reports/{id}/confirm
v1 endpoint, kept for compatibility. New UI should use `/vote`.

## POST /api/reports/{id}/verify (v3, facilities only)
Body `{"action": "confirm" | "reject" | "fixed"}`. `confirm` makes it `active`; `reject` and `fixed` make it `cleared`. 403 for student accounts.

## GET /api/reports/{id}/photo (v4)
The report's photo (JPEG, upright, at most 1568 px, location data removed). Same URL as the report's `photo_url`.

## GET /api/facilities/fixlist (v4, facilities only)
```json
{"summary": "Two barriers need attention. The blocked ramp at Main St and Wheelock cuts off the main route to the Hood.",
 "items": [{"report_id": "r1", "priority": 1, "title": "Construction fencing blocks the curb ramp",
            "why": "Confirmed by a student; wheelchair users can't get past and are rerouted.",
            "near": "Hanover Inn Dartmouth", "status": "active", "reported_at": "2026-10-31T08:00:00Z",
            "photo_url": "/api/reports/r1/photo"}],
 "drafted_by": "claude"}
```
- Open reports only. The order (`priority`) is computed by code: confirmed and impassable first, then severity, confirmations and age. Claude only writes `summary`, `title` and `why`; `drafted_by: "template"` when no API key or Claude failed. Show "Draft by AI, check before acting" when `drafted_by` is `claude`.

## Errors (v4)
Every error response is `{"detail": "<one plain-language sentence>"}`, including invalid or missing fields (422) and unexpected server errors (500). Show `detail` as-is.

## GET /api/facilities/reports (v3, facilities only)
Every report, newest first, with `reported_by` filled in. Input for the facilities fix list.

## POST /api/parse
**Cut (Oct 31): not built, returns a mock. Do not use.** Kept only because the contract is additive.
Request `{"text": "manual chair, get me to Baker-Berry by 10, nothing over 5%, avoid gravel"}`
Response: a route request (same shape as POST /api/route body) plus `{"notes": "..."}`.

## POST /api/explain
Request `{"route": <one route object>, "alternative": <other route object or null>}`
Response `{"text": "This route is 2 minutes longer but stays under 4% grade by going around the hill on Wheelock St."}`

## Routing hook (internal, not HTTP)
`backend/app/barriers.py: get_edge_modifiers() -> dict[str, float]` gives a multiplier per `edge_id` from `reported` and `active` reports (`math.inf` = blocked, only for confirmed impassable barriers). Routing multiplies segment effort by it. v3: each route lists the reports on it in `report_ids`, and its `warnings` describe them in plain language.
