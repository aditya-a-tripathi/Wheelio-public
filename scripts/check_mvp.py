"""Check the whole MVP backend before a demo: every endpoint, edge cases, plain-language rule.

Start the server in dev mode first (fresh, so in-memory reports are empty):
    WHEELIO_DEV=1 uv run uvicorn backend.app.main:app --port 8000
    uv run python scripts/check_mvp.py
Prints PASS/FAIL per check and exits 1 if anything failed. Uses /api/dev/reports for report
statuses (no API key needed); with a key, the real /api/reports also exercises Claude vision.
"""
import io
import json
import math
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from barrier_demo import call  # noqa: E402

FAILS = []
BANNED = re.compile(r"%|\bADA\b|8\.33|effort units|—|percent", re.I)


def check(name, ok, info=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({info})" if info and not ok else ""))
    if not ok:
        FAILS.append(name)


def plain(texts):
    bad = [t for t in texts if BANNED.search(t)]
    return not bad, bad[:2]


def route(frm, to, **kw):
    return call("POST", "/api/route", {"from": frm, "to": {"place_id": to}, **kw})


def jpeg(seed=None):
    from PIL import Image
    img = Image.new("RGB", (640, 480), tuple((uuid.uuid4().bytes if seed is None else seed)[:3]))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def main():
    import urllib.request
    # ---- app shell
    for path in ("/", "/app.js", "/style.css", "/manifest.webmanifest"):
        import urllib.request
        try:
            code = urllib.request.urlopen("http://localhost:8000" + path).status
        except Exception as e:  # noqa: BLE001
            code = str(e)
        check(f"serves {path}", code == 200, code)

    # ---- places and routing
    code, places = call("GET", "/api/places")
    bldgs = [p for p in places if p["kind"] == "building"]
    check("places: buildings and bus stops", code == 200 and len(bldgs) > 100, len(places))
    texts, unreachable, slow = [], [], []
    for p in bldgs:
        c, d = route({"lat": 43.7033, "lon": -72.2887}, p["id"])  # from the Green
        if c == 200:
            for r in d["routes"]:
                texts += r["warnings"]
            texts.append(d["tradeoff_text"])
        elif c == 404:
            unreachable.append(p["name"])
            texts.append(d["detail"])
        else:
            slow.append((p["name"], c))
    check("every building gives a route or a plain 404", not slow, slow[:3])
    print(f"      {len(bldgs) - len(unreachable)} of {len(bldgs)} buildings reachable from the Green; "
          f"no stair-free way into: {', '.join(unreachable[:12])}")
    ok, bad = plain(texts)
    check("route text follows the plain-language rule", ok, bad)

    c, d = route({"place_id": "bldg:hanover-inn-dartmouth"}, "bldg:west-house-judge-hall")
    le, fa = d["routes"][0], d["routes"][1]
    check("demo pair: least effort avoids the very steep hill",
          c == 200 and fa["steepness"] == "very steep" and le["steepness"] != "very steep", d.get("tradeoff_text"))
    check("demo pair: fastest route lists its very steep hill up front",
          any(h["kind"] == "very_steep" for h in fa.get("hazards", [])) and not le.get("hazards"),
          [h["label"] for h in fa.get("hazards", [])])
    ok, bad = plain([h["label"] for h in fa.get("hazards", [])])
    check("hazard labels follow the plain-language rule", ok, bad)
    for name in ("network", "hazards", "buildings", "labels", "stops", "entrances", "parking"):
        try:
            code = urllib.request.urlopen(f"http://localhost:8000/data/{name}.geojson").status
        except Exception as e:  # noqa: BLE001
            code = str(e)
        check(f"map layer data/{name}.geojson served", code == 200, code)
    c, d = route({"lat": 43.6, "lon": -72.1}, "bldg:baker-library")
    check("start far off campus: plain 404", c == 404 and "too far" in d["detail"], d)
    c, d = route({"lat": 43.7056, "lon": -72.2886}, "bldg:not-a-building")
    check("unknown destination: plain 404", c == 404 and "know" in d["detail"], d)
    c, d = call("POST", "/api/route", {"to": {"place_id": "bldg:baker-library"}})
    check("missing start: plain 422", c == 422 and isinstance(d["detail"], str), d)
    c, d = route({"place_id": "bldg:baker-library"}, "bldg:baker-library")
    check("start at the destination: still a route", c == 200 and d["routes"][0]["distance_m"] < 50, c)
    for lim in (0, -5, 99):
        c, d = route({"lat": 43.7056, "lon": -72.2886}, "bldg:hood-museum-of-art", max_grade_pct=lim)
        check(f"odd steepness limit {lim}: still a route", c == 200, c)

    # ---- briefing
    c, d = route({"place_id": "bldg:hanover-inn-dartmouth"}, "bldg:west-house-judge-hall")
    c2, ex = call("POST", "/api/explain", {"route": d["routes"][0], "alternative": d["routes"][1]})
    check("briefing: text returned", c2 == 200 and len(ex["text"]) > 20, ex)
    ok, bad = plain([ex["text"]])
    check("briefing follows the plain-language rule", ok, bad)
    c, d = route({"place_id": "bldg:baker-library"}, "bldg:baker-library")
    c2, ex = call("POST", "/api/explain", {"route": d["routes"][0], "alternative": None})
    check("briefing for a zero-length route", c2 == 200, ex)

    # ---- sign-in
    c, accts = call("GET", "/api/auth/demo-accounts")
    check("demo accounts listed", c == 200 and {a["role"] for a in accts} == {"student", "facilities"})
    tok = {a["id"]: call("POST", "/api/auth/demo-signin", {"account_id": a["id"]})[1]["token"] for a in accts}
    c, _ = call("POST", "/api/auth/demo-signin", {"account_id": "nobody"})
    check("unknown demo account: 404", c == 404)
    c, _ = call("GET", "/api/auth/me")
    check("me without sign-in: 401", c == 401)
    c, _ = call("GET", "/api/auth/me", token="garbage")
    check("me with a bad token: 401", c == 401)

    # ---- reports (dev endpoint: no key needed)
    c, _ = call("POST", "/api/dev/reports", form={"photo": jpeg(), "lat": 43.7, "lon": -72.29}, token=tok["stu-alex"])
    if c in (404, 405):
        check("dev endpoint on (start the server with WHEELIO_DEV=1)", False)
        return
    spot = {"lat": 43.70255, "lon": -72.28948, "device_lat": 43.7026, "device_lon": -72.28948}
    trip = ({"place_id": "bldg:collis-center"}, "bldg:hood-museum-of-art")
    base = route(*trip)[1]["routes"][0]["distance_m"]

    c, unsure = call("POST", "/api/dev/reports", token=tok["stu-alex"],
                     form={"photo": jpeg(), **spot, "confidence": 0.3})
    check("unsure photo -> needs_confirm (even from a student)", c == 200 and unsure["status"] == "needs_confirm", unsure)
    r = route(*trip)[1]["routes"][0]
    check("needs_confirm doesn't change the route", r["distance_m"] == base and not r["report_ids"])

    c, rep = call("POST", "/api/dev/reports", token=tok["stu-riley"], form={"photo": jpeg(), **spot})
    check("second student's confident report of the same spot confirms it",
          c == 200 and rep["id"] == unsure["id"] and rep["status"] == "active", rep.get("status"))
    r = route(*trip)[1]["routes"][0]
    check("confirmed impassable barrier: route goes around", r["distance_m"] > base + 50, r["distance_m"])
    c, again = call("POST", "/api/dev/reports", token=tok["stu-riley"], form={"photo": jpeg(), **spot})
    check("same student reporting the same spot again: 409", c == 409, again)

    c, mine = call("POST", f"/api/reports/{rep['id']}/vote", {"still_there": True}, token=tok["stu-alex"])
    check("reporter's own 'still there' doesn't count as a confirmation",
          c == 200 and mine["confirmations"] == 1, mine.get("confirmations"))
    call("POST", f"/api/reports/{rep['id']}/vote", {"still_there": False}, token=tok["stu-jordan"])
    c, v = call("POST", f"/api/reports/{rep['id']}/vote", {"still_there": False}, token=tok["stu-jordan"])
    check("voting twice counts once", v["still_there_no"] == 1 and v["status"] == "active", v.get("status"))

    c, fl = call("GET", "/api/facilities/fixlist", token=tok["fac-sam"])
    check("fix list: blocking report first", c == 200 and fl["items"] and fl["items"][0]["report_id"] == rep["id"]
          and fl["items"][0]["priority"] == 1, fl)
    ok, bad = plain([fl["summary"]] + [i["title"] + " " + i["why"] for i in fl["items"]])
    check("fix list follows the plain-language rule", ok, bad)
    c, _ = call("GET", "/api/facilities/fixlist", token=tok["stu-alex"])
    check("fix list for a student: 403", c == 403)
    c, pub = call("GET", "/api/reports")
    check("public list hides the reporter", c == 200 and all(p["reported_by"] is None for p in pub))

    c, fixed = call("POST", f"/api/reports/{rep['id']}/verify", {"action": "fixed"}, token=tok["fac-sam"])
    check("facilities marks fixed -> cleared, route back",
          fixed["status"] == "cleared" and route(*trip)[1]["routes"][0]["distance_m"] == base)
    c, _ = call("POST", f"/api/reports/{rep['id']}/vote", {"still_there": True}, token=tok["stu-alex"])
    check("vote on a cleared report: 404", c == 404)

    # anyone can report; without an account it only warns (lighter penalty), never blocks
    c, anon = call("POST", "/api/dev/reports", form={"photo": jpeg(), "lat": 43.7015, "lon": -72.2905,
                                                    "device_lat": 43.7015, "device_lon": -72.2905})
    check("report without an account: accepted as a warning only",
          c == 200 and anon["status"] == "reported" and anon["reporter_kind"] == "anonymous", anon)
    c, v1 = call("POST", f"/api/reports/{anon['id']}/vote", {"still_there": True}, token=tok["stu-jordan"])
    check("anonymous + one campus account: confirmed", v1["status"] == "active" and v1["verified_reports"] == 1, v1.get("status"))
    call("POST", f"/api/reports/{anon['id']}/verify", {"action": "fixed"}, token=tok["fac-sam"])
    spot3 = {"lat": 43.70438, "lon": -72.29107, "device_lat": 43.7044, "device_lon": -72.29107}
    states = [call("POST", "/api/dev/reports", form={"photo": jpeg(), **spot3, "as_client": f"phone-{i}"})[1].get("status")
              for i in range(3)]
    check("three reports without accounts confirm it", states == ["reported", "reported", "active"], states)

    # rate limit: alex has 1 report this hour; 2 more allowed, the 4th refused
    far = [{"lat": 43.70438, "lon": -72.29107, "device_lat": 43.7044, "device_lon": -72.29107},
           {"lat": 43.7058, "lon": -72.2888, "device_lat": 43.7058, "device_lon": -72.2888},
           {"lat": 43.7015, "lon": -72.2905, "device_lat": 43.7015, "device_lon": -72.2905}]
    codes = [call("POST", "/api/dev/reports", token=tok["stu-alex"], form={"photo": jpeg(), **f})[0] for f in far]
    check("rate limit: 4th report in an hour refused (429)", codes == [200, 200, 429], codes)

    # real endpoint: Claude vision (or the no-key fallback) + photo storage
    c, real = call("POST", "/api/reports", token=tok["stu-jordan"],
                   form={"photo": jpeg(), "lat": 43.7058, "lon": -72.2888, "device_lat": 43.7058, "device_lon": -72.2888})
    check("real report endpoint accepts a photo", c == 200, real)
    if c == 200:
        import urllib.request
        try:
            pc = urllib.request.urlopen("http://localhost:8000" + real["photo_url"]).status
        except Exception as e:  # noqa: BLE001
            pc = str(e)
        check("report photo is served", pc == 200, pc)
    c, d = call("POST", "/api/reports", token=tok["stu-jordan"],
                form={"photo": b"not an image", "lat": 43.7058, "lon": -72.2888})
    check("non-image upload: plain 422", c == 422 and "photo" in d["detail"], d)
    c, d = call("GET", "/api/reports/r999/photo")
    check("unknown photo: 404", c == 404)

    # ---- in-process: expiry and starting next to a barrier (no clock control over HTTP)
    from backend.app import barriers
    from backend.app.routing import router
    from backend.app.schemas import Account, VisionResult
    acct = Account(id="stu-test", name="Test", role="student")
    vis = VisionResult(barrier_type="ice", severity="high", passable_for_manual_chair=False,
                       confidence=0.9, description="Ice across the path.")
    r1 = barriers.submit_report(acct, b"x1", 43.70438, -72.29107, 43.70438, -72.29107, vis)
    r1.expires_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    check("expired report drops off the list", r1.id not in [r.id for r in barriers.list_reports()])
    staff = Account(id="fac-test", name="Staff", role="facilities")
    r2 = barriers.submit_report(staff, b"x2", 43.70255, -72.28948, 43.70255, -72.28948, vis)
    mods, pts = barriers.get_edge_modifiers(), barriers.blocked_points()
    try:
        d = router.route({"lat": 43.70255, "lon": -72.28948}, "bldg:hood-museum-of-art",
                         edge_modifiers=mods, blocked_points=pts)
        ok = True
    except router.NoRoute as e:
        ok, d = "barrier" in str(e), str(e)
    check("start right at a confirmed barrier: a route away from it or a plain message", ok, d)
    check("staff report starts confirmed", r2.status == "active" and r2.verified_by_facilities)

    print(f"\n{'ALL PASSED' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
