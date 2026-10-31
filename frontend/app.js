// Wheelio frontend: MapLibre map (2D by default), route picking, map click to set the start.
// Hazard layers, labels, palettes and the Map key live in maplayers.js.
// Uses fixtures from mock/ with ?mock=1, otherwise the real API (docs/api.md).
const USE_MOCK = new URLSearchParams(location.search).has("mock");

// Route identity is the name and the card; the line itself is colored by difficulty (selected route)
// or drawn gray and dashed (the other route). `color` is the identity accent for pills and cards.
const ROUTES = {
  least_effort: { label: "Least effort", color: "#1f5fae", dash: null },
  fastest: { label: "Fastest", color: "#5b6670", dash: [1.5, 1] },
};
const ALT_ROUTE_COLOR = "#8a949d";
const CLS = { gentle: "gentle", moderate: "moderate", steep: "steep", "very steep": "very_steep" };
// Shared steepness words (plain-language rule): users see words, never grade percentages.
// The backend sends `steepness`; this fallback covers older fixtures that only carry grades.
function steepnessOf(grade) {
  return grade < 5 ? "gentle" : grade < 8.33 ? "moderate" : grade <= 12 ? "steep" : "very steep";
}
const isSteep = (word) => word === "steep" || word === "very steep";
const ROUTE_STEEPNESS = {
  gentle: "Gentle slopes all the way",
  moderate: "Some moderate slopes",
  steep: "Has a steep hill",
  "very steep": "Has a very steep hill",
};

// Moderate tilt so buildings never hide the route (max 50 degrees).
const VIEW_3D = { pitch: 48, bearing: -15 };
const VIEW_2D = { pitch: 0, bearing: 0 };
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

const state = {
  places: [],
  start: null, // {lat, lon}
  dest: null, // place object
  routes: [], // from POST /api/route
  selected: "least_effort",
  view: loadView(),
};

// ---------- Map ----------

