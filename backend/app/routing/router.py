"""Least-effort and fastest accessible routes from any point to a building's ADA entrance.

Flow: snap the start onto the nearest stairs-free path -> Dijkstra over directed arcs (effort
or time as the weight) -> pick the destination entrance that is cheapest to reach -> stitch
geometry, elevation profile and stats.
"""
import heapq
import math
import re

import numpy as np
from shapely.geometry import LineString
from shapely.ops import substring

from .effort import PROFILES, cost_and_time
from .graph import Graph, load, snap, to_lonlat, to_m

SNAP_WARN_M = 30.0
FAR_M = 1500.0  # a start this far from any path is off campus
MIN_LIMIT, MAX_LIMIT = 3.0, 20.0  # sane bounds for a user's steepness limit (%)
# Plain-language steepness: words for users, numbers stay in the data.
STEEPNESS = [(5.0, "gentle"), (8.33, "moderate"), (12.0, "steep"), (math.inf, "very steep")]
RAMP_MAX = 8.33  # ADA ramp maximum; only mentioned to users as "a wheelchair ramp"


def steepness(grade_pct: float) -> str:
    for upper, word in STEEPNESS:
        if grade_pct < upper or upper == math.inf:
            return word
    return "very steep"


def _rank(word: str) -> int:
    return [w for _, w in STEEPNESS].index(word)


def _too_steep(word: str, user_limit: float) -> str:
    if word in ("steep", "very steep") and user_limit <= RAMP_MAX:
        return f"Has a {word} hill, steeper than a standard wheelchair ramp."
    return f"Has a {word} hill, steeper than your setting."


class NoRoute(Exception):
    pass


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def buildings(graph: Graph) -> dict[str, dict]:
    """bldg:<slug> -> {name, search, entrances}. Only buildings with an ADA entrance."""
    out: dict[str, dict] = {}
    for ent in graph.entrances:
        b = out.setdefault(f"bldg:{slug(ent['name'])}", {"name": ent["name"], "search": ent["search"],
                                                         "entrances": []})
        b["entrances"].append(ent)
    return out


def _stop_destination(graph: Graph, place_id: str) -> dict | None:
    """A bus or shuttle stop as a destination: the route ends at the path point nearest the stop."""
    if not place_id.startswith("stop:"):
        return None
    for s in graph.bus_stops:
        if f"stop:{slug(s['name'])}" == place_id:
            edge_id, t, d = snap(graph, s["lon"], s["lat"])
            e = graph.edges[edge_id]
            node, along = (e["u"], t) if t <= e["length_m"] / 2 else (e["v"], e["length_m"] - t)
            return {"name": s["name"], "entrances": [{"id": f"stop-{slug(s['name'])}", "name": f"{s['name']} stop",
                                                       "lon": s["lon"], "lat": s["lat"], "node": node,
                                                       "snap_m": round(d + along, 1)}]}
    return None


def resolve_start(graph: Graph, start: dict) -> tuple[float, float, str]:
    """Start as (lon, lat, description) from {lat, lon} or {place_id}."""
    if start.get("lat") is not None and start.get("lon") is not None:
        return start["lon"], start["lat"], "your location"
    pid = start.get("place_id") or ""
    if pid.startswith("bldg:") and pid in buildings(graph):
        ent = buildings(graph)[pid]["entrances"][0]
        return ent["lon"], ent["lat"], ent["name"]
    if pid.startswith("stop:"):
        for s in graph.bus_stops:
            if f"stop:{slug(s['name'])}" == pid:
                return s["lon"], s["lat"], s["name"]
    raise NoRoute("We couldn't find that starting place.")


