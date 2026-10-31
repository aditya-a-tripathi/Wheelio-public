"""Route briefing: 1-2 plain sentences on what to expect (Backend-owned).

Code computes the facts (steep stretches and the building they are near, surfaces, crossings,
reports); Claude only turns those facts into friendly sentences, so it cannot invent a hill.
Without a key, or if Claude fails, a template writes the same facts. Briefings are cached per
route, so rerouting during navigation doesn't call Claude again for a route it has seen.
"""
import hashlib
import json
import math
import re

from pydantic import BaseModel

from . import client

STRETCH_WORDS = ("moderate", "steep", "very steep")
LANDMARK_M = 90.0  # a stretch is "near" a building whose entrance is this close

SYSTEM = """You write the short briefing a wheelchair user reads on a campus route card before \
they set off. Write 1 or 2 short sentences, at most 40 words, in plain friendly language.

Rules:
- Use only the facts given. Never add places, hills, surfaces or hazards that are not listed.
- Describe slopes only with the given words: gentle, moderate, steep, very steep. Never use \
percentages, grade numbers, "ADA" or technical terms.
- Mention the hardest stretch and where it is (the nearby building) if there is one; otherwise \
say it stays gentle. Mention reported barriers if any. Mention gravel or brick if listed.
- End with the accessible entrance it arrives at when that fits.
- No em dashes. No exclamation marks. Do not tell the user what to do beyond the route itself."""


class Briefing(BaseModel):
    text: str


_cache: dict[str, str] = {}


def _entrances():
    from ..routing.graph import load

    return load().entrances


def _meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    dy = (a[1] - b[1]) * 111_132.0
    dx = (a[0] - b[0]) * 111_320.0 * math.cos(math.radians(a[1]))
    return math.hypot(dx, dy)


def _near(lon: float, lat: float) -> str | None:
    best, best_d = None, LANDMARK_M
    for e in _entrances():
        d = _meters((lon, lat), (e["lon"], e["lat"]))
        if d < best_d:
            best, best_d = e["name"], d
    return best


def facts(route: dict, alternative: dict | None = None) -> dict:
    """Plain facts about a route (dicts in the RouteResponse shape)."""
    segs = route.get("segments", [])
    total = sum(_seg_len(s) for s in segs) or 1.0
    stretches, cur, done = [], None, 0.0
    for s in segs:
        word = s.get("steepness", "gentle")
        length = _seg_len(s)
        if word in STRETCH_WORDS:
            if cur and cur["word"] == word:
                cur["m"] += length
                cur["coords"] += s["coordinates"]
            else:
                cur = {"word": word, "m": length, "coords": list(s["coordinates"]), "at": done / total}
                stretches.append(cur)
        else:
            cur = None
        done += length
    stretches = [st for st in stretches if st["m"] >= 10]
    rank = {w: i for i, w in enumerate(STRETCH_WORDS)}
    stretches.sort(key=lambda st: (rank[st["word"]], st["m"]), reverse=True)
    out_stretches = []
    for st in stretches[:2]:
        mid = st["coords"][len(st["coords"]) // 2]
        where = "near the start" if st["at"] < 0.25 else "near the end" if st["at"] > 0.75 else "partway along"
        out_stretches.append({"steepness": st["word"], "length_ft": round(st["m"] * 3.28 / 10) * 10,
                              "near_building": _near(*mid), "where": where})
    surfaces = {}
    for s in segs:
        if s.get("material") in ("Gravel", "Brick"):
            surfaces[s["material"].lower()] = surfaces.get(s["material"].lower(), 0) + _seg_len(s)
    f = {
        "minutes": max(1, round(route.get("time_min", 0))),
        "overall": route.get("steepness", "gentle"),
        "hardest_stretches": out_stretches,
        "rough_surfaces_ft": {k: round(v * 3.28) for k, v in surfaces.items() if v >= 15},
        "street_crossings": sum(1 for s in segs if s.get("material") == "Crosswalk"),
        "arrives_at": (route.get("entrance") or {}).get("name"),
        "barrier_reports_on_route": [w for w in route.get("warnings", []) if "barrier" in w.lower()],
        "route_kind": "least effort" if route.get("id") == "least_effort" else "fastest",
    }
    if alternative:
        f["other_route"] = {"minutes": max(1, round(alternative.get("time_min", 0))),
                            "overall": alternative.get("steepness", "gentle")}
    return f


def _seg_len(s: dict) -> float:
    c = s["coordinates"]
    return sum(_meters(tuple(c[i]), tuple(c[i + 1])) for i in range(len(c) - 1))


def fallback_text(f: dict) -> str:
    """The template briefing: same facts, no AI."""
    parts = []
    if f["hardest_stretches"]:
        st = f["hardest_stretches"][0]
        where = f"near {st['near_building']}" if st["near_building"] else st["where"]
        parts.append(f"About {f['minutes']} min, mostly gentle with a {st['steepness']} stretch {where}.")
    else:
        parts.append(f"About {f['minutes']} min on gentle slopes the whole way.")
    rough = " and ".join(f["rough_surfaces_ft"])
    if rough:
        parts.append(f"Part of it is on {rough}.")
    if f["barrier_reports_on_route"]:
        parts.append("Someone reported a barrier on this route.")
    if f["arrives_at"]:
        parts.append(f"Ends at the accessible entrance of {f['arrives_at']}.")
    return " ".join(parts[:3])


def _clean(text: str) -> str | None:
    text = text.strip().replace("—", ", ").replace("–", ", ")
    if not text or "%" in text or re.search(r"\bADA\b|percent|grade", text, re.I) or len(text) > 320:
        return None  # breaks the plain-language rule: use the template instead
    return text


def explain(route: dict, alternative: dict | None = None) -> tuple[str, str]:
    """(text, source) where source is "claude" or "summary"."""
    f = facts(route, alternative)
    key = hashlib.sha256(json.dumps(f, sort_keys=True).encode()).hexdigest()
    if key in _cache:
        return _cache[key], "claude"
    try:
        out = client.structured(SYSTEM, "Route facts:\n" + json.dumps(f, indent=1), Briefing, effort="low")
        text = _clean(out.text)
        if text:
            if len(_cache) > 2000:
                _cache.clear()
            _cache[key] = text
            return text, "claude"
    except client.AIUnavailable:
        pass
    return fallback_text(f), "summary"
