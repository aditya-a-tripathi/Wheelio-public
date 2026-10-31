"""Build the routable path network from data/raw/path_of_travel.geojson.

Run: uv run python scripts/build_graph.py
Output: data/processed/graph.json (nodes, edges, entrance snaps, stats). Elevation is
added afterwards by scripts/add_elevation.py.

Steps:
1. Project lon/lat to local meters (equirectangular around campus; error < 0.1% at this scale).
2. Split lines at T-junctions: where one line ends within TOL of the middle of another,
   cut the other line there so the two actually share a node.
3. Cluster line endpoints within TOL into shared nodes (union-find).
4. Report connected components and how far each ADA entrance is from the network.

Extra sources (each edge has "source"):
- "dartmouth": Dartmouth's Path of Travel layer (the base; it knows stairs and curb ramps).
- "osm": paved OpenStreetMap sidewalks and footpaths, only the stretches Dartmouth has not
  mapped (more than COVERED_M from any Dartmouth path). Dirt trails, private and indoor paths
  and steps are left out. Joined to the network by short connectors that never cross a street.
  Routing gives these a small penalty: we don't know about their curb ramps.
- "added": data/raw/path_additions.geojson, gaps in both sources checked by hand on imagery.
"""
import json
import math
from collections import Counter
from pathlib import Path

import networkx as nx
import numpy as np
import shapely
from shapely.geometry import LineString, Point
from shapely.ops import substring

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed" / "graph.json"

TOL = 1.5  # meters: endpoints closer than this are the same junction
LAT0, LON0 = 43.7033, -72.2887  # the Green; projection origin
M_PER_DEG_LAT = 111_132.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))


def to_m(lon: float, lat: float) -> tuple[float, float]:
    return ((lon - LON0) * M_PER_DEG_LON, (lat - LAT0) * M_PER_DEG_LAT)


def to_lonlat(x: float, y: float) -> list[float]:
    return [round(LON0 + x / M_PER_DEG_LON, 7), round(LAT0 + y / M_PER_DEG_LAT, 7)]


def clean(value):
    return value.strip() if isinstance(value, str) else value


COVERED_M = 8.0  # an OSM stretch this close to a Dartmouth path is the same path
CONNECT_M = 10.0  # longest connector from a new path end to the network
MIN_PIECE_M = 10.0  # shorter uncovered OSM stretches are just drawing differences
OSM_FOOT = {"footway", "path", "pedestrian"}
OSM_ROADS = {"primary", "secondary", "tertiary", "residential", "unclassified", "living_street"}
PAVED = {"asphalt": "Pavement", "paved": "Pavement", "concrete": "Concrete", "concrete:plates": "Concrete",
         "paving_stones": "Brick", "brick": "Brick", "sett": "Brick", "bricks": "Brick"}


def blank_props(source: str, material: str, **extra) -> dict:
    return {"object_id": None, "material": material, "slope_class": None, "label": None,
            "new": False, "source": source, **extra}


def osm_material(t: dict) -> str | None:
    """Our material for an OSM way, or None if a wheelchair shouldn't be routed on it."""
    if t.get("access") in ("private", "no") or t.get("indoor") == "yes" or t.get("level", "0") != "0":
        return None
    if t.get("area") == "yes":  # a plaza's outline is not a path along its edge
        return None
    if t.get("wheelchair") == "no" or t.get("tunnel") in ("yes", "building_passage"):
        return None
    surface = t.get("surface")
    if surface is None:  # untagged: trust it only for mapped sidewalks, crossings and plazas
        ok = t.get("footway") in ("sidewalk", "crossing") or t["highway"] == "pedestrian"
        return ("Crosswalk" if t.get("footway") == "crossing" else "Pavement") if ok else None
    if surface not in PAVED:
        return None
    return "Crosswalk" if t.get("footway") == "crossing" else PAVED[surface]