// ?at=lat,lon,zoom opens the map at a spot (staging the recording, checking a place).
const AT = (new URLSearchParams(location.search).get("at") || "").split(",").map(Number);
const map = new maplibregl.Map({
  container: "map",
  style: "https://tiles.openfreemap.org/styles/positron",
  center: AT.length >= 2 && AT.every(Number.isFinite) ? [AT[1], AT[0]] : [-72.2887, 43.704],
  zoom: AT.length === 3 && Number.isFinite(AT[2]) ? AT[2] : 16.4,
  maxPitch: 50,
  maxZoom: 19, // closer than this, the tilted camera clips into buildings and slices them open
  attributionControl: false, // added top-right below, where the bottom sheet can't cover it
  ...(state.view === "3d" ? VIEW_3D : VIEW_2D),
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
// Tile terms require the attribution to stay visible (also in the demo video).
// No `compact` option: MapLibre shows the full line on wide screens (the laptop recording) and a
// compact "i" button on phones, where the full line would run across the map.
map.addControl(new maplibregl.AttributionControl(), "top-right");

// ---------- Bottom sheet ----------
// Phone: three heights. Peek shows the one-line summary, half shows search or route cards, full
// shows everything. Drag the header (a flick picks the next height in that direction) or press
// the handle button. Wide screens dock the sheet on the left and skip all of this.

const sheet = document.getElementById("sheet");
const startBar = document.getElementById("start-bar");
const sheetHandle = document.getElementById("sheet-handle");
const wideScreen = matchMedia("(min-width: 900px)");
let sheetSnap = "half";

function sheetOffsets() {
  const height = sheet.offsetHeight;
  const header = sheet.querySelector(".sheet-header").offsetHeight;
  const bar = startBar.hidden ? 0 : startBar.offsetHeight; // peek stays visible above the Start bar
  // Planning: 45% of the screen. Choosing a route: just the row of route cards and the Start bar
  // (about a quarter of the screen); drag up for the briefing and details.
  let visible = Math.round(innerHeight * 0.45);
  const cardsEl = document.getElementById("cards");
  if (sheet.dataset.view === "routes" && cardsEl?.children.length) {
    const body = sheet.querySelector(".sheet-body");
    const cardsBottom = cardsEl.getBoundingClientRect().bottom - sheet.getBoundingClientRect().top + body.scrollTop;
    visible = Math.min(Math.round(innerHeight * 0.4), Math.ceil(cardsBottom + bar + 10));
  }
  return { full: 0, half: Math.max(0, height - visible), peek: Math.max(0, height - header - bar) };
}

function setSheet(snap, { animate = true } = {}) {
  sheetSnap = snap;
  sheet.dataset.snap = snap; // first: the header's height (measured below) depends on it
  if (wideScreen.matches) {
    document.documentElement.style.setProperty("--sheet-visible", "0px");
    sheet.style.removeProperty("--sheet-offset");
    sheet.style.setProperty("--sheet-hidden", "0px");
    return;
  }
  const offset = sheetOffsets()[snap];
  // Things that float above the sheet (the report button) follow its visible height.
  document.documentElement.style.setProperty("--sheet-visible", `${sheet.offsetHeight - offset}px`);
  sheet.classList.toggle("dragging", !animate);
  sheet.style.setProperty("--sheet-offset", `${offset}px`);
  sheet.style.setProperty("--sheet-hidden", `${offset}px`);
  sheetHandle.setAttribute("aria-expanded", String(snap !== "peek"));
  sheetHandle.setAttribute("aria-label", snap === "full" ? "Show less" : "Show more");
}

// Visible height of the sheet over the map at its current snap (0 when docked on a wide screen).
function sheetVisibleHeight() {
  return wideScreen.matches ? 0 : sheet.offsetHeight - sheetOffsets()[sheetSnap];
}

// Map padding that keeps routes and markers clear of the sheet and the top controls.
function mapPadding() {
  if (wideScreen.matches) return { top: 120, bottom: 110, left: sheet.getBoundingClientRect().right + 60, right: 80 };
  const top = 190; // clears the search pill and the 2D/3D and Map key buttons, plus the door card above its door
  const bottom = Math.min(sheetVisibleHeight() + 36, innerHeight - top - 120);
  return { top, bottom, left: 110, right: 70 }; // the door card is ~250 px wide; keep it on screen
}

// Center on a point in the part of the map the sheet doesn't cover.
function centerOn(lngLat) {
  map.easeTo({ center: lngLat, offset: [0, -sheetVisibleHeight() / 2], duration: reduceMotion ? 0 : 500 });
}

// The web font changes text heights after first paint; re-measure the sheet once it has loaded.
document.fonts?.ready.then(() => !wideScreen.matches && setSheet(sheetSnap, { animate: false }));

// Phones: typing in From/To turns the sheet into a full-screen search page (like Google Maps).
// iPhone Safari mis-scrolls when a focused field sits inside a transformed (sliding) panel and
// pushed the fields off screen; the search page has no transform and the page can't scroll.
const rootEl = document.documentElement;
function startTripEditing(e) {
  if (wideScreen.matches || !e.target.matches?.(".trip input")) return;
  if (!rootEl.classList.contains("trip-editing")) {
    rootEl.classList.add("trip-editing");
    window.scrollTo(0, 0);
  }
}
document.addEventListener("touchstart", startTripEditing, { passive: true, capture: true });
document.addEventListener("pointerdown", startTripEditing, { capture: true });
document.addEventListener("focusin", startTripEditing);
document.addEventListener("focusout", () => {
  setTimeout(() => {
    if (document.activeElement?.matches?.(".trip input")) return;
    if (!rootEl.classList.contains("trip-editing")) return;
    rootEl.classList.remove("trip-editing");
    window.scrollTo(0, 0);
    if (!wideScreen.matches) setSheet(sheetSnap, { animate: false });
  }, 300);
});
// If Safari still nudges the page while the keyboard opens, put it back.
window.visualViewport?.addEventListener("resize", () => {
  if (rootEl.classList.contains("trip-editing")) window.scrollTo(0, 0);
});

// The handle steps up (peek, half, full) and from full back down to peek.
let sheetDragged = false;
sheetHandle.addEventListener("click", () => {
  if (sheetDragged) return;
  setSheet(sheetSnap === "peek" ? "half" : sheetSnap === "half" ? "full" : "peek");
});

(function enableSheetDrag() {
  const header = sheet.querySelector(".sheet-header");
  let startY = 0;
  let startOffset = 0;
  let lastY = 0;
  let lastT = 0;
  let velocity = 0;
  let pointerId = null;

  header.addEventListener("pointerdown", (e) => {
    if (wideScreen.matches || e.button !== 0) return;
    pointerId = e.pointerId;
    startY = lastY = e.clientY;
    lastT = e.timeStamp;
    startOffset = sheetOffsets()[sheetSnap];
    velocity = 0;
    sheetDragged = false;
  });
  header.addEventListener("pointermove", (e) => {
    if (e.pointerId !== pointerId) return;
    const dy = e.clientY - startY;
    if (!sheetDragged && Math.abs(dy) < 6) return; // a tap on the handle stays a click
    if (!sheetDragged) {
      sheetDragged = true;
      header.setPointerCapture(pointerId); // keep dragging when the finger leaves the header
      sheet.classList.add("dragging");
    }
    const { peek } = sheetOffsets();
    let offset = startOffset + dy;
    if (offset < 0) offset /= 3; // resist past the top instead of stopping dead
    offset = Math.min(offset, peek + 40);
    sheet.style.setProperty("--sheet-offset", `${offset}px`);
    velocity = (e.clientY - lastY) / Math.max(1, e.timeStamp - lastT);
    lastY = e.clientY;
    lastT = e.timeStamp;
  });
  const end = (e) => {
    if (e.pointerId !== pointerId) return;
    pointerId = null;
    if (!sheetDragged) return;
    const offsets = sheetOffsets();
    const current = startOffset + (lastY - startY);
    const order = ["full", "half", "peek"]; // top to bottom
    let target;
    if (Math.abs(velocity) > 0.5) {
      // Flick: next height in the flick's direction.
      const i = order.indexOf(sheetSnap);
      target = order[Math.max(0, Math.min(order.length - 1, i + (velocity > 0 ? 1 : -1)))];
    } else {
      target = order.reduce((best, s) => (Math.abs(offsets[s] - current) < Math.abs(offsets[best] - current) ? s : best));
    }
    setSheet(target);
    setTimeout(() => (sheetDragged = false), 0); // swallow the click that follows a drag
  };
  header.addEventListener("pointerup", end);
  header.addEventListener("pointercancel", end);
})();

wideScreen.addEventListener("change", () => setSheet(sheetSnap, { animate: false }));
addEventListener("resize", () => setSheet(sheetSnap, { animate: false }));
setSheet("half", { animate: false });

// Typing in a search field opens the sheet fully, so the suggestions aren't under the keyboard.
for (const input of document.querySelectorAll(".combo input")) {
  input.addEventListener("focus", () => !wideScreen.matches && setSheet("full"));
}

// ---------- Sheet views ----------
// plan: From/To fields. routes: "Choose a route" with the cards and the pinned Start bar.
// nav: the countdown. The search pill over the map and the compact trip row mirror the trip.

function setSheetView(view) {
  sheet.dataset.view = view;
  updateChrome();
}

function updateChrome() {
  const view = sheet.dataset.view;
  const showBar = view === "routes" && state.routes.length > 0;
  startBar.hidden = !showBar;
  sheet.style.setProperty("--bar-h", showBar ? `${startBar.offsetHeight}px` : "0px");

  const pill = document.getElementById("search-pill");
  pill.classList.toggle("empty", !state.dest);
  document.getElementById("search-pill-text").textContent = state.dest ? state.dest.name : "Where to?";
  pill.setAttribute("aria-label", state.dest ? `${state.dest.name}, change destination` : "Where to? Search for a building");
  document.getElementById("search-clear").hidden = !state.dest;
  document.querySelector(".search-bar").hidden = view === "nav";

  document.getElementById("trip-from").textContent = startCombo?.getValue() || "Not set";
  document.getElementById("trip-to").textContent = state.dest?.name ?? "Not set";
}

// The pill and the trip row both open the fields; the pill's X clears the destination.
function editTrip(focusId) {
  setSheetView("plan");
  setSheet("full", { animate: false });
  if (!wideScreen.matches) {
    rootEl.classList.add("trip-editing"); // full-screen search page before the keyboard opens
    window.scrollTo(0, 0);
  }
  const input = document.getElementById(focusId);
  input.focus();
  input.select(); // typing replaces the old destination, as in Google Maps
}
document.getElementById("search-pill").addEventListener("click", () => editTrip("dest-input"));
document.getElementById("trip-row").addEventListener("click", () => editTrip("dest-input"));
document.getElementById("search-clear").addEventListener("click", () => {
  setDest(null);
  setSheetView("plan");
  setSheet("half");
  showStatus("Choose where you are going.");
});

// One line in the sheet header that says where things stand, readable even at peek height.
function summarize() {
  const selected = state.routes.find((r) => r.id === state.selected);
  let text = "Where to?";
  if (nav.active) text = nav.summary || "Starting…";
  else if (state.loading) text = "Finding routes…";
  else if (selected) text = `${routeLabel(selected)}, ${fmtMinutes(selected.time_min)}. ${ROUTE_STEEPNESS[selected.steepness ?? steepnessOf(selected.max_grade_pct)]}.`;
  else if (state.routeError) text = "No route found";
  else if (state.dest && !state.start) text = `To ${state.dest.name}. Set your start.`;
  document.getElementById("summary").textContent = text;
}

const startMarker = new maplibregl.Marker({ element: el("div", { class: "pin-start", "aria-hidden": "true" }), draggable: true });
startMarker.on("dragend", () => {
  const { lng, lat } = startMarker.getLngLat();
  setStart({ lat, lon: lng });
});
let entranceMarkers = [];
let pillMarkers = [];
let startCombo = null;
let destCombo = null;

// The building list, URL presets and route requests don't wait for the map (a slow tile server or
// weak signal would leave search empty); the map only has to be ready to draw. loadPlaces() runs at
// the end of this file, and anything already found is drawn once the map is ready.
let mapReady = false;
map.on("load", async () => {
  restyleBaseMap();
  try {
    await addInfoLayers(); // maplayers.js: hazards, labels, stops, entrances (never blocks routing)
  } catch (err) {
    console.error("map layers", err);
  }
  addRouteLayers();
  mapReady = true;
  highlightDestination(state.dest?.name);
  setView(state.view, { animate: false });
  if (state.routes.length) {
    renderRoutes();
    fitToRoutes();
  }
  // On phones the attribution starts expanded across the map; fold it to its "i" button.
  document.querySelector(".maplibregl-ctrl-attrib.maplibregl-compact")?.classList.remove("maplibregl-compact-show");
});

// Recolor the Positron base to a cool, quiet paper gray: the base map steps back so the hazard
// signs and difficulty colors (maplayers.js) are what you see first.
function restyleBaseMap() {
  const paint = (id, prop, value) => map.getLayer(id) && map.setPaintProperty(id, prop, value);
  paint("background", "background-color", "#eceff1");
  paint("landuse_residential", "fill-color", "#e6eaed");
  paint("park", "fill-color", "#d9ead0");
  paint("landcover_wood", "fill-color", "#c3dfb3");
  paint("water", "fill-color", "#a9cfe6");
  paint("building", "fill-color", "#e1e5e8"); // town buildings; campus ones come from Dartmouth's data
  paint("building", "fill-outline-color", "#d3d9de");
  paint("highway_path", "line-color", "#ffffff");
  paint("highway_minor", "line-color", "#ffffff");
  paint("highway_major_casing", "line-color", "#cfd5da");
  paint("highway_major_inner", "line-color", "#ffffff");
  addGreenery();

  map.setLight({ anchor: "viewport", color: "#ffffff", intensity: 0.4, position: [1.4, 210, 35] });

  // Extruded buildings go under the labels; route layers are added later on top of everything.
  const firstLabel = map.getStyle().layers.find((l) => l.type === "symbol")?.id;
  map.addLayer(
    {
      id: "building-3d",
      type: "fill-extrusion",
      source: "openmaptiles",
      "source-layer": "building",
      minzoom: 14,
      paint: {
        "fill-extrusion-color": "#e9edf0",
        "fill-extrusion-height": ["coalesce", ["get", "render_height"], 6],
        "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
        // See-through, so a route (drawn above) is never hidden or seems to cut through a wall.
        "fill-extrusion-opacity": 0.6,
      },
    },
    firstLabel,
  );
}

// Lawns, parks, fields and woods in lively greens (Positron leaves most of them out). They go
// under the roads, buildings and routes, and stay light enough that both route colors keep
// their contrast on top of them.
function addGreenery() {
  const below = map.getLayer("waterway") ? "waterway" : undefined;
  const fadeIn = ["interpolate", ["linear"], ["zoom"], 12, 0.5, 15, 1];

  map.addImage("tree-dots", treeDotsPattern(), { pixelRatio: 2 });

  map.addLayer({
    id: "green-grass",
    type: "fill",
    source: "openmaptiles",
    "source-layer": "landcover",
    filter: ["==", ["get", "class"], "grass"],
    paint: { "fill-color": "#d3e8c3", "fill-opacity": fadeIn },
  }, below);
  map.addLayer({
    id: "green-grounds",
    type: "fill",
    source: "openmaptiles",
    "source-layer": "landuse",
    filter: ["in", ["get", "class"], ["literal", ["playground", "cemetery"]]],
    paint: { "fill-color": "#d6eac8", "fill-opacity": fadeIn },
  }, below);
  map.addLayer({
    id: "green-fields",
    type: "fill",
    source: "openmaptiles",
    "source-layer": "landuse",
    filter: ["==", ["get", "class"], "pitch"],
    paint: { "fill-color": "#c6e2b0", "fill-outline-color": "#a8cf8e", "fill-opacity": fadeIn },
  }, below);
  // Places where OpenStreetMap is wrong and the team knows better (e.g. the paved plaza in front of
  // the Hop is mapped as grass). Each feature in map-corrections.geojson notes who verified it.
  map.addSource("corrections", { type: "geojson", data: "map-corrections.geojson" });
  map.addLayer({
    id: "correction-paved",
    type: "fill",
    source: "corrections",
    filter: ["==", ["get", "kind"], "paved"],
    paint: { "fill-color": "#f3f5f6" },
  }, below);
  // A thin edge in the same color covers the anti-aliased green fringe under the patch.
  map.addLayer({
    id: "correction-paved-edge",
    type: "line",
    source: "corrections",
    filter: ["==", ["get", "kind"], "paved"],
    paint: { "line-color": "#f3f5f6", "line-width": 2 },
  }, below);
  map.addLayer({
    id: "green-wood-trees",
    type: "fill",
    source: "openmaptiles",
    "source-layer": "landcover",
    filter: ["==", ["get", "class"], "wood"],
    minzoom: 14,
    paint: { "fill-pattern": "tree-dots", "fill-opacity": 0.9 },
  }, below);
}

// Tiny repeating tree-crown dots for wooded areas, drawn once on a canvas.
function treeDotsPattern() {
  const size = 32; // device pixels; pixelRatio 2 makes it a 16 px tile
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  const crown = (x, y, r) => {
    ctx.fillStyle = "#9cc97c";
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#b3d897"; // lit side of the crown
    ctx.beginPath();
    ctx.arc(x - r * 0.3, y - r * 0.3, r * 0.5, 0, Math.PI * 2);
    ctx.fill();
  };
  crown(8, 9, 5);
  crown(24, 25, 5);
  return ctx.getImageData(0, 0, size, size);
}

function addRouteLayers() {
  const empty = { type: "FeatureCollection", features: [] };
  // One feature per route segment: {id, selected, cls, material}. Routes draw above everything
  // (buildings are see-through in 3D), so a route never looks like it goes through a wall.
  map.addSource("routes", { type: "geojson", data: empty });
  const sel = ["boolean", ["get", "selected"], false];

  for (const id of Object.keys(ROUTES)) {
    const mine = ["==", ["get", "id"], id];
    // The other route: gray and dashed, clearly the alternative.
    map.addLayer({
      id: `route-alt-${id}`, type: "line", source: "routes", filter: ["all", mine, ["!", sel]],
      layout: { "line-cap": "butt", "line-join": "round" },
      paint: { "line-color": ALT_ROUTE_COLOR, "line-width": 5, "line-dasharray": [1.4, 1] },
    });
    map.addLayer({
      id: `route-casing-${id}`, type: "line", source: "routes", filter: ["all", mine, sel],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#ffffff", "line-width": 13 },
    });
    // The chosen route: every stretch colored by how hard it is (green easy ... red very steep).
    map.addLayer({
      id: `route-line-${id}`, type: "line", source: "routes", filter: ["all", mine, sel],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": clsColor(), "line-width": 8 },
    });
    // Wide invisible line so the route is easy to click.
    map.addLayer({
      id: `route-hit-${id}`, type: "line", source: "routes", filter: mine,
      paint: { "line-color": "#000", "line-width": 24, "line-opacity": 0.001 },
    });
    map.on("mouseenter", `route-hit-${id}`, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", `route-hit-${id}`, () => (map.getCanvas().style.cursor = ""));
  }
  // Second cue for steep stretches: white breaks cut into the line (colorblind mode), and brown
  // dots on gravel (always), so color is never the only signal.
  map.addLayer({
    id: "route-steep-pattern", type: "line", source: "routes",
    filter: ["all", sel, ["in", ["get", "cls"], ["literal", ["steep", "very_steep"]]]],
    layout: { "line-cap": "butt", visibility: "none" },
    paint: { "line-color": "#ffffff", "line-width": 3, "line-dasharray": ["literal", [1, 1.5]] },
  });
  map.addLayer({
    id: "route-gravel", type: "line", source: "routes", filter: ["all", sel, ["==", ["get", "material"], "gravel"]],
    layout: { "line-cap": "round" },
    paint: { "line-color": "#5a3a1a", "line-width": 3, "line-dasharray": [0.1, 1.6] },
  });

  // While navigating, the part already traveled turns gray over the route (Google-style progress).
  // The source holds the whole route; a gradient split at line-progress paints the traveled share,
  // so moving the split each frame is a style change, not a geometry rebuild.
  map.addSource("traveled", { type: "geojson", data: empty, lineMetrics: true });
  map.addLayer({
    id: "route-traveled", type: "line", source: "traveled",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-width": 8, "line-gradient": traveledGradient(0) },
  });

  // Lighter streak from a dot that is off the path to where the route picks it up (updateConnector).
  map.addSource("connector", { type: "geojson", data: empty });
  map.addLayer({
    id: "connector-line", type: "line", source: "connector",
    layout: { "line-cap": "round" },
    paint: { "line-color": "#1f5fae", "line-width": 6, "line-opacity": 0.35 },
  });
  recolorRoutes();
}

// Palette change (Colorblind mode): route colors and the steep-stretch pattern.
function recolorRoutes() {
  if (!mapReady && !map.getLayer("route-steep-pattern")) return;
  for (const id of Object.keys(ROUTES)) map.setPaintProperty(`route-line-${id}`, "line-color", clsColor());
  map.setLayoutProperty("route-steep-pattern", "visibility", settings.colorblind ? "visible" : "none");
  if (mapReady) renderRoutes();
}
window.recolorRoutes = recolorRoutes;

// Per-segment features for a route (falls back to one gray-free piece for old fixtures).
function routeFeatures(r) {
  const selected = r.id === state.selected;
  const segs = r.segments?.length ? r.segments : [{ coordinates: r.geojson.coordinates, steepness: r.steepness ?? "gentle" }];
  return segs.map((s) => ({
    type: "Feature",
    geometry: { type: "LineString", coordinates: s.coordinates },
    properties: { id: r.id, selected, cls: CLS[s.steepness ?? steepnessOf(s.grade_pct ?? 0)] ?? "gentle", material: (s.material ?? "").toLowerCase() },
  }));
}

// Click a route line to select it; click anywhere else to set the start.
// Works while tilted: MapLibre unprojects the click to the ground plane.
map.on("click", (e) => {
  // While navigating, the start is wherever you are; while placing a barrier pin, a tap moves the pin.
  if (nav.active || window.isPlacing?.()) return;
  const hitLayers = Object.keys(ROUTES).map((id) => `route-hit-${id}`);
  const hit = map.queryRenderedFeatures(e.point, { layers: hitLayers })[0];
  if (hit) {
    selectRoute(hit.properties.id);
    return;
  }
  setStart({ lat: e.lngLat.lat, lon: e.lngLat.lng });
});

// ---------- 2D / 3D ----------

function loadView() {
  try {
    return localStorage.getItem("wheelio:view2") === "3d" ? "3d" : "2d";
  } catch {
    return "2d";
  }
}

function setView(view, { animate = true } = {}) {
  state.view = view;
  try {
    localStorage.setItem("wheelio:view2", view);
  } catch {}
  for (const btn of document.querySelectorAll(".view-toggle button")) {
    btn.setAttribute("aria-pressed", String(btn.dataset.view === view));
  }
  const is3d = view === "3d";
  map.setLayoutProperty("building-3d", "visibility", is3d ? "visible" : "none");
  highlightDestination(state.dest?.kind === "building" ? state.dest.name : null);
  for (const id of ["campus-buildings", "campus-buildings-edge"]) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", is3d ? "none" : "visible");
  }
  map.easeTo({ ...(is3d ? VIEW_3D : VIEW_2D), duration: animate && !reduceMotion ? 600 : 0 });
}

for (const btn of document.querySelectorAll(".view-toggle button")) {
  btn.addEventListener("click", () => setView(btn.dataset.view));
}

// ---------- Data ----------

async function loadPlaces() {
  try {
    const res = await fetch(USE_MOCK ? "mock/places.json" : "/api/places");
    if (!res.ok) throw new Error(`places ${res.status}`);
    state.places = await res.json();
  } catch (err) {
    console.error(err);
    showStatus("Could not load campus buildings. Reload the page to try again.", true);
    return;
  }
  // "Use my location" shows only for an empty field or a matching query, so Enter on "collis" picks Collis.
  const myLocation = { id: "my-location", label: "Use my location", detail: "From your device's location" };
  startCombo = createCombobox("start", (q) => [
    ...(!q || normalize(myLocation.label).includes(normalize(q)) ? [myLocation] : []),
    ...searchPlaces(q, () => true),
  ], (opt) => (opt.id === "my-location" ? useMyLocation() : setStart(placePoint(opt.place), opt.place.name)));
  destCombo = createCombobox("dest", (q) => searchPlaces(q, (p) => p.kind === "building"), (opt) => setDest(opt.place));

  // Presets for the demo recording and screenshots: ?start=lat,lon and ?to=<place id>.
  const params = new URLSearchParams(location.search);
  const to = state.places.find((p) => p.id === params.get("to"));
  if (to) setDest(to);
  const preset = params.get("start")?.split(",").map(Number);
  if (preset?.length === 2 && preset.every(Number.isFinite)) setStart({ lat: preset[0], lon: preset[1] });
  if (!state.dest) showStatus("Choose where you are going.");
}

// label: text for the From field; a map point gets a generic label.
function setStart(point, label = "Point on the map") {
  state.start = point;
  startMarker.setLngLat([point.lon, point.lat]).addTo(map);
  startCombo?.setValue(label);
  updateChrome();
  if (!state.dest) {
    showStatus("Start set. Now choose where you are going.");
    centerOn([point.lon, point.lat]);
  }
  summarize();
  requestRoute();
}

function setDest(place) {
  state.dest = place;
  highlightDestination(place?.kind === "building" ? place.name : null);
  destCombo?.setValue(place ? place.name : "");
  if (place && !state.start) {
    showStatus("Destination set. Now set your start: use your location, pick a building or tap the map.");
    centerOn([place.lon, place.lat]);
  }
  state.routes = []; // routes to the old destination would mislead while the new ones load
  renderRoutes();
  requestRoute();
}

// One marker per accessible entrance the routes end at (least effort and fastest can use different
// doors). The selected route's door gets the full dark card; another route's door gets a lighter
// card that is also a button for switching to that route. Before any route, the building's place
// point (itself an accessible entrance) gets the dark card.
function renderEntrances() {
  for (const m of entranceMarkers) m.remove();
  entranceMarkers = [];
  if (!state.dest) return;

  const groups = new Map();
  for (const r of state.routes) {
    if (!r.entrance || (nav.active && r.id !== state.selected)) continue;
    if (!groups.has(r.entrance.id)) groups.set(r.entrance.id, { entrance: r.entrance, routes: [] });
    groups.get(r.entrance.id).routes.push(r);
  }
  if (!groups.size) {
    addEntranceMarker([state.dest.lon, state.dest.lat], entranceCard(state.dest.name, "Accessible entrance", null), true);
    return;
  }
  const bothRoutes = groups.size === 1 && state.routes.length > 1;
  for (const { entrance, routes } of groups.values()) {
    const forSelected = routes.some((r) => r.id === state.selected);
    const route = routes.find((r) => r.id === state.selected) ?? routes[0];
    const which = bothRoutes ? "both routes" : `${routeLabel(route).toLowerCase()} route`;
    // With a single route (or one door for both), "which route" adds nothing; keep the card short.
    const sub = state.routes.length === 1 || nav.active ? "Accessible entrance" : `Accessible entrance, ${which}`;
    const element = forSelected ? entranceCard(state.dest.name, sub, route.id) : entranceButton(route, which);
    addEntranceMarker([entrance.lon, entrance.lat], element, forSelected);
  }
}

// The selected door's card sits above its dot, another door's card below its dot, so two doors
// a few dozen meters apart don't stack their cards on top of each other.
function addEntranceMarker(lngLat, element, selected) {
  // offset: the dot's center (not the marker's edge) sits on the entrance
  const marker = new maplibregl.Marker({ element, anchor: selected ? "bottom" : "top", offset: [0, selected ? 6 : -6] })
    .setLngLat(lngLat)
    .addTo(map);
  marker.getElement().style.zIndex = selected ? "3" : "2";
  // MapLibre names the wrapper "Map marker"; the card text (or the inner button's name) says more.
  marker.getElement().removeAttribute("aria-label");
  entranceMarkers.push(marker);
}

// Selected door: dark card on a stem whose tip (a dot in the route's color) is the entrance.
function entranceCard(name, sub, routeId) {
  return el(
    "div",
    { class: "dest-marker" },
    el(
      "div",
      { class: "dest-card" },
      accessSymbolSvg(),
      el("span", { class: "dest-text" }, el("span", { class: "dest-name" }, name), el("span", { class: "dest-sub" }, sub)),
    ),
    el("div", { class: "dest-stem", "aria-hidden": "true" }),
    el("div", { class: `dest-dot ${routeId ?? ""}`, "aria-hidden": "true" }),
  );
}

// The other route's door: a lighter card that switches to that route, by pointer or keyboard.
function entranceButton(route, which) {
  const button = el(
    "button",
    { type: "button", class: `dest-card alt ${route.id}`, "aria-label": `Accessible entrance for the ${which}. Switch to this route.` },
    accessSymbolSvg(22),
    el("span", { class: "dest-text" }, el("span", { class: "dest-name" }, "Accessible entrance"), el("span", { class: "dest-sub" }, `For the ${which}`)),
  );
  button.addEventListener("click", (e) => {
    e.stopPropagation(); // don't also set the start under the card
    selectRoute(route.id);
  });
  // Dot first: this card hangs below its door (see addEntranceMarker).
  return el(
    "div",
    { class: "dest-marker below" },
    el("div", { class: `dest-dot ${route.id}`, "aria-hidden": "true" }),
    el("div", { class: "dest-stem alt", "aria-hidden": "true" }),
    button,
  );
}

// Simplified International Symbol of Access (public domain), white on blue.
function accessSymbolSvg(size = 28) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  Object.entries({ class: "isa", viewBox: "0 0 24 24", width: size, height: size, "aria-hidden": "true" }).forEach(([k, v]) => svg.setAttribute(k, v));
  svg.innerHTML = `
    <rect width="24" height="24" rx="5" fill="#1f5fae"/>
    <g fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M11 8v5h4.5l2 4.5"/>
      <path d="M11 10.5h3.5"/>
      <path d="M8.6 11.2a4.6 4.6 0 1 0 6.4 5.4"/>
    </g>
    <circle cx="11" cy="5.4" r="1.7" fill="#fff"/>`;
  return svg;
}

// POST /api/route from a point to the current destination (shared by planning and rerouting).
// Throws with the backend's plain-language detail when no accessible route exists.
class ConnectionError extends Error {}

async function fetchRoutes(from, retry = true) {
  const body = {
    from: { lat: from.lat, lon: from.lon },
    to: { place_id: state.dest.id },
    profile: "manual_chair",
  };
  let res;
  let data;
  try {
    res = USE_MOCK
      ? await fetch("mock/route.json")
      : await fetch("/api/route", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    data = await res.json();
  } catch {
    // No answer, or not JSON (a dropped phone tunnel answers with an HTML error page): try once more.
    if (retry) {
      await new Promise((r) => setTimeout(r, 2000));
      return fetchRoutes(from, false);
    }
    throw new ConnectionError("Lost connection to the server. Check your signal and try again.");
  }
  if (!res.ok) throw new Error(data.detail || `route ${res.status}`);
  return data;
}

let requestSeq = 0;
async function requestRoute() {
  if (!state.start || !state.dest) return;
  const seq = ++requestSeq;
  showStatus("Finding accessible routes…");
  state.loading = true;
  state.routeError = false;
  summarize();
  let data;
  try {
    data = await fetchRoutes(state.start);
  } catch (err) {
    if (seq !== requestSeq) return;
    console.error(err);
    state.loading = false;
    state.routeError = true;
    clearRoutes();
    setSheetView("plan");
    if (sheetSnap === "peek") setSheet("half"); // the explanation is in the sheet
    // The backend's 404 detail is already plain language; a lost connection gets its own sentence.
    showStatus(err instanceof ConnectionError ? err.message : `${err.message} Try a different start point or destination.`, true);
    return;
  }
  if (seq !== requestSeq) return; // a newer request superseded this one

  state.loading = false;
  state.routes = mergeSameRoutes(data.routes);
  if (!state.routes.some((r) => r.id === state.selected)) state.selected = state.routes[0]?.id;
  document.getElementById("tradeoff").textContent = data.tradeoff_text || "";
  renderRoutes();
  setSheetView("routes");
  // Half height shows the route cards and leaves half the screen for the map. Set it before
  // fitting, so the fit leaves room for the sheet's new height.
  if (!wideScreen.matches) setSheet("half");
  fitToRoutes();
  const sel = state.routes.find((r) => r.id === state.selected);
  showStatus(sel.combined
    ? `Route found, ${fmtMinutes(sel.time_min)}. It is both the least effort and the fastest.`
    : `${state.routes.length} routes found. ${ROUTES[sel.id].label} selected, ${fmtMinutes(sel.time_min)}.`);
}

// ---------- Search fields ----------

// ARIA APG combobox: focus stays in the input, arrow keys move aria-activedescendant,
// Enter picks, Escape closes (or restores the last chosen text if already closed).
function createCombobox(name, getOptions, onSelect) {
  const input = document.getElementById(`${name}-input`);
  const list = document.getElementById(`${name}-list`);
  let options = [];
  let active = -1;
  let committed = "";

  function open() {
    const query = input.value.trim();
    options = getOptions(query);
    active = -1;
    const items = options.map((opt, i) => {
      const li = el(
        "li",
        { id: `${name}-opt-${i}`, role: "option", "aria-selected": "false" },
        el("span", { class: "opt-label" }, opt.label),
        ...(opt.detail ? [el("span", { class: "opt-detail" }, opt.detail)] : []),
      );
      li.addEventListener("mousedown", (e) => e.preventDefault()); // keep focus in the input
      li.addEventListener("click", () => choose(i));
      return li;
    });
    if (!items.length) {
      items.push(el("li", { class: "empty", role: "option", "aria-disabled": "true", "aria-selected": "false" },
        query ? "No matching buildings" : "Type a building name"));
    }
    list.replaceChildren(...items);
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    input.removeAttribute("aria-activedescendant");
    announceCount(query ? `${options.length} ${options.length === 1 ? "result" : "results"}` : "");
  }

  function close() {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function move(delta) {
    if (list.hidden) open();
    if (!options.length) return;
    active = (active + delta + options.length) % options.length;
    list.querySelectorAll("[role=option]").forEach((li, i) => li.setAttribute("aria-selected", String(i === active)));
    const li = document.getElementById(`${name}-opt-${active}`);
    input.setAttribute("aria-activedescendant", li.id);
    li.scrollIntoView({ block: "nearest" });
  }

  function choose(i) {
    const opt = options[i];
    close();
    if (!opt) return;
    onSelect(opt); // the handler sets the field text via setValue
    // Typing opened the sheet fully; once a place is picked, give the map back (e.g. to tap a start).
    if (!wideScreen.matches) {
      input.blur();
      setSheet("half");
    }
  }

  input.addEventListener("input", open);
  input.addEventListener("click", () => list.hidden && open());
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      move(e.key === "ArrowDown" ? 1 : -1);
    } else if (e.key === "Enter") {
      if (list.hidden) return;
      e.preventDefault();
      // Enter with nothing highlighted takes the top match, so "baker" + Enter just works.
      choose(active >= 0 ? active : input.value.trim() ? 0 : -1);
    } else if (e.key === "Escape") {
      if (!list.hidden) close();
      else input.value = committed;
    }
  });
  input.addEventListener("blur", () => {
    close();
    input.value = committed; // typed text that was never chosen doesn't stick
  });

  return {
    setValue(text) {
      committed = text;
      input.value = text;
    },
    getValue: () => committed,
  };
}

