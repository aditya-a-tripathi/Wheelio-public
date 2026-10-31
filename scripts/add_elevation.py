"""Add elevation and directed grades to data/processed/graph.json.

Run after build_graph.py: uv run python scripts/add_elevation.py
Downloads the USGS 1 m lidar tile (67 MB, gitignored) on first run.

Per edge we sample the bare-earth elevation every ~5 m and store:
  elev            sampled profile [[dist_m, elev_m], ...] from u to v
  avg_grade_pct   signed overall grade u -> v (negative = downhill)
  max_up_fwd_pct  steepest uphill stretch going u -> v (best-fit line over 15 m)
  max_up_bwd_pct  steepest uphill stretch going v -> u
  max_abs_grade_pct  steepest stretch either way (steep downhill is also unsafe)
  climb_fwd_m / climb_bwd_m  total meters climbed in each direction
  grade_source    "lidar", or "label" when the lidar profile is an artifact
  grade_conflict  lidar says > 8.33% but Dartmouth labels the path "<5"

Combining the two sources:
- Bare-earth lidar removes bridges, decks and raised plazas, so a path over one appears to
  plunge. Any 5 m step steeper than ARTIFACT_PCT on a non-stairs path marks the edge as an
  artifact, and its grades come from Dartmouth's slope label instead.
- Otherwise grades come from lidar but never drop below Dartmouth's label (take the worse).
- Dartmouth's Slope classes ("<5", ">5", ">10") behave like percent grade: measured medians
  are 2.0%, 6.3% and 14.3%. Degrees would put ">10" near 18%.
"""
import json
import math
import urllib.request
from pathlib import Path

import certifi
import numpy as np
import rasterio
from rasterio.warp import transform

ROOT = Path(__file__).resolve().parents[1]
DEM = ROOT / "data" / "raw" / "usgs_1m_hanover.tif"
DEM_URL = (
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/"
    "NH_CT_RiverNorthL6_2015/TIFF/USGS_one_meter_x71y485_NH_CT_RiverNorthL6_2015.tif"
)
GRAPH = ROOT / "data" / "processed" / "graph.json"

STEP = 5.0  # meters between samples
WINDOW = 15.0  # meters over which "steepest stretch" is measured (best-fit line)
MIN_RUN = 10.0  # shortest run a grade is measured over; shorter is lidar noise
ARTIFACT_PCT = 25.0  # no mapped walkway climbs this steeply over 5 m; it's a removed structure
LABEL_FLOOR = {"<5": 0.0, ">5": 5.0, ">10": 10.0}  # lidar grade is never below the label
LABEL_GRADE = {"<5": 3.0, ">5": 7.5, ">10": 12.5}  # used when lidar is an artifact


def ensure_dem() -> None:
    if DEM.exists():
        return
    import ssl

    print(f"downloading {DEM.name} (67 MB)...")
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(DEM_URL, context=ctx) as r, open(DEM, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)


