"""Walk the barrier report loop against a running server and print each step.

Needs the server started with WHEELIO_DEV=1 (for /api/dev/reports, which skips Claude vision):
    WHEELIO_DEV=1 uv run uvicorn backend.app.main:app --reload --port 8000
    uv run python scripts/barrier_demo.py            # full story, then clears the barrier
    uv run python scripts/barrier_demo.py --keep     # leave the barrier confirmed (to see it in the app)
    uv run python scripts/barrier_demo.py --video --keep   # the recording's trip: Collis -> Hood Museum,
                                                           # barrier at Main St & Wheelock (108 m detour)

Story: Alex reports construction on the least-effort route from Hanover Inn to West House;
the route gets a warning. Jordan confirms it; the route goes around. Then the safeguards:
reporting without an account, same photo twice, phone too far away, students can't see who reported or verify.
Finally two people say it's gone and the route comes back.
"""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("WHEELIO_URL", "http://localhost:8000")
TRIP = {"from": {"place_id": "bldg:hanover-inn-dartmouth"}, "to": {"place_id": "bldg:west-house-judge-hall"}}
# Recording: blocking the sidewalk at the Main St / Wheelock corner moves the whole route (shares 38%).
VIDEO_TRIP = {"from": {"place_id": "bldg:collis-center"}, "to": {"place_id": "bldg:hood-museum-of-art"}}
VIDEO_SPOT = (43.70255, -72.28948)


def call(method, path, body=None, token=None, form=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = None
    if form is not None:
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in form.items():
            if k == "photo":
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="p.jpg"\r\n'
                             f"Content-Type: image/jpeg\r\n\r\n".encode() + v + b"\r\n")
            else:
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        data = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def show_route(label):
    _, d = call("POST", "/api/route", TRIP)
    le = d["routes"][0]
    print(f"   {label}: least effort {le['distance_m']:.0f} m, {le['time_min']} min, "
          f"reports on it {le.get('report_ids', [])}")
    for w in le["warnings"]:
        print(f"      warning: {w}")
    return le


def main():
    global TRIP
    keep = "--keep" in sys.argv
    video = "--video" in sys.argv
    if video:
        TRIP = VIDEO_TRIP
    tok = {a: call("POST", "/api/auth/demo-signin", {"account_id": a})[1]["token"]
           for a in ("stu-alex", "stu-jordan", "stu-riley", "fac-sam")}

    print("1. Before any report")
    before = show_route("route")
    seg = before["segments"][len(before["segments"]) // 2]
    lon, lat = seg["coordinates"][len(seg["coordinates"]) // 2]
    if video:
        lat, lon = VIDEO_SPOT
    near = {"lat": lat, "lon": lon, "device_lat": lat + 0.0001, "device_lon": lon}  # phone ~11 m away

    print("2. Someone reports construction fencing without an account (Claude confident)")
    photo = uuid.uuid4().bytes * 8
    code, r = call("POST", "/api/dev/reports", form={"photo": photo, **near})
    if code != 200:
        sys.exit(f"   {code}: {r.get('detail')} (restart the server to clear reports and hourly limits)")
    print(f"   {code} status={r['status']}: {r['status_text']}")
    rid = r["id"]
    show_route("route")

    print("3. Safeguards")
    code, e = call("POST", "/api/dev/reports", token=tok["stu-riley"], form={"photo": photo, **near})
    print(f"   same photo again -> {code}: {e['detail']}")
    far = {**near, "device_lat": lat + 0.003}
    code, e = call("POST", "/api/dev/reports", token=tok["stu-riley"], form={"photo": uuid.uuid4().bytes, **far})
    print(f"   phone 330 m away -> {code}: {e['detail']}")
    code, e = call("POST", f"/api/reports/{rid}/verify", {"action": "confirm"}, token=tok["stu-riley"])
    print(f"   student tries facilities verify -> {code}: {e['detail']}")
    _, pub = call("GET", "/api/reports")
    print(f"   students see reported_by = {pub[0]['reported_by']!r}")
    _, fac = call("GET", "/api/facilities/reports", token=tok["fac-sam"])
    print(f"   facilities see reported_by = {fac[0]['reported_by']!r}")

    print("4. Jordan (signed in) passes by and confirms it's still there")
    code, r = call("POST", f"/api/reports/{rid}/vote", {"still_there": True}, token=tok["stu-jordan"])
    print(f"   {code} status={r['status']}: {r['status_text']}")
    show_route("route")

    if keep:
        print("Leaving the barrier confirmed (--keep). Open the app to see the route go around it.")
        return
    print("5. Later: Riley and Alex say it's gone")
    call("POST", f"/api/reports/{rid}/vote", {"still_there": False}, token=tok["stu-riley"])
    code, r = call("POST", f"/api/reports/{rid}/vote", {"still_there": False}, token=tok["stu-alex"])
    print(f"   {code} status={r['status']}: {r['status_text']}")
    show_route("route")

    print("6. Facilities staff report a blocked ramp; Claude is unsure (confidence 0.4)")
    code, r = call("POST", "/api/dev/reports", token=tok["fac-sam"],
                   form={"photo": uuid.uuid4().bytes * 8, **near, "confidence": 0.4})
    print(f"   {code} status={r['status']}: {r['status_text']}")
    show_route("route")
    call("POST", f"/api/reports/{r['id']}/verify", {"action": "fixed"}, token=tok["fac-sam"])
    print("   facilities mark it fixed")
    show_route("route")


if __name__ == "__main__":
    main()