let countTimer = 0;
function announceCount(text) {
  // Debounced so screen readers hear the count once typing pauses, not on every key.
  clearTimeout(countTimer);
  countTimer = setTimeout(() => (document.getElementById("combo-count").textContent = text), 500);
}

function normalize(s) {
  return s.toLowerCase().replace(/['’.]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
}

// Name prefix beats word prefix beats substring beats alias match; street-address aliases come last.
function searchPlaces(query, filter) {
  const q = normalize(query);
  if (!q) return [];
  const hits = [];
  for (const p of state.places) {
    if (!filter(p)) continue;
    const name = normalize(p.name);
    let score = -1;
    let via = null;
    if (name.startsWith(q)) score = 0;
    else if (name.split(" ").some((w) => w.startsWith(q))) score = 1;
    else if (name.includes(q)) score = 2;
    else {
      const matches = (p.aliases ?? []).filter((a) => normalize(a).includes(q));
      const isAddress = (a) => /^\d/.test(a);
      via = matches.find((a) => !isAddress(a)) ?? matches[0];
      if (via) score = isAddress(via) ? 4 : 3;
    }
    if (score >= 0) hits.push({ score, p, via });
  }
  hits.sort((a, b) => a.score - b.score || a.p.name.localeCompare(b.p.name));
  return hits.slice(0, 8).map(({ p, via }) => ({
    id: p.id,
    label: p.name,
    detail: via ? `Matches “${via}”` : p.kind === "bus_stop" ? "Bus stop" : null,
    place: p,
  }));
}

function placePoint(p) {
  return { lat: p.lat, lon: p.lon };
}

// ---------- Use my location ----------

const CAMPUS_CENTER = [-72.2887, 43.704];
const CAMPUS_RADIUS_M = 2500;

function useMyLocation() {
  const previous = startCombo.getValue();
  if (!("geolocation" in navigator)) {
    startCombo.setValue(previous);
    showStatus("This browser cannot share your location. Pick a building or tap the map instead.", true);
    return;
  }
  startCombo.setValue("Finding your location…");
  showStatus("Finding your location…");
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const point = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      const away = haversine([point.lon, point.lat], CAMPUS_CENTER);
      if (away > CAMPUS_RADIUS_M) {
        startCombo.setValue(previous);
        showStatus(`Your location is ${fmtDistance(away)} from campus. Pick a building or tap the map to start on campus.`, true);
        return;
      }
      setStart(point, "My location");
    },
    (err) => {
      startCombo.setValue(previous);
      const why = err.code === err.PERMISSION_DENIED ? "Location access is off for this site." : "Could not find your location.";
      showStatus(`${why} Pick a building or tap the map instead.`, true);
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
  );
}

// When least effort and fastest are the same path, show one route instead of two identical cards.
// One route when the "fastest" isn't really faster: if it doesn't save at least a minute, the
// least-effort route is also the fastest worth taking (user feedback, Oct 31).
function mergeSameRoutes(routes) {
  const [a, b] = routes;
  if (!a || !b) return routes;
  const ca = a.geojson.coordinates;
  const cb = b.geojson.coordinates;
  const identical = ca.length === cb.length && ca.every((c, i) => c[0] === cb[i][0] && c[1] === cb[i][1]);
  const least = routes.find((r) => r.id === "least_effort");
  const fast = routes.find((r) => r.id === "fastest");
  const savesAMinute = least && fast && Math.round(least.time_min) - Math.round(fast.time_min) >= 1;
  const same = identical || !savesAMinute;
  if (!same) return routes;
  const keep = routes.find((r) => r.id === "least_effort") ?? a;
  return [{ ...keep, combined: true }];
}

function routeLabel(r) {
  return r.combined ? "Least effort and fastest" : ROUTES[r.id].label;
}

// ---------- Rendering ----------

function selectRoute(id) {
  if (id === state.selected) return;
  state.selected = id;
  renderRoutes();
  const r = state.routes.find((x) => x.id === id);
  showStatus(`${ROUTES[id].label} selected, ${fmtMinutes(r.time_min)}.`);
}

function renderRoutes() {
  const selected = state.routes.find((r) => r.id === state.selected);
  // The sheet (cards, summary) never waits for the map; map drawing happens once it is ready.
  renderCards();
  document.getElementById("results").hidden = state.routes.length === 0;
  summarize();
  updateChrome();
  window.renderBarrierPins?.(); // pins on the selected route get a ring (reports.js)
  if (!mapReady) return;

  // While navigating, only the route being followed is drawn.
  const shown = nav.active ? state.routes.filter((r) => r.id === state.selected) : state.routes;
  map.getSource("routes").setData({ type: "FeatureCollection", features: shown.flatMap(routeFeatures) });

  // The selected route draws over the alternative; progress, patterns and the link go on top.
  for (const part of ["casing", "line"]) map.moveLayer(`route-${part}-${state.selected}`);
  for (const id of ["route-steep-pattern", "route-gravel", "route-traveled", `route-hit-${state.selected}`, "connector-line", "route-hazard-signs"]) {
    if (map.getLayer(id)) map.moveLayer(id);
  }

  renderEntrances();
  renderPills(selected);
  setRouteContext(shown, state.selected); // the route's hazards and nearby ones, at every zoom
  updateConnector();
  map.getSource("traveled").setData(
    nav.active && selected ? { type: "Feature", geometry: selected.geojson, properties: {} } : { type: "FeatureCollection", features: [] },
  );
}

// Joins back-to-back steep or very steep segments into one stretch, so a long hill gets one tag
// instead of a pile of overlapping ones. Gentle and moderate slopes get no tag.
function steepStretches(segments) {
  const stretches = [];
  let current = null;
  for (const s of segments) {
    const word = s.steepness ?? steepnessOf(s.grade_pct);
    if (!isSteep(word)) {
      current = null;
      continue;
    }
    if (current) {
      current.coordinates.push(...s.coordinates.slice(1));
      if (word === "very steep") current.word = word;
    } else {
      current = { coordinates: [...s.coordinates], word };
      stretches.push(current);
    }
  }
  return stretches;
}

// Places each route's pill on its own line, as far as possible from the other pill, so two routes
// that run close together don't stack their pills on top of each other.
function spreadPills(routes) {
  const fractions = [0.25, 0.35, 0.45, 0.55, 0.65, 0.75];
  const candidates = routes.map((r) => fractions.map((f) => pointAlong(r.geojson.coordinates, f)));
  if (routes.length < 2) return candidates.map((c) => c[2]);
  let best = [candidates[0][2], candidates[1][3]];
  let bestDistance = -1;
  for (const a of candidates[0]) {
    for (const b of candidates[1]) {
      const d = haversine(a, b);
      if (d > bestDistance) [best, bestDistance] = [[a, b], d];
    }
  }
  return best;
}

// Hazard words, icons and colors shared by the map pills, the cards and navigation.
const HAZARD_TEXT = {
  very_steep: "Very steep hill", steep: "Steep hill", gravel: "Gravel", brick: "Brick",
  unverified: "Curb ramps not confirmed", barrier: "Barrier on route",
};
function hazardIcon(kind, size = 20) {
  const name = { very_steep: "hz-very_steep", steep: "hz-steep", gravel: "hz-gravel", brick: "hz-brick", stairs: "hz-stairs" }[kind];
  if (!name) {
    // Barrier and unconfirmed curb ramps: a plain warning triangle.
    const color = kind === "barrier" ? "#d0021b" : "#5b6670";
    return iconSvg(size, `<path d="M12 2.8 1.8 20.5h20.4z" fill="${color}" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/><path d="M12 9v5.5" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/><circle cx="12" cy="17.4" r="1.3" fill="#fff"/>`);
  }
  return el("img", { src: "data:image/svg+xml;charset=utf-8," + encodeURIComponent(iconSvgs()[name]), width: size, height: size, alt: "" });
}
// Route hazards from the backend (v5), or derived from segments for older fixtures.
function routeHazards(r) {
  if (r?.hazards) return r.hazards.filter((h) => h.kind !== "unverified");
  return steepStretches(r?.segments ?? []).map((s) => {
    const [lon, lat] = pointAlong(s.coordinates, 0.5);
    return { kind: CLS[s.word], label: s.word === "very steep" ? "Very steep hill" : "Steep hill", length_m: lineLength(s.coordinates), lon, lat };
  });
}

function renderPills(selected) {
  for (const m of pillMarkers) m.remove();
  pillMarkers = [];
  const pillSpots = spreadPills(state.routes);
  state.routes.forEach((r, i) => {
    if (nav.active) return; // no route choice while navigating; hazard pills below still show
    const isSelected = r.id === state.selected;
    const pill = el(
      "button",
      { type: "button", class: `route-pill ${r.id}`, "aria-pressed": String(isSelected) },
      `${routeLabel(r)}, ${fmtMinutes(r.time_min)}`,
    );
    pill.addEventListener("click", (e) => {
      e.stopPropagation(); // don't also set the start under the pill
      selectRoute(r.id);
    });
    pillMarkers.push(new maplibregl.Marker({ element: pill }).setLngLat(pillSpots[i]).addTo(map));
    // MapLibre labels every marker element "Map marker"; give the button its own name back.
    pill.setAttribute("aria-label", `${routeLabel(r)}, ${fmtMinutes(r.time_min)}`);
  });
  // The route's hazards are drawn by the route-hazard-signs layer (maplayers.js): small, labeled,
  // and they give way to each other instead of covering the map.
}

// Route cards: a swipeable row, minimal on purpose (user feedback, Oct 31). Name and time, distance
// and feet uphill, and one line that says whether the route has hills or gravel. The map's colors
// and hazard signs carry the rest; the briefing and exact numbers are below, revealed by dragging up.
function renderCards() {
  const cards = document.getElementById("cards");
  cards.replaceChildren();
  document.getElementById("route-details").replaceChildren();
  for (const r of state.routes) {
    const selected = r.id === state.selected;
    const radio = el("input", { type: "radio", name: "route", value: r.id });
    radio.checked = selected;
    radio.addEventListener("change", () => selectRoute(r.id));
    const steepness = r.steepness ?? steepnessOf(r.max_grade_pct);
    const climb = r.climb_m ?? totalClimb(r.elevation_profile);

    // Exact numbers stay available, but only in a collapsed view (plain-language rule).
    const surfaces = [...new Set((r.segments ?? []).map((s) => s.material).filter(Boolean))];
    const details = el(
      "details",
      { class: "route-details" },
      el("summary", {}, `Details for ${routeLabel(r).toLowerCase()}`),
      el(
        "ul",
        {},
        el("li", {}, `Distance: ${fmtDistance(r.distance_m)}`),
        el("li", {}, `Total climb: ${fmtFeet(climb)}`),
        el("li", {}, `Steepest stretch: ${r.max_grade_pct.toFixed(1)}% grade (${steepness})`),
        ...(surfaces.length ? [el("li", {}, `Surfaces: ${surfaces.join(", ").toLowerCase()}`)] : []),
      ),
    );
    if (selected) {
      const notes = (r.warnings ?? []).map((w) => el("p", { class: "route-note" }, w));
      document.getElementById("route-details").replaceChildren(briefingLine(r), ...notes, details);
    }

    const card = el(
      "label",
      { class: `route-card ${r.id}` },
      radio,
      el("span", { class: "rc-top" }, el("span", { class: "route-name" }, routeLabel(r)), el("span", { class: "route-time" }, fmtMinutes(r.time_min))),
      el("span", { class: "rc-meta" }, `${fmtDistance(r.distance_m)} · ${fmtFeet(climb)} uphill`),
      hazardLine(r),
    );
    card.addEventListener("click", () => {
      // Tapping the card already showing scrolls it fully into view; tapping another selects it.
      card.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", inline: "nearest", block: "nearest" });
    });
    cards.append(card);
  }
  // Keep the selected route's card in view in the swipeable row.
  const chosen = cards.querySelector(`.route-card.${state.selected}`);
  if (chosen) cards.scrollLeft = Math.max(0, chosen.offsetLeft - 16);
}

// MobiliD: Dartmouth's door-to-door accessible shuttle for approved riders (no stops, no public API).
// Hours from dartmouth.edu/transportation: Monday to Friday, 7 a.m. to 7 p.m. Booking is in the
// MobiliD app, so this card only says whether it's running now and how to book.
function mobilidStatus(now = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", hour: "numeric", hour12: false })
    .formatToParts(now).map((p) => [p.type, p.value]));
  const weekday = !["Sat", "Sun"].includes(parts.weekday);
  const hour = Number(parts.hour) % 24;
  const open = weekday && hour >= 7 && hour < 19;
  if (open) return { open, text: "Running now, until 7 p.m." };
  const nextDay = parts.weekday === "Fri" && hour >= 19 || parts.weekday === "Sat" || parts.weekday === "Sun" ? "Monday" : hour < 7 ? "today" : "tomorrow";
  return { open, text: `Closed. Next rides ${nextDay} 7 a.m.` };
}
function mobilidCard() {
  const st = mobilidStatus();
  return el(
    "div",
    { class: `route-card mobilid${st.open ? " open" : ""}` },
    el("span", { class: "rc-top" }, el("span", { class: "route-name" }, "MobiliD shuttle"), el("span", { class: `mob-dot${st.open ? " open" : ""}`, "aria-hidden": "true" })),
    el("span", { class: `rc-hazards ${st.open ? "clear" : "closed"}` }, st.text),
    el("span", { class: "rc-meta" }, "Door-to-door, approved riders. ", el("a", { class: "mob-link", href: "tel:+16036463154" }, "Call to book")),
  );
}