def densify(coords: list[list[float]]) -> tuple[np.ndarray, np.ndarray]:
    """Points every ~STEP m along a lon/lat polyline. Returns (lonlat array, distance array)."""
    lon0, lat0 = coords[0]
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 111_132.0
    xy = np.array([((lon - lon0) * kx, (lat - lat0) * ky) for lon, lat in coords])
    seg = np.hypot(*np.diff(xy, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    n = max(1, round(total / STEP))
    d = np.linspace(0.0, total, n + 1)
    x = np.interp(d, cum, xy[:, 0])
    y = np.interp(d, cum, xy[:, 1])
    return np.column_stack([lon0 + x / kx, lat0 + y / ky]), d


def fit_slope(d: np.ndarray, z: np.ndarray) -> float:
    """Least-squares slope (rise per meter) through the points."""
    dc = d - d.mean()
    var = float((dc * dc).sum())
    return float((dc * (z - z.mean())).sum() / var) if var > 0 else 0.0


def window_grades(d: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Signed grades (%) over sliding windows of ~WINDOW m, each a best-fit line.

    A best-fit line through every sample in the window ignores the +/- 1 m zigzag lidar shows
    near structures and trees; a two-point difference would report that zigzag as a 20% grade.
    Edges shorter than WINDOW: the rise is spread over at least MIN_RUN m, since lidar can't
    resolve shorter features and a 20 cm error over a 2 m edge would read as 10%.
    """
    if d[-1] <= WINDOW:
        rise = fit_slope(d, z) * d[-1]
        return np.array([100.0 * rise / max(d[-1], MIN_RUN)])
    k = max(1, round(WINDOW / (d[1] - d[0])))
    return np.array([100.0 * fit_slope(d[i : i + k + 1], z[i : i + k + 1]) for i in range(len(d) - k)])


def apply_labels(e: dict, d: np.ndarray, z: np.ndarray) -> None:
    """Fuse lidar grades with Dartmouth's slope label (see module docstring)."""
    cls = e["slope_class"]
    step = np.abs(np.diff(z)) / np.maximum(np.diff(d), 1e-6) * 100.0 if len(d) > 1 else np.array([0.0])
    if e["material"] != "Stairs" and step.max() > ARTIFACT_PCT:
        # No label (OSM or hand-added path): unknown grade, so assume it's steep to stay cautious.
        g = LABEL_GRADE[cls] if cls else LABEL_GRADE[">10"]
        e["grade_source"] = "label"
        e["max_up_fwd_pct"] = e["max_up_bwd_pct"] = e["max_abs_grade_pct"] = g
        e["avg_grade_pct"] = 0.0
        e["climb_fwd_m"] = e["climb_bwd_m"] = round(g * e["length_m"] / 200.0, 2)
        e["elev"] = [[p[0], round(float(np.median(z)), 2)] for p in e["elev"]]
    else:
        e["grade_source"] = "lidar"
        floor = LABEL_FLOOR.get(cls, 0.0)
        for k in ("max_up_fwd_pct", "max_up_bwd_pct", "max_abs_grade_pct"):
            e[k] = round(max(e[k], floor), 2)
    e["grade_conflict"] = cls == "<5" and e["max_abs_grade_pct"] > 8.33


def main() -> None:
    ensure_dem()
    g = json.loads(GRAPH.read_text())
    edges = g["edges"]

    # Densify every edge, then sample all points in one pass.
    dens = [densify(e["coordinates"]) for e in edges]
    all_ll = np.concatenate([ll for ll, _ in dens])
    with rasterio.open(DEM) as src:
        xs, ys = transform("EPSG:4326", src.crs, all_ll[:, 0].tolist(), all_ll[:, 1].tolist())
        z_all = np.array([v[0] for v in src.sample(zip(xs, ys))], dtype=float)
        z_all[z_all < -1000] = np.nan  # nodata

    missing, i = 0, 0
    for e, (ll, d) in zip(edges, dens):
        z = z_all[i : i + len(ll)]
        i += len(ll)
        if np.isnan(z).any():
            missing += 1
            z = np.where(np.isnan(z), np.nanmean(z) if not np.isnan(z).all() else 0.0, z)
        diffs = np.diff(z)
        w = window_grades(d, z)
        e["elev"] = [[round(float(a), 1), round(float(b), 2)] for a, b in zip(d, z)]
        e["avg_grade_pct"] = round(100.0 * (z[-1] - z[0]) / d[-1], 2) if d[-1] > 0 else 0.0
        e["max_up_fwd_pct"] = round(max(0.0, float(w.max())), 2)
        e["max_up_bwd_pct"] = round(max(0.0, float(-w.min())), 2)
        e["max_abs_grade_pct"] = round(float(np.abs(w).max()), 2)
        e["climb_fwd_m"] = round(float(diffs[diffs > 0].sum()), 2)
        e["climb_bwd_m"] = round(float(-diffs[diffs < 0].sum()), 2)
        apply_labels(e, d, z)

    # Node elevations from the DEM at each node.
    node_ids = list(g["nodes"])
    ll = np.array([g["nodes"][n] for n in node_ids])
    with rasterio.open(DEM) as src:
        xs, ys = transform("EPSG:4326", src.crs, ll[:, 0].tolist(), ll[:, 1].tolist())
        zn = [round(float(v[0]), 2) for v in src.sample(zip(xs, ys))]
    g["node_elev"] = dict(zip(node_ids, zn))

    # Sanity checks.
    with rasterio.open(DEM) as src:
        (gx,), (gy,) = transform("EPSG:4326", src.crs, [-72.2887], [43.7033])
        green = float(next(src.sample([(gx, gy)]))[0])
    by_class: dict[str, list[float]] = {}
    for e in edges:
        if e["material"] != "Stairs":
            by_class.setdefault(e["slope_class"] or "unlabeled", []).append(e["max_abs_grade_pct"])
    acc = [e for e in edges if e["material"] != "Stairs"]
    g["stats"]["elevation"] = {
        "green_elev_m": round(green, 1),
        "edges_with_nodata": missing,
        "artifact_edges_using_label": sum(e["grade_source"] == "label" for e in edges),
        "grade_conflicts": sum(e["grade_conflict"] for e in edges),
        "accessible_edges_over_8_33_pct": sum(e["max_abs_grade_pct"] > 8.33 for e in acc),
        "accessible_edges_over_5_pct": sum(e["max_abs_grade_pct"] > 5 for e in acc),
        "median_max_grade_by_dartmouth_class": {
            k: round(float(np.median(v)), 1) for k, v in sorted(by_class.items())
        },
    }
    GRAPH.write_text(json.dumps(g))
    for k, v in g["stats"]["elevation"].items():
        print(f"{k:40} {v}")
    print(f"elevation range on paths: {np.nanmin(z_all):.1f} to {np.nanmax(z_all):.1f} m")
    print(f"wrote {GRAPH.relative_to(ROOT)} ({GRAPH.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
