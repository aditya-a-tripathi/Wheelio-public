"""Load data/processed/graph.json once and index it for routing and snapping."""
import json
import math
from dataclasses import dataclass, field
from functools import lru_cache

import shapely
from shapely.geometry import LineString, Point

from ..config import DATA_PROCESSED, DATA_RAW
from .effort import PROFILES, cost_and_time

# Same local projection as scripts/build_graph.py (meters around the Green).
LAT0, LON0 = 43.7033, -72.2887
M_PER_DEG_LAT = 111_132.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))


def to_m(lon: float, lat: float) -> tuple[float, float]:
    return ((lon - LON0) * M_PER_DEG_LON, (lat - LAT0) * M_PER_DEG_LAT)


def to_lonlat(x: float, y: float) -> list[float]:
    return [round(LON0 + x / M_PER_DEG_LON, 7), round(LAT0 + y / M_PER_DEG_LAT, 7)]


@dataclass
class Arc:
    """One direction of travel along an edge."""

    to: str
    edge_id: str
    forward: bool  # u -> v
    steep_pct: float  # steepest stretch either way (for limits)
    effort: dict[str, float] = field(default_factory=dict)  # per profile
    time_s: dict[str, float] = field(default_factory=dict)


@dataclass
class Graph:
    nodes: dict[str, list[float]]
    node_elev: dict[str, float]
    edges: dict[str, dict]
    adj: dict[str, list[Arc]]
    entrances: list[dict]
    lines_m: list[LineString]  # snappable edges (no stairs, main network), in meters
    line_ids: list[str]
    tree: shapely.STRtree
    bus_stops: list[dict]
    buildings_m: list = field(default_factory=list)  # Dartmouth building outlines, in meters
    building_tree: shapely.STRtree | None = None


@lru_cache(maxsize=1)
def load() -> Graph:
    g = json.loads((DATA_PROCESSED / "graph.json").read_text())
    edges = {e["id"]: e for e in g["edges"]}
    adj: dict[str, list[Arc]] = {n: [] for n in g["nodes"]}
    for e in g["edges"]:
        for fwd, a, b in ((True, e["u"], e["v"]), (False, e["v"], e["u"])):
            arc = Arc(to=b, edge_id=e["id"], forward=fwd, steep_pct=e["max_abs_grade_pct"])
            for name, p in PROFILES.items():
                arc.effort[name], arc.time_s[name] = cost_and_time(e, 0.0, e["length_m"], fwd, p)
            adj[a].append(arc)

    snappable = [e for e in g["edges"] if e["material"] != "Stairs" and e["main_component"]]
    lines_m = [LineString([to_m(*c) for c in e["coordinates"]]) for e in snappable]

    stops = json.loads((DATA_RAW / "bus_stops.geojson").read_text())["features"]
    bus_stops = [
        {"name": s["properties"]["STOP_NAME"].strip(), "lon": s["geometry"]["coordinates"][0],
         "lat": s["geometry"]["coordinates"][1]}
        for s in stops
        if (s["properties"]["STOP_NAME"] or "").strip()
    ]
    from shapely.geometry import shape

    buildings_m = []
    for f in json.loads((DATA_RAW / "buildings.geojson").read_text())["features"]:
        if f["geometry"]:
            poly = shapely.transform(shape(f["geometry"]).buffer(0),
                                     lambda xy: (xy - [LON0, LAT0]) * [M_PER_DEG_LON, M_PER_DEG_LAT])
            buildings_m.append(poly.buffer(-0.5))  # ignore paths that just touch a wall
    return Graph(
        nodes=g["nodes"], node_elev=g["node_elev"], edges=edges, adj=adj, entrances=g["entrances"],
        lines_m=lines_m, line_ids=[e["id"] for e in snappable], tree=shapely.STRtree(lines_m),
        bus_stops=bus_stops, buildings_m=buildings_m, building_tree=shapely.STRtree(buildings_m),
    )


SNAP_SLACK_M = 25.0  # accept a path up to this much farther away to avoid cutting through a building


def snap(graph: Graph, lon: float, lat: float) -> tuple[str, float, float]:
    """Nearest point on a stairs-free main-network edge: (edge id, distance along edge, snap m).

    Prefers a path the user can walk or wheel to in a straight line without crossing a building
    (the dotted link on the map), up to SNAP_SLACK_M farther than the nearest path.
    """
    pt = Point(to_m(lon, lat))
    i = int(graph.tree.nearest(pt))
    best = (i, graph.lines_m[i].distance(pt))
    if graph.building_tree is not None and best[1] > 1.0:
        inside = {int(b) for b in graph.building_tree.query(pt, predicate="intersects")}
        cands = graph.tree.query(pt.buffer(best[1] + SNAP_SLACK_M))
        for j in sorted((int(c) for c in cands), key=lambda c: graph.lines_m[c].distance(pt)):
            line = graph.lines_m[j]
            link = LineString([pt, line.interpolate(line.project(pt))])
            hits = {int(b) for b in graph.building_tree.query(link, predicate="intersects")} - inside
            if not hits:
                best = (j, line.distance(pt))
                break
    j = best[0]
    line = graph.lines_m[j]
    return graph.line_ids[j], float(line.project(pt)), float(line.distance(pt))
