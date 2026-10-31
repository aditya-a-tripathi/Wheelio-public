"""Write a standalone HTML map of the graph for visual checks (Backend only, gitignored).

Run: uv run python scripts/preview.py   then open data/processed/preview_grades.html
     uv run python scripts/preview.py --from bldg:hanover-inn-dartmouth --to bldg:west-house-judge-hall
       draws both routes on top -> data/processed/preview_route.html
Edges are colored by their steepest stretch; click one to see its numbers.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "data" / "processed" / "graph.json"
OUT = ROOT / "data" / "processed" / "preview_grades.html"

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Wheelio grade preview</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body,#map{height:100%;margin:0;font-family:system-ui,sans-serif}
#legend{position:absolute;z-index:1000;bottom:24px;right:10px;background:#fff;padding:8px 12px;border-radius:6px;box-shadow:0 1px 4px #0004;font-size:13px;line-height:1.6}
#legend i{display:inline-block;width:18px;height:4px;margin-right:6px;vertical-align:middle}</style></head>
<body><div id="map"></div><div id="legend"><b>Steepest stretch</b><br>
<i style="background:#1a9850"></i>under 5%<br><i style="background:#fdae61"></i>5 to 8.33% (ADA max)<br>
<i style="background:#d73027"></i>over 8.33%<br><i style="background:#555"></i>stairs<br>
<i style="background:#7b3294"></i>lidar artifact, label used<br>dashed = sources disagree<br>
dotted = OpenStreetMap sidewalk<br>long dashes = added by hand<br>
<span style="color:#666">__STATS__</span></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const data = __DATA__;
const map = L.map("map").setView([43.7040, -72.2887], 16);
// Esri light gray basemap: needs no key and works from file:// (OSM's servers block requests
// without a Referer; CARTO now wants a key). Muted colors keep the grade colors readable.
const esri = "https://server.arcgisonline.com/ArcGIS/rest/services/";
const gray = L.tileLayer(esri + "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
  maxZoom: 20, maxNativeZoom: 16, attribution: "Tiles &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors"});
// Satellite shows the actual sidewalks and buildings, so path alignment can be checked by eye.
const sat = L.tileLayer(esri + "World_Imagery/MapServer/tile/{z}/{y}/{x}", {
  maxZoom: 20, maxNativeZoom: 19, attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics"}).addTo(map);
L.control.layers({"Satellite": sat, "Gray streets": gray}, null, {position: "topleft"}).addTo(map);
function color(p){
  if (p.material === "Stairs") return "#555";
  if (p.grade_source === "label") return "#7b3294";
  const g = p.max_abs_grade_pct;
  return g > 8.33 ? "#d73027" : g > 5 ? "#fdae61" : "#1a9850";
}
L.geoJSON(data, {
  style: f => ({color: color(f.properties), weight: f.properties.source === "dartmouth" ? 4 : 6, opacity: __NETOP__,
    dashArray: f.properties.source === "osm" ? "2 6" : f.properties.source === "added" ? "10 4" : f.properties.grade_conflict ? "6 6" : null}),
  onEachFeature: (f, l) => { const p = f.properties; l.bindPopup(
    `<b>${p.id}</b> ${p.material}, from ${p.source}, Dartmouth: ${p.label} (${p.slope_class})<br>` +
    `length ${p.length_m} m, avg ${p.avg_grade_pct}%<br>` +
    `steepest up u&rarr;v ${p.max_up_fwd_pct}%, v&rarr;u ${p.max_up_bwd_pct}%<br>` +
    `climb ${p.climb_fwd_m} / ${p.climb_bwd_m} m, source ${p.grade_source}`); }
}).addTo(map);
const routes = __ROUTES__;
const RC = {least_effort: "#2563eb", fastest: "#f97316"};
for (const r of routes) {
  L.geoJSON(r.geojson, {style: {color: "#fff", weight: 11, opacity: 0.95}}).addTo(map);
  L.geoJSON(r.geojson, {style: {color: RC[r.id], weight: 7, opacity: 1}})
    .bindTooltip(`${r.id}: ${Math.round(r.distance_m)} m, ${r.time_min} min, max ${r.max_grade_pct}%`, {sticky: true}).addTo(map);
}
if (routes.length) {
  map.fitBounds(L.geoJSON(routes[0].geojson).getBounds().extend(L.geoJSON(routes[1].geojson).getBounds()), {paddingTopLeft: [60, 40], paddingBottomRight: [420, 40]});
  const c = routes[0].geojson.coordinates;
  L.circleMarker([c[0][1], c[0][0]], {radius: 8, color: "#111", fillColor: "#fff", fillOpacity: 1}).addTo(map).bindTooltip("start");
  L.circleMarker([c[c.length-1][1], c[c.length-1][0]], {radius: 8, color: "#111", fillColor: "#111", fillOpacity: 1}).addTo(map).bindTooltip("ADA entrance");
  document.getElementById("legend").insertAdjacentHTML("afterbegin",
    `<b>Route</b><br><i style="background:#2563eb;height:6px"></i>least effort<br><i style="background:#f97316;height:6px"></i>fastest<br>__TRADEOFF__<br><br>`);
}
</script></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", help="start as LAT,LON, or a place id like bldg:hanover-inn-dartmouth")
    ap.add_argument("--to", help="destination building id, e.g. bldg:west-house-judge-hall")
    args = ap.parse_args()
    g = json.loads(GRAPH.read_text())
    keep = ("id", "material", "label", "slope_class", "length_m", "avg_grade_pct", "max_up_fwd_pct",
            "max_up_bwd_pct", "max_abs_grade_pct", "climb_fwd_m", "climb_bwd_m", "grade_source",
            "grade_conflict", "source")
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": e["coordinates"]},
             "properties": {k: e.get(k) for k in keep}}
            for e in g["edges"]
        ],
    }
    s = g["stats"]
    stats = f'{s["edges"]} edges, {s["elevation"]["accessible_edges_over_8_33_pct"]} accessible over 8.33%'
    routes, tradeoff, out = [], "", OUT
    if args.start and args.to:
        sys.path.insert(0, str(ROOT))
        from backend.app.routing import route

        if "," in args.start and not args.start.startswith(("bldg:", "stop:")):
            lat, lon = (float(x) for x in args.start.split(","))
            start = {"lat": lat, "lon": lon}
        else:
            start = {"place_id": args.start}
        r = route(start, args.to)
        routes, tradeoff = r["routes"], r["tradeoff_text"]
        out = OUT.with_name("preview_route.html")
        for x in routes:
            print(f"{x['id']:12} {x['distance_m']:.0f} m, {x['time_min']} min, effort {x['effort']:.0f}, "
                  f"max {x['max_grade_pct']}%, climb {x['climb_m']} m, warnings {x['warnings']}")
        print(tradeoff)
    page = (PAGE.replace("__DATA__", json.dumps(fc)).replace("__STATS__", stats)
            .replace("__ROUTES__", json.dumps(routes)).replace("__NETOP__", "0.45" if routes else "0.9").replace("__TRADEOFF__", tradeoff))
    out.write_text(page)
    print(f"wrote {out.relative_to(ROOT)}  (open it in a browser)")


if __name__ == "__main__":
    main()
