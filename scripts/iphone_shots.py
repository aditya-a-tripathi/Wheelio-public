"""Screenshot the app as an iPhone 13 in Safari's engine (Playwright WebKit), driving it like a user.

One-time setup (not a project dependency):
    uvx --from playwright==1.48.0 playwright install webkit
Run (server on :8000):
    uvx --from playwright==1.48.0 python scripts/iphone_shots.py OUT_DIR URL STEPS_JSON
STEPS_JSON is a list of steps, run in order after the map has loaded:
    ["wait", ms]                 pause
    ["tap", "css selector"]      tap an element
    ["type", "css selector", "text"]   tap a field and type (on-screen keyboard is not simulated)
    ["eval", "js"]               run JavaScript in the page (e.g. map.jumpTo(...))
    ["swipe", x1, y1, x2, y2]    drag with a finger (CSS pixels)
    ["shot", "name"]             save OUT_DIR/name.png
Prints console errors and page errors at the end.
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

out, url, steps = Path(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.webkit.launch()
    ctx = browser.new_context(**p.devices["iPhone 13"], locale="en-US", timezone_id="America/New_York")
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto(url)
    page.wait_for_function("() => typeof mapReady !== 'undefined' && mapReady", timeout=90000)
    page.wait_for_timeout(1500)

    def settle():
        page.wait_for_function("() => map.loaded() && map.areTilesLoaded()", timeout=60000)
        page.wait_for_timeout(1200)

    for step in steps:
        kind = step[0]
        if kind == "wait":
            page.wait_for_timeout(step[1])
        elif kind == "tap":
            page.tap(step[1])
        elif kind == "type":
            page.tap(step[1])
            page.keyboard.type(step[2], delay=40)
        elif kind == "eval":
            page.evaluate(step[1])
        elif kind == "swipe":
            x1, y1, x2, y2 = step[1:5]
            page.mouse.move(x1, y1)
            page.mouse.down()
            page.mouse.move(x2, y2, steps=12)
            page.mouse.up()
        elif kind == "shot":
            settle()
            page.screenshot(path=str(out / f"{step[1]}.png"))
            print("saved", out / f"{step[1]}.png")
    for e in errors:
        if "Expected value to be of type number" not in e:
            print(e[:300])
    browser.close()
