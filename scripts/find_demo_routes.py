"""Find building pairs that make a good demo: Fastest climbs a steep hill, Least effort
stays under the manual-chair limit for only a little extra time.

Run: uv run python scripts/find_demo_routes.py [--top 15]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.routing import NoRoute, buildings, route  # noqa: E402
from backend.app.routing.graph import load  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    g = load()
    B = buildings(g)
    rows = []
    for a, A in B.items():
        start = {"lat": A["entrances"][0]["lat"], "lon": A["entrances"][0]["lon"]}
        for b in B:
            if a == b:
                continue
            try:
                r = route(start, b)
            except NoRoute:
                continue
            least, fast = r["routes"]
            if not (300 <= fast["distance_m"] <= 1200):
                continue
            if least["max_grade_pct"] > 8.33 or fast["max_grade_pct"] <= 9.0:
                continue  # want: least effort is ADA-ok, fastest is clearly steep
            extra_min = least["time_min"] - fast["time_min"]
            if extra_min > 4:
                continue
            saved = 1 - least["effort"] / fast["effort"]
            score = (fast["max_grade_pct"] - least["max_grade_pct"]) + 30 * saved - extra_min
            rows.append((score, A["name"], B[b]["name"], fast, least, extra_min, saved))
    rows.sort(key=lambda r: -r[0])
    print(f"{len(rows)} candidate pairs\n")
    for score, an, bn, fast, least, extra, saved in rows[: args.top]:
        print(f"{an}  ->  {bn}")
        print(f"   fastest {fast['distance_m']:.0f} m {fast['time_min']} min, max {fast['max_grade_pct']}%, "
              f"climb {fast['climb_m']} m | least effort +{extra:.1f} min, max {least['max_grade_pct']}%, "
              f"{100 * saved:.0f}% less effort")


if __name__ == "__main__":
    main()