// One line: the hazards on this route in words (worst first), or a calm all-clear.
function hazardLine(r) {
  const order = ["barrier", "very_steep", "steep", "gravel", "brick"];
  const kinds = [...new Set(routeHazards(r).map((h) => h.kind))].filter((k) => order.includes(k));
  kinds.sort((a, b) => order.indexOf(a) - order.indexOf(b));
  if (!kinds.length) {
    return el("span", { class: "rc-hazards clear" },
      iconSvg(18, '<circle cx="12" cy="12" r="10.5" fill="#1f9d55"/><path d="M7 12.5l3.2 3.2L17 9" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'),
      "No steep hills or gravel");
  }
  return el("span", { class: `rc-hazards ${kinds[0]}` }, hazardIcon(kinds[0], 18), kinds.map((k) => HAZARD_TEXT[k]).join(" · "));
}

// ---------- Route briefing ----------
// Claude's (or the backend's template) one- or two-sentence description of the selected route, from
// POST /api/explain. A quiet loading line, hidden if it fails: routing never waits on it.

const briefings = new Map(); // route key -> { text } | { pending } | { failed }

function briefingKey(route) {
  const c = route.geojson.coordinates;
  return `${route.id}:${Math.round(route.distance_m)}:${c.length}:${c[0]}:${c[c.length - 1]}`;
}