def load_osm(dart: list[LineString]) -> tuple[list[LineString], list[dict]]:
    """Paved OSM footpath stretches that Dartmouth's layer is missing, plus their connectors."""
    path = RAW / "osm_ways.json"
    if not path.exists():
        print("no data/raw/osm_ways.json (run scripts/fetch_data.py); skipping OSM paths")
        return [], []
    ways = json.loads(path.read_text())["elements"]
    dart_tree = shapely.STRtree(dart)

    def near_dart(pt: Point) -> float:
        return float(pt.distance(dart[int(dart_tree.nearest(pt))]))

    lines, props = [], []
    for w in ways:
        t = w["tags"]
        if t.get("highway") not in OSM_FOOT:
            continue
        material = osm_material(t)
        if material is None:
            continue
        line = LineString([to_m(n["lon"], n["lat"]) for n in w["geometry"]])
        if line.length < 1:
            continue
        n = max(2, int(line.length // 4) + 1)
        ds = np.linspace(0, line.length, n)
        covered = [near_dart(line.interpolate(d)) <= COVERED_M for d in ds]
        i = 0
        while i < n:
            if covered[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and not covered[j + 1]:
                j += 1
            a, b = ds[max(i - 1, 0)], ds[min(j + 1, n - 1)]  # reach back onto the covered path
            if b - a >= MIN_PIECE_M:
                lines.append(substring(line, a, b))
                props.append(blank_props("osm", material, osm_id=w["id"]))
            i = j + 1
    return lines, props


def load_additions() -> tuple[list[LineString], list[dict]]:
    path = RAW / "path_additions.geojson"
    if not path.exists():
        return [], []
    lines, props = [], []
    for f in json.loads(path.read_text())["features"]:
        lines.append(LineString([to_m(lon, lat) for lon, lat in f["geometry"]["coordinates"]]))
        props.append(blank_props("added", f["properties"]["material"], note=f["properties"]["note"]))
    return lines, props


def connectors(new: list[LineString], new_props: list[dict], network: list[LineString],
               roads: list[LineString]) -> tuple[list[LineString], list[dict]]:
    """Short links from each loose end of a new path to the nearest other path, if no street is crossed."""
    tree = shapely.STRtree(network)
    road_tree = shapely.STRtree(roads) if roads else None
    out, out_props = [], []
    for k, line in enumerate(new):
        if new_props[k]["material"] == "Crosswalk":
            continue  # crossings end at curbs on purpose; T-junction splitting joins them
        for end in (Point(line.coords[0]), Point(line.coords[-1])):
            best, best_d = None, CONNECT_M
            for j in tree.query(end, predicate="dwithin", distance=CONNECT_M):
                other = network[int(j)]
                if other.equals(line):
                    continue
                d = end.distance(other)
                if d < best_d:
                    best, best_d = other, d
            if best is None or best_d <= TOL:
                continue
            link = LineString([end, best.interpolate(best.project(end))])
            if road_tree is not None and len(road_tree.query(link, predicate="intersects")):
                continue
            out.append(link)
            out_props.append(blank_props(new_props[k]["source"], new_props[k]["material"], connector=True))
    return out, out_props


def load_roads() -> list[LineString]:
    path = RAW / "osm_ways.json"
    if not path.exists():
        return []
    return [LineString([to_m(n["lon"], n["lat"]) for n in w["geometry"]])
            for w in json.loads(path.read_text())["elements"] if w["tags"].get("highway") in OSM_ROADS]


def load_lines() -> tuple[list[LineString], list[dict]]:
    feats = json.loads((RAW / "path_of_travel.geojson").read_text())["features"]
    lines, props = [], []
    for f in feats:
        line = LineString([to_m(lon, lat) for lon, lat in f["geometry"]["coordinates"]])
        if line.length == 0:
            continue
        p = f["properties"]
        lines.append(line)
        props.append(
            {
                "object_id": p["OBJECTID"],
                "material": clean(p["Material"]),
                "slope_class": clean(p["Slope"]),
                "label": clean(p["Static"]),
                "new": p["New"] == "Yes",
                "source": "dartmouth",
            }
        )
    osm, osm_props = load_osm(lines)
    add, add_props = load_additions()
    new, new_props = osm + add, osm_props + add_props
    links, link_props = connectors(new, new_props, lines + new, load_roads())
    return lines + new + links, props + new_props + link_props


def split_at_t_junctions(lines: list[LineString]) -> tuple[list[tuple[int, LineString]], int]:
    """Return (source line index, piece) for every piece after T-junction splits."""
    tree = shapely.STRtree(lines)
    cuts: dict[int, list[float]] = {}
    for i, line in enumerate(lines):
        for end in (Point(line.coords[0]), Point(line.coords[-1])):
            for j in tree.query(end, predicate="dwithin", distance=TOL):
                if j == i:
                    continue
                other = lines[j]
                d = other.project(end)
                # Only a T-junction if the endpoint touches the other line's interior.
                if TOL < d < other.length - TOL:
                    cuts.setdefault(int(j), []).append(d)

    pieces, n_splits = [], 0
    for i, line in enumerate(lines):
        stops = [0.0]
        for d in sorted(cuts.get(i, [])):
            if d - stops[-1] > TOL:
                stops.append(d)
        if line.length - stops[-1] <= TOL and len(stops) > 1:
            stops.pop()
        stops.append(line.length)
        n_splits += len(stops) - 2
        for a, b in zip(stops, stops[1:]):
            pieces.append((i, substring(line, a, b)))
    return pieces, n_splits


def cluster_endpoints(
    pieces: list[tuple[int, LineString]], stairs: set[int]
) -> tuple[np.ndarray, np.ndarray]:
    """Union-find endpoints within TOL. Returns (node xy array, endpoint -> node id).

    Endpoint 2k and 2k+1 are the two ends of piece k. A merge is refused if it would put
    both ends of a stairs piece in one node: a 1 m step collapsed to a point would let a
    wheelchair route straight over it.
    """
    pts = np.array([c for _, ln in pieces for c in (ln.coords[0], ln.coords[-1])])
    parent = list(range(len(pts)))
    # root -> stairs pieces with an end in this cluster
    stair_ends = {2 * k + e: {k} for k in stairs for e in (0, 1)}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    geoms = shapely.points(pts)
    tree = shapely.STRtree(geoms)
    left, right = tree.query(geoms, predicate="dwithin", distance=TOL)
    dist = np.hypot(*(pts[left] - pts[right]).T)
    for idx in np.argsort(dist, kind="stable"):  # closest pairs merge first
        ra, rb = find(int(left[idx])), find(int(right[idx]))
        if ra == rb:
            continue
        sa, sb = stair_ends.get(ra, set()), stair_ends.get(rb, set())
        if sa & sb:
            continue
        parent[ra] = rb
        if sa or sb:
            stair_ends[rb] = sa | sb

    roots = [find(k) for k in range(len(pts))]
    ids = {r: n for n, r in enumerate(dict.fromkeys(roots))}
    endpoint_node = np.array([ids[r] for r in roots])
    nodes = np.zeros((len(ids), 2))
    counts = np.zeros(len(ids))
    for k, n in enumerate(endpoint_node):
        nodes[n] += pts[k]
        counts[n] += 1
    return nodes / counts[:, None], endpoint_node


def main() -> None:
    lines, props = load_lines()
    pieces, n_splits = split_at_t_junctions(lines)
    stairs = {k for k, (src, _) in enumerate(pieces) if props[src]["material"] == "Stairs"}
    nodes, endpoint_node = cluster_endpoints(pieces, stairs)

    edges = []
    for k, (src, ln) in enumerate(pieces):
        u, v = int(endpoint_node[2 * k]), int(endpoint_node[2 * k + 1])
        if u == v:  # piece collapsed into a single junction
            continue
        coords = list(ln.coords)
        coords[0], coords[-1] = tuple(nodes[u]), tuple(nodes[v])  # glue ends to the node
        edges.append(
            {
                "id": f"e{len(edges)}",
                "u": f"n{u}",
                "v": f"n{v}",
                "length_m": round(LineString(coords).length, 2),
                "coordinates": [to_lonlat(x, y) for x, y in coords],
                **props[src],
            }
        )

    g = nx.MultiGraph()
    for e in edges:
        g.add_edge(e["u"], e["v"], key=e["id"])
    comps = sorted(nx.connected_components(g), key=len, reverse=True)
    main_comp = comps[0]
    for e in edges:
        e["main_component"] = e["u"] in main_comp

    # Accessible-only network: what a manual chair can actually use.
    g_acc = nx.MultiGraph()
    for e in edges:
        if e["material"] != "Stairs":
            g_acc.add_edge(e["u"], e["v"])
    acc_comps = sorted(nx.connected_components(g_acc), key=len, reverse=True)
    acc_main = acc_comps[0]

    # How far is each ADA entrance from the (accessible, main) network?
    acc_nodes = sorted(acc_main, key=lambda n: int(n[1:]))
    acc_xy = nodes[[int(n[1:]) for n in acc_nodes]]
    node_tree = shapely.STRtree(shapely.points(acc_xy))
    entrances = []
    ents = json.loads((RAW / "ada_entrances.geojson").read_text())["features"]
    for f in ents:
        p = f["properties"]
        x, y = to_m(*f["geometry"]["coordinates"])
        idx = int(node_tree.nearest(Point(x, y)))
        dist = float(np.hypot(*(acc_xy[idx] - (x, y))))
        entrances.append(
            {
                "id": f"ent{p['OBJECTID']}",
                "name": clean(p["Name"]),
                "address": clean(p["Address"]),
                "search": clean(p["Search_Field"]),
                "fac_code": clean(p["Fac_Code"]),
                "lon": f["geometry"]["coordinates"][0],
                "lat": f["geometry"]["coordinates"][1],
                "node": acc_nodes[idx],
                "snap_m": round(dist, 1),
            }
        )

    snaps = [e["snap_m"] for e in entrances]
    stats = {
        "source_lines": len(lines),
        "t_junction_splits": n_splits,
        "edges": len(edges),
        "nodes": len(nodes),
        "components": len(comps),
        "largest_component_node_share": round(len(main_comp) / g.number_of_nodes(), 4),
        "accessible_components": len(acc_comps),
        "accessible_largest_node_share": round(len(acc_main) / g_acc.number_of_nodes(), 4),
        "entrance_snap_m_median": round(float(np.median(snaps)), 1),
        "entrances_over_30m": sum(s > 30 for s in snaps),
        "materials": dict(Counter(e["material"] for e in edges)),
        "sources": dict(Counter(e["source"] for e in edges)),
        "source_length_m": {s: round(sum(e["length_m"] for e in edges if e["source"] == s))
                            for s in ("dartmouth", "osm", "added")},
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "nodes": {f"n{i}": to_lonlat(x, y) for i, (x, y) in enumerate(nodes)},
                "edges": edges,
                "entrances": entrances,
                "stats": stats,
            }
        )
    )
    for k, v in stats.items():
        print(f"{k:32} {v}")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