def _search(graph, sources, weight, profile, limit, avoid, mods):
    """Dijkstra from virtual start pieces. Returns (dist, prev) over real nodes."""
    dist: dict[str, float] = {}
    prev: dict[str, tuple] = {}
    heap = []
    for node, w, piece in sources:
        if w < dist.get(node, math.inf):
            dist[node], prev[node] = w, (None, piece)
            heapq.heappush(heap, (w, node))
    while heap:
        d, n = heapq.heappop(heap)
        if d > dist.get(n, math.inf):
            continue
        for arc in graph.adj[n]:
            e = graph.edges[arc.edge_id]
            m = mods.get(arc.edge_id, 1.0)
            if e["material"] == "Stairs" or e["material"] in avoid or arc.steep_pct > limit or math.isinf(m):
                continue
            w = (arc.effort[profile] * m) if weight == "effort" else arc.time_s[profile]
            nd = d + w
            if nd < dist.get(arc.to, math.inf):
                dist[arc.to] = nd
                piece = (arc.edge_id, 0.0, e["length_m"], arc.forward)
                prev[arc.to] = (n, piece)
                heapq.heappush(heap, (nd, arc.to))
    return dist, prev


def _route(graph, start_piece_sources, targets, weight, profile, limit, avoid, mods):
    dist, prev = _search(graph, start_piece_sources, weight, profile, limit, avoid, mods)
    best, best_cost = None, math.inf
    for ent in targets:
        c = dist.get(ent["node"], math.inf) + ent["snap_m"]
        if c < best_cost:
            best, best_cost = ent, c
    if best is None:
        return None
    pieces, n = [], best["node"]
    while True:
        p, piece = prev[n]
        pieces.append(piece)
        if p is None:
            break
        n = p
    return best, pieces[::-1]


def _start_sources(graph, edge_id, t, weight, profile, barriers_at=()):
    """Virtual start in the middle of an edge: reach u by going back, v by going forward.

    barriers_at: distances along this edge of confirmed impassable barriers; the start can't
    leave in a direction that passes one.
    """
    e = graph.edges[edge_id]
    L = e["length_m"]
    t = min(max(t, 0.0), L)
    out = []
    for node, a, b, fwd in ((e["u"], 0.0, t, False), (e["v"], t, L, True)):
        if any(a <= x <= b for x in barriers_at):
            continue
        eff, sec = cost_and_time(e, a, b, fwd, PROFILES[profile])
        out.append((node, eff if weight == "effort" else sec, (edge_id, a, b, fwd)))
    return out