function briefingLine(route) {
  const key = briefingKey(route);
  if (!briefings.has(key)) requestBriefing(route, key);
  const b = briefings.get(key);
  const line = el("p", { class: "route-briefing", id: "route-briefing", "data-key": key });
  if (b.text) line.textContent = b.text;
  else if (b.pending) {
    line.textContent = "Getting a short description…";
    line.classList.add("loading");
  } else line.hidden = true;
  return line;
}

async function requestBriefing(route, key) {
  briefings.set(key, { pending: true });
  // Send the route objects as the backend returned them (without our own cached extras).
  const clean = (r) => {
    if (!r) return null;
    const { _cum, combined, ...rest } = r;
    return rest;
  };
  const alternative = state.routes.find((r) => r.id !== route.id) ?? null;
  try {
    if (USE_MOCK) throw new Error("no briefing for mock routes");
    const res = await fetch("/api/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ route: clean(route), alternative: clean(alternative) }),
    });
    if (!res.ok) throw new Error(`explain ${res.status}`);
    briefings.set(key, { text: (await res.json()).text });
  } catch {
    briefings.set(key, { failed: true });
  }
  // Update just the line (re-rendering the cards would move keyboard focus).
  const line = document.getElementById("route-briefing");
  if (line?.dataset.key !== key) return;
  const b = briefings.get(key);
  line.classList.remove("loading");
  line.textContent = b.text ?? "";
  line.hidden = !b.text;
  // The card grew; let a content-sized sheet grow with it.
  if (!wideScreen.matches && sheet.dataset.view === "routes" && sheetSnap === "half") setSheet("half");
}

