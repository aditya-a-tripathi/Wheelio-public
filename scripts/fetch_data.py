"""Download Dartmouth accessibility layers from the public ArcGIS service to data/raw/,
plus OpenStreetMap footpaths and streets around campus (osm_ways.json, via Overpass) that
fill gaps in Dartmouth's path layer (see scripts/build_graph.py).

Run once: uv run python scripts/fetch_data.py
Outputs are committed so nobody else needs network access to the source.
"""
import json
import ssl
import sys
import urllib.request
from pathlib import Path

import certifi

BASE = (
    "https://services1.arcgis.com/ajkPMiuiqxF9xEgP/arcgis/rest/services/"
    "Static_Path_of_Travel_Map_3_WFL1/FeatureServer"
)
RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
PAGE = 2000  # ArcGIS max records per request
# python.org builds on macOS ship without root certificates; use certifi's bundle.
SSL_CTX = ssl.create_default_context(cafile=certifi.where())

# layer id -> (output file, expected count)
LAYERS = {
    2: ("path_of_travel.geojson", 2669),
    0: ("ada_entrances.geojson", 217),
    4: ("bus_stops.geojson", 46),
    1: ("ada_spaces.geojson", 153),
    23: ("buildings.geojson", 622),
}


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60, context=SSL_CTX) as r:
        return json.load(r)


def fetch_layer(layer: int) -> dict:
    """Fetch every feature of a layer, paging past the 2,000-record limit."""
    features, offset = [], 0
    while True:
        url = (
            f"{BASE}/{layer}/query?where=1=1&outFields=*&outSR=4326&f=geojson"
            f"&resultOffset={offset}&resultRecordCount={PAGE}"
        )
        page = fetch_json(url)
        batch = page.get("features", [])
        features.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return {"type": "FeatureCollection", "features": features}


# Campus bounding box (south, west, north, east): the extent of Dartmouth's path layer.
OSM_BBOX = "43.6979,-72.3010,43.7127,-72.2769"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]


def fetch_osm() -> int:
    """Footpaths (for gaps) and streets (so connectors never cross one). Returns way count."""
    import time
    import urllib.parse

    q = (f'[out:json][timeout:60];('
         f'way["highway"~"^(footway|path|pedestrian|steps|living_street|cycleway)$"]({OSM_BBOX});'
         f'way["highway"~"^(residential|tertiary|secondary|primary|service|unclassified)$"]({OSM_BBOX}););'
         f'out tags geom;')
    body = urllib.parse.urlencode({"data": q}).encode()
    for attempt in range(6):  # public Overpass servers are often busy: back off and retry
        url = OVERPASS[attempt % len(OVERPASS)]
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": "Wheelio-hackathon/0.1"})
            with urllib.request.urlopen(req, timeout=90, context=SSL_CTX) as r:
                data = json.load(r)
            (RAW / "osm_ways.json").write_text(json.dumps(data))
            return len(data["elements"])
        except Exception as ex:  # noqa: BLE001
            print(f"overpass busy ({ex}); retrying")
            time.sleep(2 ** attempt)
    raise RuntimeError("Overpass unavailable; try again later")


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    ok = True
    for layer, (name, expected) in LAYERS.items():
        fc = fetch_layer(layer)
        n = len(fc["features"])
        (RAW / name).write_text(json.dumps(fc))
        flag = "ok" if n == expected else f"EXPECTED {expected}"
        ok &= n == expected
        print(f"layer {layer:>2} {name:<24} {n:>5} features  {flag}")
    print(f"OpenStreetMap osm_ways.json      {fetch_osm():>5} ways")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
