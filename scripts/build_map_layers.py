"""Build the static map layers the frontend draws (hazards, path network, labels, stops).

Run: uv run python scripts/build_map_layers.py
Writes frontend/data/*.geojson (committed; small, and works in mock mode and offline):
  network.geojson    every path segment: cls (gentle|moderate|steep|very_steep|stairs), material,
                     source. Steep segments run UPHILL so the map can draw chevrons up the hill.
  hazards.geojson    one labeled point per connected stretch of: very steep hill, steep hill,
                     gravel, brick, stairs (with the nearest building and length in feet).
  buildings.geojson  Dartmouth building outlines with names (for labels and the destination).
  labels.geojson     one point per building name (label placement).
  stops.geojson      bus and shuttle stops.
  entrances.geojson  ADA entrances.  parking.geojson  accessible parking spaces.
"""
import json
import math
import sys
from pathlib import Path

from shapely.geometry import LineString, shape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RAW = ROOT / "data" / "raw"
OUT = ROOT / "frontend" / "data"
GRAPH = ROOT / "data" / "processed" / "graph.json"


def cls_of(e: dict) -> str:
    if e["material"] == "Stairs":
        return "stairs"
    g = e["max_abs_grade_pct"]
    return "gentle" if g < 5 else "moderate" if g < 8.33 else "steep" if g <= 12 else "very_steep"


def rnd(coords):
    return [[round(x, 6), round(y, 6)] for x, y in coords]


def fc(features):
    return {"type": "FeatureCollection", "features": features}


def meters(a, b):
    dy = (a[1] - b[1]) * 111_132.0
    dx = (a[0] - b[0]) * 111_320.0 * math.cos(math.radians(a[1]))
    return math.hypot(dx, dy)


def main() -> None:
    from backend.app.ai.explain import _near  # nearest building name (ADA entrance names)

    g = json.loads(GRAPH.read_text())
    edges = g["edges"]
    OUT.mkdir(parents=True, exist_ok=True)

    # ---- network
    net = []
    for e in edges:
        c = e["coordinates"]
        cls = cls_of(e)
        if cls in ("steep", "very_steep", "moderate") and e["max_up_bwd_pct"] > e["max_up_fwd_pct"]:
            c = c[::-1]  # draw uphill
        net.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": rnd(c)},
                    "properties": {"cls": cls, "material": e["material"].lower(), "source": e["source"],
                                   "main": bool(e["main_component"])}})

    # ---- hazard stretches: connected edges of the same kind, one labeled point each
    def kind(e):
        c = cls_of(e)
        if c in ("stairs", "steep", "very_steep"):
            return c
        if e["material"] in ("Gravel", "Brick"):
            return e["material"].lower()
        return None

    by_kind: dict[str, list[dict]] = {}
    for e in edges:
        k = kind(e)
        if k:
            by_kind.setdefault(k, []).append(e)
    names = {"very_steep": "Very steep hill", "steep": "Steep hill", "gravel": "Gravel path",
             "brick": "Brick path", "stairs": "Stairs"}
    hazards = []
    for k, es in by_kind.items():
        parent = list(range(len(es)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        at_node: dict[str, list[int]] = {}
        for i, e in enumerate(es):
            for n in (e["u"], e["v"]):
                at_node.setdefault(n, []).append(i)
        for idx in at_node.values():
            for j in idx[1:]:
                parent[find(j)] = find(idx[0])
        groups: dict[int, list[dict]] = {}
        for i, e in enumerate(es):
            groups.setdefault(find(i), []).append(e)
        for grp in groups.values():
            length = sum(e["length_m"] for e in grp)
            if k in ("gravel", "brick") and length < 15:
                continue
            longest = max(grp, key=lambda e: e["length_m"])
            mid = LineString(longest["coordinates"]).interpolate(0.5, normalized=True)
            near = _near(mid.x, mid.y)
            label = names[k] + (", steeper than a ramp" if k == "very_steep" else "")
            hazards.append({"type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [round(mid.x, 6), round(mid.y, 6)]},
                            "properties": {"kind": k, "label": label, "near": near,
                                           "length_ft": int(round(length * 3.28084 / 10) * 10)}})

    # ---- buildings, labels
    bl, labels = [], []
    for f in json.loads((RAW / "buildings.geojson").read_text())["features"]:
        if not f["geometry"]:
            continue
        name = (f["properties"].get("Name") or "").strip()
        geom = shape(f["geometry"]).buffer(0)
        if geom.is_empty:
            continue
        bl.append({"type": "Feature", "geometry": json.loads(json.dumps(geom.simplify(0.000005).__geo_interface__)),
                   "properties": {"name": name, "area": round(geom.area * 1e10)}})
        if name:
            p = geom.representative_point()
            labels.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(p.x, 6), round(p.y, 6)]},
                           "properties": {"name": name, "area": round(geom.area * 1e10)}})
    for f in bl:
        f["geometry"]["coordinates"] = json.loads(json.dumps(f["geometry"]["coordinates"]),
                                                  parse_float=lambda s: round(float(s), 6))

    # ---- stops, entrances, parking
    stops = [{"type": "Feature", "geometry": f["geometry"],
              "properties": {"name": (f["properties"]["STOP_NAME"] or "Bus stop").strip(),
                             "service": "Juniper Shuttle" if "Juniper" in (f["properties"].get("Service_Name") or "")
                             else "Advance Transit"}}
             for f in json.loads((RAW / "bus_stops.geojson").read_text())["features"] if f["geometry"]]
    ents = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(e["lon"], 6), round(e["lat"], 6)]},
             "properties": {"name": e["name"]}} for e in g["entrances"]]
    parking = [{"type": "Feature", "geometry": f["geometry"], "properties": {}}
               for f in json.loads((RAW / "ada_spaces.geojson").read_text())["features"] if f["geometry"]]

    files = {"network": net, "hazards": hazards, "buildings": bl, "labels": labels, "stops": stops,
             "entrances": ents, "parking": parking}
    for name, feats in files.items():
        p = OUT / f"{name}.geojson"
        p.write_text(json.dumps(fc(feats), separators=(",", ":")))
        print(f"{name:10} {len(feats):5} features  {p.stat().st_size / 1000:7.0f} KB")
    from collections import Counter
    print("hazards by kind:", dict(Counter(h["properties"]["kind"] for h in hazards)))


if __name__ == "__main__":
    main()