function chip(icon, text) {
  return el("span", { class: "chip" }, icon, text);
}

// Small line icons for the chips and the selected check (decorative: the text says it all).
function iconSvg(size, inner) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  Object.entries({ viewBox: "0 0 24 24", width: size, height: size, "aria-hidden": "true" }).forEach(([k, v]) => svg.setAttribute(k, v));
  svg.innerHTML = inner;
  return svg;
}
const iconUphill = () => iconSvg(16, '<path d="M3 19 L21 7 L21 19 Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>');
const iconClock = () => iconSvg(16, '<circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 7.5V12l3 2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>');
const iconFeather = () => iconSvg(16, '<path d="M5 19 C9 15 13 11 19 5 M8 16h6 M11 13h5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>');
function iconCheck(color) {
  const svg = iconSvg(24, `<circle cx="12" cy="12" r="11" fill="${color}"/><path d="M7 12.5l3.2 3.2L17 9" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>`);
  svg.setAttribute("class", "route-check");
  return svg;
}

function clearRoutes() {
  state.routes = [];
  renderRoutes();
}

function fitToRoutes() {
  const coords = state.routes.flatMap((r) => r.geojson.coordinates);
  if (state.start) coords.push([state.start.lon, state.start.lat]);
  if (state.dest) coords.push([state.dest.lon, state.dest.lat]);
  if (!coords.length) return;
  const bounds = coords.reduce((b, c) => b.extend(c), new maplibregl.LngLatBounds(coords[0], coords[0]));
  map.fitBounds(bounds, {
    padding: mapPadding(), // clear of the sheet, with room for the entrance cards above and below doors
    maxZoom: 18,
    duration: reduceMotion ? 0 : 700,
    bearing: map.getBearing(), // fitBounds would otherwise reset the rotation to north
  });
}

// ---------- Link from the dot to the path ----------

const CONNECT_MIN_M = 5; // closer than this, the dot is on the path (GPS noise)

// A lighter streak in the route's color from a dot that is off the path (on a lawn, in a building,
// in a parking lot) to where the route picks it up: before starting, from the start to the route's
// first point (where the backend snapped it); while navigating, from where you are (`from`, the
// drawn dot) to the nearest point of the route.
function updateConnector(from = null) {
  const route = selectedRoute();
  let to = null;
  if (route && nav.active && !nav.arrived) {
    from ??= nav.disp?.lngLat ?? nav.fix;
    if (from) to = projectOnRoute(route, from).point;
  } else if (route && !nav.active && state.start) {
    from = [state.start.lon, state.start.lat];
    to = route.geojson.coordinates[0];
  }
  const show = Boolean(from && to) && haversine(from, to) >= CONNECT_MIN_M;
  if (!mapReady) return;
  if (route) map.setPaintProperty("connector-line", "line-color", ROUTES[route.id].color);
  map.getSource("connector")?.setData({
    type: "FeatureCollection",
    features: show ? [{ type: "Feature", geometry: { type: "LineString", coordinates: [from, to] }, properties: {} }] : [],
  });
}

// ---------- Live navigation ----------
// Start follows your location along the selected route: the dot snaps onto the route when you are
// on it, the part behind you turns gray, the sheet counts down time and distance and warns about
// steep stretches ahead. Off the route for two fixes in a row, it asks the backend for a new route
// from where you are. Your location stays on the phone except for those route requests.

const SNAP_M = 20; // within this of the route, the dot sits on the route
const OFF_ROUTE_M = 25; // farther than this for two fixes in a row means off route
const ARRIVE_M = 15;
const REROUTE_GAP_MS = 8000;
const DEMO = new URLSearchParams(location.search).has("demo");

const nav = {
  active: false,
  arrived: false,
  follow: true,
  fix: null, // last location [lon, lat]
  progress: 0, // meters traveled along the selected route
  offCount: 0,
  allowance: null, // how far off the route you were when it started (e.g. starting on a lawn)
  rerouting: false,
  lastReroute: 0,
  watchId: null,
  simTimer: null,
  lostTimer: null,
  target: null, // where the latest fix says you are (see setTarget)
  disp: null, // where the dot is drawn this frame
  pad: null, // camera padding, eased toward navPadding() each frame
  raf: null,
  lastFrame: 0,
  touching: false, // a finger or mouse is down on the map: let the gesture win
  userZooming: false,
  zoomRamp: false, // zooming in to street level once at the start
  summary: "",
};
let youMarker = null;

function selectedRoute() {
  return state.routes.find((r) => r.id === state.selected) ?? null;
}

// Cumulative meters along a route's line, cached on the route object.
function routeGeometry(route) {
  if (!route._cum) {
    const c = route.geojson.coordinates;
    route._cum = [0];
    for (let i = 1; i < c.length; i++) route._cum.push(route._cum[i - 1] + haversine(c[i - 1], c[i]));
  }
  return { coords: route.geojson.coordinates, cum: route._cum, length: route._cum[route._cum.length - 1] };
}

// Nearest point of the route to a location: the point, its distance along the route and how far
// off the route the location is. fromM/toM limit the search to a window around current progress,
// so a route that doubles back on itself doesn't make the dot jump to the wrong pass.
function projectOnRoute(route, lngLat, fromM = -Infinity, toM = Infinity) {
  const { coords, cum } = routeGeometry(route);
  const kx = 111320 * Math.cos((lngLat[1] * Math.PI) / 180); // meters per degree, local flat approximation
  const ky = 110540;
  let best = null;
  for (let i = 1; i < coords.length; i++) {
    if (cum[i] < fromM || cum[i - 1] > toM) continue;
    const ax = (coords[i - 1][0] - lngLat[0]) * kx;
    const ay = (coords[i - 1][1] - lngLat[1]) * ky;
    const dx = (coords[i][0] - coords[i - 1][0]) * kx;
    const dy = (coords[i][1] - coords[i - 1][1]) * ky;
    const len2 = dx * dx + dy * dy;
    const t = len2 ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len2)) : 0;
    const offset = Math.hypot(ax + t * dx, ay + t * dy);
    if (!best || offset < best.offset) {
      best = {
        offset,
        along: cum[i - 1] + t * (cum[i] - cum[i - 1]),
        point: [coords[i - 1][0] + t * (coords[i][0] - coords[i - 1][0]), coords[i - 1][1] + t * (coords[i][1] - coords[i - 1][1])],
      };
    }
  }
  return best;
}