def _build(graph, rid, pieces, ent, profile, user_limit):
    p = PROFILES[profile]
    coords, segments, prof_pts = [], [], []
    total_d = total_eff = total_t = climb = 0.0
    max_grade, conflicts, osm_m = 0.0, 0, 0.0
    for edge_id, a, b, fwd in pieces:
        e = graph.edges[edge_id]
        if b - a < 0.01:
            continue
        line = substring(_line_m(graph, e), a, b)
        seg_coords = [to_lonlat(x, y) for x, y in line.coords]
        if not fwd:
            seg_coords.reverse()
        eff, sec = cost_and_time(e, a, b, fwd, p)
        # elevation along this piece, in travel order
        d = np.array([q[0] for q in e["elev"]])
        z = np.array([q[1] for q in e["elev"]])
        ds = np.linspace(a, b, max(2, int((b - a) // 5) + 2))
        zs = np.interp(ds, d, z)
        if not fwd:
            zs = zs[::-1]
        for k, (dd, zz) in enumerate(zip(ds - a, zs)):
            if k or not prof_pts:
                prof_pts.append([round(total_d + float(dd), 1), round(float(zz), 1)])
        if e["grade_source"] == "label":  # flattened profile; use the label grade as effort does
            climb += e["max_abs_grade_pct"] * (b - a) / 200.0
        else:
            climb += float(np.clip(np.diff(zs), 0, None).sum())
        coords.extend(seg_coords if not coords else seg_coords[1:])
        grade = e["max_abs_grade_pct"]
        max_grade = max(max_grade, grade)
        conflicts += e["grade_conflict"]
        osm_m += (b - a) if e.get("source") == "osm" else 0.0
        segments.append({"edge_id": edge_id, "effort": round(eff, 1), "grade_pct": round(grade, 1),
                         "coordinates": seg_coords, "material": e["material"], "steepness": steepness(grade)})
        total_d += b - a
        total_eff += eff
        total_t += sec
    # last stretch from the path to the door
    total_eff += ent["snap_m"]
    total_d += ent["snap_m"]
    total_t += ent["snap_m"] / p.speed_mps
    coords.append([ent["lon"], ent["lat"]])

    warnings = []
    if max_grade > user_limit:
        warnings.append(_too_steep(steepness(max_grade), user_limit))
    if ent["snap_m"] > SNAP_WARN_M:
        warnings.append(f"The last {round(ent['snap_m'])} m to the door aren't on our map. "
                        "Check the approach when you arrive.")
    if conflicts:
        warnings.append("Our slope information is less certain on part of this route.")
    # OSM sidewalks still cost a little more (effort.SOURCE); users found the note noise (Oct 31).
    return {
        "id": rid,
        "hazards": _hazards(graph, segments),
        "geojson": {"type": "LineString", "coordinates": coords},
        "distance_m": round(total_d, 1),
        "time_min": round(total_t / 60, 1),
        "effort": round(total_eff, 1),
        "max_grade_pct": round(max_grade, 1),
        "climb_m": round(climb, 1),
        "steepness": steepness(max_grade),
        "elevation_profile": prof_pts,
        "segments": segments,
        "warnings": warnings,
        "entrance": {"id": ent["id"], "name": ent["name"], "lat": ent["lat"], "lon": ent["lon"]},
    }


HAZARD_WORDS = {"very_steep": "Very steep hill", "steep": "Steep hill", "gravel": "Gravel",
                "brick": "Brick", "unverified": "Sidewalk with unconfirmed curb ramps"}
HAZARD_MIN_M = {"very_steep": 5.0, "steep": 8.0, "gravel": 10.0, "brick": 10.0, "unverified": 20.0}


def _hazards(graph, segments: list[dict]) -> list[dict]:
    """Stretches of the route a wheelchair user should know about, in route order."""
    from ..ai.explain import _near  # nearest building name

    def kinds(s):
        e = graph.edges[s["edge_id"]]
        out = []
        if s["steepness"] in ("steep", "very steep"):
            out.append("hill")  # one hill, even if it alternates steep / very steep
        if e["material"] in ("Gravel", "Brick"):
            out.append(e["material"].lower())
        return out

    runs: list[dict] = []
    open_runs: dict[str, dict] = {}
    for s in segments:
        ks = kinds(s)
        length = sum(_meters(s["coordinates"][i], s["coordinates"][i + 1]) for i in range(len(s["coordinates"]) - 1))
        for k in list(open_runs):
            if k not in ks:
                del open_runs[k]
        for k in ks:
            run = open_runs.get(k)
            if run is None:
                run = {"kind": k, "length_m": 0.0, "coords": [], "worst": "steep"}
                open_runs[k] = run
                runs.append(run)
            run["length_m"] += length
            run["coords"] += s["coordinates"]
            if s["steepness"] == "very steep":
                run["worst"] = "very_steep"
    out = []
    for r in runs:
        if r["kind"] == "hill":
            r["kind"] = r["worst"]
        if r["length_m"] < HAZARD_MIN_M[r["kind"]]:
            continue
        mid = LineString(r["coords"]).interpolate(0.5, normalized=True) if len(r["coords"]) > 1 else None
        lon, lat = (mid.x, mid.y) if mid else tuple(r["coords"][0])
        near = _near(lon, lat)
        label = HAZARD_WORDS[r["kind"]] + (f" near {near}" if near else "")
        out.append({"kind": r["kind"], "label": label, "length_m": round(r["length_m"], 1),
                    "lon": round(lon, 6), "lat": round(lat, 6)})
    return out


def _meters(a, b) -> float:
    dy = (a[1] - b[1]) * 111_132.0
    dx = (a[0] - b[0]) * 111_320.0 * math.cos(math.radians(a[1]))
    return math.hypot(dx, dy)


_line_cache: dict[str, LineString] = {}


def _line_m(graph, e):
    if e["id"] not in _line_cache:
        _line_cache[e["id"]] = LineString([to_m(*c) for c in e["coordinates"]])
    return _line_cache[e["id"]]


def _tradeoff(least, fastest) -> str:
    """One plain-language line comparing the two routes."""
    if [x["edge_id"] for x in least["segments"]] == [x["edge_id"] for x in fastest["segments"]]:
        return "The fastest route is also the easiest one."
    dt = least["time_min"] - fastest["time_min"]
    if abs(dt) < 0.5:
        parts = ["Same time"]
    else:
        mins = max(1, round(abs(dt)))
        parts = [f"About {mins} min {'longer' if dt > 0 else 'shorter'}"]
    saved = 100 * (1 - least["effort"] / fastest["effort"]) if fastest["effort"] else 0
    if saved >= 25:
        parts.append("much less effort")
    elif saved >= 10:
        parts.append("noticeably less effort")
    elif saved >= 3:
        parts.append("a bit less effort")
    if _rank(fastest["steepness"]) > _rank(least["steepness"]) and fastest["steepness"] != "gentle":
        parts.append(f"avoids a {fastest['steepness']} hill")
    if len(parts) == 1:
        parts.append("about the same effort either way")
    return ", ".join(parts) + "."


def route(start: dict, to_place_id: str, profile: str = "manual_chair", max_grade_pct: float | None = None,
          avoid_surfaces: list[str] | None = None, edge_modifiers: dict[str, float] | None = None,
          blocked_points: list[tuple[float, float]] = ()) -> dict:
    """blocked_points: (lon, lat) of confirmed impassable barriers, so a start on the same path
    segment as one can't route through it (whole-segment blocking already covers everyone else)."""
    graph = load()
    if profile not in PROFILES:
        raise NoRoute("Unknown mobility setting.")
    dest = buildings(graph).get(to_place_id) or _stop_destination(graph, to_place_id)
    if dest is None:
        raise NoRoute("We don't know that destination.")
    lon, lat, _ = resolve_start(graph, start)
    edge_id, t, snap_m = snap(graph, lon, lat)
    if snap_m > FAR_M:
        raise NoRoute("You're too far from campus for us to find a route. Pick a starting point on "
                      "campus or tap the map.")
    user_limit = PROFILES[profile].max_grade_pct if max_grade_pct is None else max_grade_pct
    user_limit = min(max(user_limit, MIN_LIMIT), MAX_LIMIT)
    here = [bt for bid, bt, _ in (snap(graph, *pt) for pt in blocked_points) if bid == edge_id]
    avoid = set(avoid_surfaces or [])
    mods = edge_modifiers or {}

    def attempt(limit):
        return _route(graph, _start_sources(graph, edge_id, t, "effort", profile, here), dest["entrances"],
                      "effort", profile, limit, avoid, mods)

    found, limit = attempt(user_limit), user_limit
    if found is None:
        # Nothing fits under the limit: binary-search the lowest limit that reaches the building,
        # so the result really is the least steep route that exists.
        cands = sorted({a.steep_pct for arcs in graph.adj.values() for a in arcs if a.steep_pct > user_limit})
        if not cands or attempt(cands[-1]) is None:
            if mods and any(math.isinf(m) for m in mods.values()):
                raise NoRoute("A confirmed barrier blocks every route without stairs to this building "
                              "right now.")
            raise NoRoute("We couldn't find a route without stairs to this building.")
        lo, hi = 0, len(cands) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if attempt(cands[mid]) is None:
                lo = mid + 1
            else:
                hi = mid
        limit = cands[lo]
        found = attempt(limit)
    least = _build(graph, "least_effort", found[1], found[0], profile, user_limit)
    if limit > user_limit:
        # The fallback message replaces the generic "has a steep hill" one.
        least["warnings"] = [f"There's no way there without a {steepness(limit)} hill. "
                             "This is the gentlest route."] + least["warnings"][1:]

    found = _route(graph, _start_sources(graph, edge_id, t, "time", profile, here), dest["entrances"],
                   "time", profile, math.inf, avoid, mods)
    fastest = _build(graph, "fastest", found[1], found[0], profile, user_limit)

    for r in (least, fastest):
        if snap_m > SNAP_WARN_M:
            r["warnings"].insert(0, f"You're about {round(snap_m)} m from the nearest accessible path, "
                                    "so the route starts there.")
    return {"routes": [least, fastest], "tradeoff_text": _tradeoff(least, fastest),
            "start_snap_m": round(snap_m, 1)}
