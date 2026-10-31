"""Backend-owned. Buildings with an ADA entrance, plus bus stops as optional starts."""
import re
from functools import lru_cache

from fastapi import APIRouter

from ..routing import buildings
from ..routing.graph import load
from ..routing.router import slug
from ..schemas import Place

router = APIRouter()

_DROP = {"hanover", "nh", "new hampshire"}


def aliases(name: str, search: str) -> list[str]:
    """Dartmouth's Search_Field, split on commas, minus the name, town, state, ZIP and repeats."""
    seen, out = {name.lower()}, []
    for part in (search or "").split(","):
        a = part.strip()
        key = a.lower()
        if not a or key in seen or key in _DROP or re.fullmatch(r"\d{5}(-\d{4})?", a):
            continue
        seen.add(key)
        out.append(a)
    return out


@lru_cache(maxsize=1)
def all_places() -> list[dict]:
    g = load()
    out = []
    for pid, b in sorted(buildings(g).items(), key=lambda kv: kv[1]["name"].lower()):
        ent = b["entrances"][0]
        out.append({"id": pid, "name": b["name"], "kind": "building", "lat": ent["lat"], "lon": ent["lon"],
                    "aliases": aliases(b["name"], b["search"])})
    seen = set()
    for s in g.bus_stops:
        pid = f"stop:{slug(s['name'])}"
        if pid in seen:  # same stop listed once per direction
            continue
        seen.add(pid)
        out.append({"id": pid, "name": f"{s['name']} (bus stop)", "kind": "bus_stop", "lat": s["lat"],
                    "lon": s["lon"], "aliases": []})
    return out


@router.get("/places", response_model=list[Place])
def get_places():
    return all_places()