function pointAtMeters(route, meters) {
  const { coords, cum, length } = routeGeometry(route);
  const m = Math.max(0, Math.min(length, meters));
  for (let i = 1; i < coords.length; i++) {
    if (cum[i] >= m) {
      const t = cum[i] > cum[i - 1] ? (m - cum[i - 1]) / (cum[i] - cum[i - 1]) : 0;
      return [coords[i - 1][0] + t * (coords[i][0] - coords[i - 1][0]), coords[i - 1][1] + t * (coords[i][1] - coords[i - 1][1])];
    }
  }
  return coords[coords.length - 1];
}

function startNavigation({ simulate = false, wrongTurn = false } = {}) {
  const route = selectedRoute();
  if (!route || nav.active) return;
  Object.assign(nav, { active: true, arrived: false, follow: true, fix: null, progress: 0, offCount: 0, allowance: null, rerouting: false, summary: "Finding your location…" });
  sheet.classList.add("navigating");
  setSheetView("nav");
  document.getElementById("nav-panel").hidden = false;
  document.getElementById("nav-end").hidden = false;
  document.getElementById("nav-end").textContent = "End";
  startMarker.remove();
  youMarker ??= new maplibregl.Marker({ element: el("div", { class: "you-dot" }) });
  youMarker.getElement().setAttribute("aria-label", "Your location");
  youMarker.getElement().style.zIndex = "4"; // above the entrance cards, so you never disappear under one
  stopFrames();
  renderRoutes();
  if (!wideScreen.matches) setSheet("peek");
  nav.pad = navPadding();
  nav.zoomRamp = true;
  showStatus(`Navigation started. Follow the ${routeLabel(route).toLowerCase()} route.`);

  if (simulate) {
    startSimulation(wrongTurn);
  } else if (!("geolocation" in navigator)) {
    endNavigation("This browser cannot share your location, which navigation needs.");
  } else {
    nav.watchId = navigator.geolocation.watchPosition(
      (pos) => onFix([pos.coords.longitude, pos.coords.latitude], pos.coords.accuracy),
      (err) => {
        if (err.code === err.PERMISSION_DENIED) endNavigation("Navigation needs your location. Location access is off for this site.");
        // Brief "unavailable" blips are normal; only speak up if no fix follows for a while.
        else if (err.code === err.POSITION_UNAVAILABLE && !nav.lostTimer) {
          nav.lostTimer = setTimeout(() => showStatus("Lost your location for a moment. Keep going; it will catch up.", true), 10000);
        }
      },
      // No timeout: a phone sitting still sends no new fixes, and a pause is not lost location.
      { enableHighAccuracy: true, maximumAge: 2000 },
    );
  }
}

function onFix(lngLat, accuracy = 10) {
  if (!nav.active || nav.arrived) return;
  clearTimeout(nav.lostTimer);
  nav.lostTimer = null;
  const route = selectedRoute();
  if (!route) return;
  nav.fix = lngLat;
  // Look near current progress first; if that's far, check the whole route (you may have skipped ahead).
  let hit = projectOnRoute(route, lngLat, nav.progress - 30, nav.progress + 250);
  if (!hit || hit.offset > SNAP_M) {
    const anywhere = projectOnRoute(route, lngLat);
    if (!hit || anywhere.offset < hit.offset) hit = anywhere;
  }
  const onRoute = hit.offset <= Math.max(SNAP_M, accuracy);
  // Starting off the path (on a lawn, in a parking lot) is not "off route": only moving clearly
  // farther away than where the route began counts, until you reach the route.
  if (nav.allowance === null) nav.allowance = onRoute ? 0 : hit.offset;
  const offLimit = Math.max(OFF_ROUTE_M, nav.allowance + 15);
  if (onRoute) {
    nav.progress = hit.along;
    nav.offCount = 0;
    nav.allowance = 0;
  } else if (hit.offset > offLimit) {
    nav.offCount++;
  }
  setTarget(route, lngLat);

  const { length } = routeGeometry(route);
  const end = route.geojson.coordinates[route.geojson.coordinates.length - 1];
  if (length - nav.progress < ARRIVE_M || haversine(lngLat, end) < ARRIVE_M) {
    arrive(route);
    return;
  }
  updateNavPanel(route, onRoute);
  if (nav.offCount >= 2 && hit.offset > offLimit) reroute(lngLat);
}

// ---------- Smooth movement between fixes ----------
// A phone reports its location about once a second. Drawing only on fixes makes the dot hop and the
// camera lurch, so fixes only set a target; every animation frame the dot glides toward it. On the
// route it glides along the line itself (following curves, not cutting corners) and predicts ahead
// at your recent speed for up to 1.5 s, the way Google Maps does, so it doesn't stall waiting for
// the next fix. Off the route it glides straight toward the reported position. The camera is moved
// in the same frame, so map and dot move together.

const GLIDE_MS = 180; // how quickly the dot closes the gap to its target
const MAX_SPEED = 4; // m/s; faster than this between fixes is GPS noise, not travel
const PREDICT_MAX_S = 1.5;
const FOLLOW_ZOOM = 17.5;

function setTarget(route, lngLat) {
  const now = performance.now();
  const prev = nav.target;
  let vel = [0, 0]; // degrees per second, smoothed
  if (prev && now > prev.time) {
    const dt = (now - prev.time) / 1000;
    const mps = haversine(prev.lngLat, lngLat) / dt;
    const scale = mps > MAX_SPEED ? MAX_SPEED / mps : 1; // a GPS jump is not a speed
    for (const i of [0, 1]) vel[i] = 0.6 * prev.vel[i] + 0.4 * ((lngLat[i] - prev.lngLat[i]) / dt) * scale;
  }
  nav.target = { lngLat: [...lngLat], vel, time: now };
  nav.disp ??= { lngLat: [...lngLat], along: nav.progress }; // first fix: start right there
  if (!nav.raf) {
    nav.lastFrame = performance.now();
    nav.raf = requestAnimationFrame(frame);
  }
}

function frame(now) {
  nav.raf = requestAnimationFrame(frame);
  const route = selectedRoute();
  if (!route || !nav.target || !nav.disp) return;
  const dt = Math.min(100, now - nav.lastFrame);
  nav.lastFrame = now;
  const k = 1 - Math.exp(-dt / GLIDE_MS);

  // The dot is where you are: glide toward the latest fix plus a short prediction along your
  // direction of travel, never pulled onto the route.
  const t = Math.min(PREDICT_MAX_S, (now - nav.target.time) / 1000);
  for (const i of [0, 1]) nav.disp.lngLat[i] += (nav.target.lngLat[i] + nav.target.vel[i] * t - nav.disp.lngLat[i]) * k;
  const pos = nav.disp.lngLat;

  // Gray progress follows the nearest point of the route to you (only while you are on it).
  const { length } = routeGeometry(route);
  const near = projectOnRoute(route, pos, nav.progress - 40, nav.progress + 80);
  const along = near && near.offset <= SNAP_M ? near.along : nav.progress;
  nav.disp.along += (along - nav.disp.along) * k;
  map.setPaintProperty("route-traveled", "line-gradient", traveledGradient(nav.disp.along / length));
  updateConnector(pos);

  youMarker.setLngLat(pos);
  if (!youMarker.getElement().isConnected) youMarker.addTo(map);
  // Moving the camera from code cancels a gesture in progress, so stand back while you touch or zoom.
  if (nav.follow && !nav.touching && !nav.userZooming) {
    // Padding glides with the sheet too, so dragging the sheet mid-trip doesn't jolt the map.
    const want = navPadding();
    nav.pad ??= want;
    for (const side of ["top", "bottom", "left", "right"]) nav.pad[side] += (want[side] - nav.pad[side]) * k;
    let zoom = map.getZoom();
    if (nav.zoomRamp) {
      zoom += (FOLLOW_ZOOM - zoom) * k;
      if (Math.abs(FOLLOW_ZOOM - zoom) < 0.01) nav.zoomRamp = false; // after that, your zoom is yours
    }
    map.jumpTo({ center: pos, zoom, padding: nav.pad });
  }
}

function stopFrames() {
  cancelAnimationFrame(nav.raf);
  nav.raf = null;
  nav.target = null;
  nav.disp = null;
}

// Gray up to `fraction` of the route's length, transparent after.
function traveledGradient(fraction) {
  const f = Math.min(0.999999, Math.max(0.000001, fraction));
  return ["step", ["line-progress"], "#9c968a", f, "rgba(156, 150, 138, 0)"];
}

// While navigating, the map's center is the middle of the part the sheet doesn't cover, so
// following keeps you there without per-frame offsets.
function navPadding() {
  if (!nav.active) return { top: 0, bottom: 0, left: 0, right: 0 };
  if (wideScreen.matches) return { top: 0, bottom: 0, left: sheet.getBoundingClientRect().right, right: 0 };
  return { top: 0, bottom: sheetVisibleHeight(), left: 0, right: 0 };
}

function updateNavPanel(route, onRoute) {
  const { length } = routeGeometry(route);
  const remaining = Math.max(0, length - nav.progress);
  const minutes = route.distance_m > 0 ? (remaining / route.distance_m) * route.time_min : 0;
  const left = `${fmtMinutes(minutes)} left, ${fmtDistance(remaining)}`;

  // Next hazard (hill, gravel, brick, barrier): where it starts along the route, relative to you.
  let ahead = "";
  const NOW = { very_steep: "Very steep hill now. Take it slowly.", steep: "Steep hill now. Take it slowly.",
    gravel: "Gravel now. Loose surface.", brick: "Brick now. Bumpy surface.", barrier: "Reported barrier here." };
  for (const h of routeHazards(route).filter((h) => NOW[h.kind])) {
    const mid = projectOnRoute(route, [h.lon, h.lat]).along;
    const half = (h.length_m || 0) / 2;
    if (nav.progress >= mid - half && nav.progress <= mid + half) {
      ahead = NOW[h.kind];
      break;
    }
    if (mid - half > nav.progress && mid - half - nav.progress < 250) {
      ahead = `${HAZARD_TEXT[h.kind]} in ${fmtFeet(mid - half - nav.progress)}.`;
      break;
    }
  }
  const status = nav.rerouting ? "Off the route. Finding a new way from here…" : !onRoute ? "Head to the route; the lighter line shows the way." : "";
  document.getElementById("nav-remaining").textContent = left;
  document.getElementById("nav-ahead").textContent = status || ahead;
  document.getElementById("nav-ahead").classList.toggle("warn", Boolean(status || ahead));
  document.getElementById("nav-route").textContent = `Following the ${routeLabel(route).toLowerCase()} route to ${state.dest.name}.`;
  nav.summary = nav.rerouting ? `${left}. Finding a new way.` : !onRoute ? `${left}. Head to the route.` : ahead ? `${left}. ${ahead}` : left;
  summarize();
}

async function reroute(lngLat) {
  if (nav.rerouting || Date.now() - nav.lastReroute < REROUTE_GAP_MS) return;
  nav.rerouting = true;
  nav.lastReroute = Date.now();
  showStatus("Off the route. Finding a new way from here…");
  updateNavPanel(selectedRoute(), false);
  try {
    const data = await fetchRoutes({ lat: lngLat[1], lon: lngLat[0] });
    if (!nav.active) return;
    const keep = state.selected;
    state.routes = mergeSameRoutes(data.routes);
    if (nav.disp) nav.disp.along = 0; // progress starts over on the new route; the dot stays put
    if (!state.routes.some((r) => r.id === keep)) state.selected = state.routes[0]?.id;
    document.getElementById("tradeoff").textContent = data.tradeoff_text || "";
    Object.assign(nav, { progress: 0, offCount: 0, allowance: null });
    renderRoutes();
    showStatus(`New route found from where you are. Follow the ${routeLabel(selectedRoute()).toLowerCase()} route.`);
  } catch (err) {
    console.error(err);
    showStatus(err instanceof ConnectionError
      ? `${err.message} Meanwhile, head back to the route; the lighter line shows the way.`
      : "Could not find a new route from here. Head back to the route; the lighter line shows the way.", true);
  } finally {
    nav.rerouting = false;
    if (nav.active && nav.fix) onFix(nav.fix);
  }
}

function arrive(route) {
  nav.arrived = true;
  stopTracking();
  nav.progress = routeGeometry(route).length;
  setTarget(route, route.geojson.coordinates[route.geojson.coordinates.length - 1]);
  nav.target.vel = [0, 0];
  const text = `You've arrived at the accessible entrance of ${state.dest.name}.`;
  document.getElementById("nav-remaining").textContent = "You've arrived";
  document.getElementById("nav-ahead").textContent = text;
  document.getElementById("nav-ahead").classList.remove("warn");
  document.getElementById("nav-end").textContent = "Done";
  nav.summary = "You've arrived";
  summarize();
  showStatus(text);
}

function stopTracking() {
  if (nav.watchId !== null) navigator.geolocation.clearWatch(nav.watchId);
  clearInterval(nav.simTimer);
  clearTimeout(nav.lostTimer);
  nav.lostTimer = null;
  nav.watchId = null;
  nav.simTimer = null;
}

function endNavigation(message) {
  stopTracking();
  stopFrames();
  Object.assign(nav, { active: false, arrived: false, fix: null, progress: 0 });
  youMarker?.remove();
  sheet.classList.remove("navigating");
  setSheetView(state.routes.length ? "routes" : "plan");
  document.getElementById("nav-panel").hidden = true;
  document.getElementById("nav-end").hidden = true;
  document.getElementById("recenter").hidden = true;
  map.setPaintProperty("route-traveled", "line-gradient", traveledGradient(0));
  map.setPadding({ top: 0, bottom: 0, left: 0, right: 0 }); // following set it; fitBounds adds its own
  if (state.start) startMarker.setLngLat([state.start.lon, state.start.lat]).addTo(map);
  renderRoutes();
  fitToRoutes();
  if (!wideScreen.matches) setSheet("half");
  showStatus(message ?? "Navigation ended.", Boolean(message));
}

// Panning the map yourself pauses following; Re-center brings it back. Zooming yourself keeps
// following, at your zoom.
map.getCanvasContainer().addEventListener("pointerdown", () => (nav.touching = true));
addEventListener("pointerup", () => (nav.touching = false));
addEventListener("pointercancel", () => (nav.touching = false));
map.on("zoomstart", (e) => {
  if (e.originalEvent) Object.assign(nav, { userZooming: true, zoomRamp: false });
});
map.on("zoomend", () => (nav.userZooming = false));
map.on("dragstart", (e) => {
  if (!nav.active || !e.originalEvent) return;
  nav.follow = false;
  document.getElementById("recenter").hidden = false;
});
document.getElementById("recenter").addEventListener("click", () => {
  nav.follow = true; // the next frame glides the camera back to you
  document.getElementById("recenter").hidden = true;
});
document.getElementById("nav-start").addEventListener("click", () => startNavigation());
document.getElementById("nav-end").addEventListener("click", () => endNavigation());

// Demo only (?demo=1): a laptop can't wheel across campus, so these replay a trip along the route
// at about 8x speed, optionally with a wrong turn partway to show rerouting. Clearly labeled.
document.getElementById("demo-sim").hidden = !DEMO;
document.getElementById("sim-trip").addEventListener("click", () => startNavigation({ simulate: true }));
document.getElementById("sim-wrong").addEventListener("click", () => startNavigation({ simulate: true, wrongTurn: true }));

function startSimulation(wrongTurn) {
  const STEP_M = 10; // per 1 s tick: about 8x a manual wheelchair's 1.2 m/s
  const jitter = () => (Math.random() - 0.5) * 0.00003; // about 2 m of GPS noise
  let detour = wrongTurn ? null : "done";
  let pos = route0Start();
  onFix(pos, 8);
  nav.simTimer = setInterval(() => {
    if (!nav.active || nav.rerouting) return; // wait while a new route is being found
    const route = selectedRoute();
    const { length } = routeGeometry(route);
    if (detour !== "done" && nav.progress > length * 0.35) {
      // Wrong turn: head away from the route, perpendicular to it, about 8 m per tick.
      if (!detour) {
        const a = pointAtMeters(route, nav.progress);
        const b = pointAtMeters(route, nav.progress + 5);
        const kx = 111320 * Math.cos((a[1] * Math.PI) / 180);
        const ex = (b[0] - a[0]) * kx;
        const ey = (b[1] - a[1]) * 110540;
        const n = Math.hypot(ex, ey) || 1;
        detour = { base: a, perp: [(-ey / n) / kx, (ex / n) / 110540], steps: 0 };
      }
      detour.steps++;
      pos = [detour.base[0] + detour.perp[0] * 8 * detour.steps, detour.base[1] + detour.perp[1] * 8 * detour.steps];
      onFix(pos, 8);
      if (detour.steps >= 6 || nav.rerouting) detour = "done"; // one wrong turn per trip
      return;
    }
    pos = pointAtMeters(route, nav.progress + STEP_M);
    onFix([pos[0] + jitter(), pos[1] + jitter()], 8);
  }, 1000);

  function route0Start() {
    return state.start ? [state.start.lon, state.start.lat] : selectedRoute().geojson.coordinates[0];
  }
}

function showStatus(text, isError = false) {
  const s = document.getElementById("status");
  s.textContent = text;
  s.classList.toggle("error", isError);
}

// ---------- Helpers ----------

function el(tag, attrs = {}, ...children) {
  const node = tag === "svg" ? document.createElementNS("http://www.w3.org/2000/svg", "svg") : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  node.append(...children);
  return node;
}

// Short line sample matching the map: solid for least effort, dashed for fastest.
function swatchSvg(id, color) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "24");
  svg.setAttribute("height", "8");
  svg.setAttribute("aria-hidden", "true");
  const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
  Object.entries({ x1: 2, y1: 4, x2: 22, y2: 4, stroke: color, "stroke-width": 4, "stroke-linecap": ROUTES[id].dash ? "butt" : "round" })
    .forEach(([k, v]) => line.setAttribute(k, v));
  if (ROUTES[id].dash) line.setAttribute("stroke-dasharray", "6 4");
  svg.append(line);
  return svg;
}

function haversine([lon1, lat1], [lon2, lat2]) {
  const R = 6371000;
  const toRad = (d) => (d * Math.PI) / 180;
  const a = Math.sin(toRad(lat2 - lat1) / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(toRad(lon2 - lon1) / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function lineLength(coords) {
  let total = 0;
  for (let i = 1; i < coords.length; i++) total += haversine(coords[i - 1], coords[i]);
  return total;
}

function pointAlong(coords, fraction) {
  const target = lineLength(coords) * fraction;
  let walked = 0;
  for (let i = 1; i < coords.length; i++) {
    const step = haversine(coords[i - 1], coords[i]);
    if (walked + step >= target) {
      const t = step ? (target - walked) / step : 0;
      return [coords[i - 1][0] + t * (coords[i][0] - coords[i - 1][0]), coords[i - 1][1] + t * (coords[i][1] - coords[i - 1][1])];
    }
    walked += step;
  }
  return coords[coords.length - 1];
}

function totalClimb(profile = []) {
  let climb = 0;
  for (let i = 1; i < profile.length; i++) climb += Math.max(0, profile[i][1] - profile[i - 1][1]);
  return climb;
}

function fmtFeet(m) {
  return `${Math.round(m * 3.28084)} ft`;
}

function fmtDistance(m) {
  const miles = m / 1609.34;
  return miles < 0.1 ? fmtFeet(m) : `${miles.toFixed(1)} mi`;
}

function fmtMinutes(min) {
  return `${Math.max(1, Math.round(min))} min`;
}

// Start: the building list loads right away, without waiting for the map (see map.on("load")).
loadPlaces();
