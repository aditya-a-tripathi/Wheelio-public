// Wheelio map layers: everything a wheelchair user needs to SEE on the map, route or not.
// Hazards are drawn in road-sign language: diamond warning signs (amber steep, red very steep,
// brown gravel, dark stairs) plus line styles on the paths themselves, labeled in plain words.
// Also building names, bus and shuttle stops, accessible entrances and accessible parking.
// Data: frontend/data/*.geojson (scripts/build_map_layers.py). Settings: Map key dialog.

// ---------- Settings (remembered on this device) ----------

const SETTINGS_KEY = "wheelio:settings";
const settings = (() => {
  const defaults = { colorblind: false, hazards: true };
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") };
  } catch {
    return defaults;
  }
})();
function saveSettings() {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {}
}

// Difficulty colors. Default: the green-to-red the user asked for. Colorblind mode: Okabe-Ito
// blue / orange / vermillion / plum, which differ in lightness too, and steep parts of a route
// also get dash patterns (see app.js route layers). Text labels are the third cue everywhere.
const PALETTES = {
  default: { gentle: "#1f9d55", moderate: "#e0a800", steep: "#ef6c00", very_steep: "#d0021b" },
  colorblind: { gentle: "#0072b2", moderate: "#e69f00", steep: "#d55e00", very_steep: "#6a1b5a" },
};
const SURFACE = { gravel: "#8a5a2b", gravelBed: "#eadcc2", brick: "#b0473a", stairs: "#2b2f33" };
function palette() {
  return PALETTES[settings.colorblind ? "colorblind" : "default"];
}
// MapLibre expression: color by a `cls` property (gentle | moderate | steep | very_steep).
function clsColor(prop = "cls") {
  const p = palette();
  return ["match", ["get", prop], "moderate", p.moderate, "steep", p.steep, "very_steep", p.very_steep, p.gentle];
}

// ---------- Icons (SVG, rasterized once) ----------

const sign = (fill, glyph) => `<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
  <rect x="9" y="9" width="30" height="30" rx="4" transform="rotate(45 24 24)" fill="${fill}" stroke="#fff" stroke-width="3"/>${glyph}</svg>`;
const square = (fill, glyph) => `<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
  <rect x="5" y="5" width="38" height="38" rx="9" fill="${fill}" stroke="#fff" stroke-width="3"/>${glyph}</svg>`;
const ISA_GLYPH = `<g transform="translate(9 8.5) scale(1.25)"><g fill="none" stroke="#fff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
  <path d="M11 8v5h4.5l2 4.5"/><path d="M11 10.5h3.5"/><path d="M8.6 11.2a4.6 4.6 0 1 0 6.4 5.4"/></g><circle cx="11" cy="5.4" r="1.8" fill="#fff"/></g>`;

function iconSvgs() {
  const p = palette();
  return {
    "hz-steep": sign(p.steep, `<path d="M14.5 31.5h19v-11z" fill="#fff"/>`),
    "hz-very_steep": sign(p.very_steep, `<path d="M15.5 32h17V15z" fill="#fff"/>`),
    "hz-gravel": sign(SURFACE.gravel, `<g fill="#fff"><circle cx="19" cy="21" r="2.2"/><circle cx="27" cy="20" r="1.8"/><circle cx="23" cy="26" r="2.4"/><circle cx="30" cy="27" r="1.7"/><circle cx="17.5" cy="28" r="1.6"/></g>`),
    "hz-brick": sign(SURFACE.brick, `<g fill="none" stroke="#fff" stroke-width="2"><path d="M15 20h18M15 26h18M15 32h18"/><path d="M21 20v6M28 26v6"/></g>`),
    "hz-stairs": sign(SURFACE.stairs, `<path d="M15 32h5v-5h5v-5h5v-5h4" fill="none" stroke="#fff" stroke-width="2.8" stroke-linejoin="round"/>`),
    "chev-steep": `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20"><path d="M5 13l5-5 5 5" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    bus: square("#0b6e75", `<g fill="#fff"><rect x="14" y="12" width="20" height="20" rx="3"/><rect x="16" y="33" width="4" height="3" rx="1"/><rect x="28" y="33" width="4" height="3" rx="1"/></g>
      <rect x="16.5" y="15" width="15" height="8" rx="1.2" fill="#0b6e75"/><g fill="#0b6e75"><circle cx="18.5" cy="28" r="1.6"/><circle cx="29.5" cy="28" r="1.6"/></g>`),
    entrance: square("#1f5fae", ISA_GLYPH),
    parking: square("#1f5fae", `<text x="24" y="33" text-anchor="middle" font-family="Arial, sans-serif" font-weight="700" font-size="26" fill="#fff">P</text>`),
  };
}

function loadImage(svg) {
  return new Promise((resolve, reject) => {
    const img = new Image(48, 48);
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  });
}

async function addIcons() {
  await Promise.all(
    Object.entries(iconSvgs()).map(async ([name, svg]) => {
      const img = await loadImage(svg);
      const id = `wh-${name}`; // prefixed: the base style's sprite already has "bus", "parking", ...
      if (map.hasImage(id)) map.removeImage(id);
      map.addImage(id, img, { pixelRatio: 2 });
    }),
  );
}

// ---------- Layers ----------

const DATA = (name) => `data/${name}.geojson`;
const FONT = ["Noto Sans Regular"];
const FONT_BOLD = ["Noto Sans Bold"];
const z = (...stops) => ["interpolate", ["linear"], ["zoom"], ...stops];
const HAZARD_SHORT = { very_steep: "Very steep hill", steep: "Steep hill", gravel: "Gravel", brick: "Brick", stairs: "Stairs" };
const HAZARD_LAYERS = [];

async function addInfoLayers() {
  await addIcons();
  const firstLabel = map.getStyle().layers.find((l) => l.type === "symbol")?.id;
  const add = (layer, before) => {
    map.addLayer(layer, before);
    return layer.id;
  };
  for (const name of ["network", "hazards", "labels", "buildings", "stops", "entrances", "parking"]) {
    map.addSource(name, { type: "geojson", data: DATA(name) });
  }

  // Campus buildings from Dartmouth's own map (the same survey as the paths and accessible
  // entrances), drawn over the base map's OpenStreetMap buildings, which sit several meters off.
  add({ id: "campus-buildings", type: "fill", source: "buildings",
    paint: { "fill-color": "#d5dbe0", "fill-outline-color": "#bcc4cb" } }, firstLabel);
  add({ id: "campus-buildings-edge", type: "line", source: "buildings", minzoom: 16,
    paint: { "line-color": "#b4bdc5", "line-width": 1 } }, firstLabel);
  // Destination building: tinted so you can find the door you are heading to.
  add({ id: "dest-building", type: "fill", source: "buildings", filter: ["==", ["get", "name"], ""],
    paint: { "fill-color": "#1f5fae", "fill-opacity": 0.14 } }, firstLabel);
  add({ id: "dest-building-edge", type: "line", source: "buildings", filter: ["==", ["get", "name"], ""],
    paint: { "line-color": "#1f5fae", "line-width": 2 } }, firstLabel);

  // ----- path network: what each path is like, drawn on the path itself -----
  // Zoomed out, only the route and its own hazards show (route-hazards below); the whole network's
  // surfaces and slopes appear once you zoom in to street level.
  const DETAIL_Z = 16;
  const net = (id, filter, paint, layout = {}, minzoom = DETAIL_Z) =>
    HAZARD_LAYERS.push(add({ id, type: "line", source: "network", filter, minzoom,
      layout: { "line-cap": "round", "line-join": "round", ...layout }, paint }, firstLabel));
  const cls = (v) => ["==", ["get", "cls"], v];
  const mat = (v) => ["==", ["get", "material"], v];
  // Gravel: a tan bed with brown dots, unmistakably not pavement.
  net("net-gravel-bed", mat("gravel"), { "line-color": SURFACE.gravelBed, "line-width": z(14, 3, 18, 10) });
  net("net-gravel", mat("gravel"), { "line-color": SURFACE.gravel, "line-width": z(14, 2, 18, 4.5), "line-dasharray": [0.2, 1.6] });
  net("net-brick", mat("brick"), { "line-color": SURFACE.brick, "line-width": z(14, 2, 18, 5), "line-dasharray": [1.2, 0.6] }, { "line-cap": "butt" });
  // Stairs: short dark hatch, like steps seen from above.
  net("net-stairs", cls("stairs"), { "line-color": SURFACE.stairs, "line-width": z(14, 2.5, 18, 8), "line-dasharray": [0.3, 0.3] }, { "line-cap": "butt" });
  // Hills: the steeper, the wider and redder. Moderate only shows up close.
  // Moderate slopes are common and fine for most: only close up, thin and light, so the real
  // hazards (steep, very steep, gravel, stairs) stand out.
  net("net-moderate", ["all", cls("moderate"), ["!", mat("gravel")]], { "line-color": clsColor(), "line-width": z(16.8, 1.5, 18.5, 3.5), "line-opacity": 0.55 }, {}, 16.8);
  net("net-steep", ["in", ["get", "cls"], ["literal", ["steep", "very_steep"]]], { "line-color": clsColor(), "line-width": z(14, 2.5, 16, 4, 18, 9) });
  // Chevrons point uphill (the data runs uphill on steep paths).
  HAZARD_LAYERS.push(add({ id: "net-chevrons", type: "symbol", source: "network", minzoom: 16,
    filter: ["in", ["get", "cls"], ["literal", ["steep", "very_steep"]]],
    layout: { "symbol-placement": "line", "symbol-spacing": 28, "icon-image": "wh-chev-steep", "icon-size": z(16, 0.55, 18, 0.9),
      "icon-rotate": 90, "icon-rotation-alignment": "map", "icon-allow-overlap": true, "icon-ignore-placement": true } }));
  // ----- building names -----
  add({ id: "building-names", type: "symbol", source: "labels", minzoom: 16,
    layout: { "text-field": ["get", "name"], "text-font": FONT, "text-size": z(15.6, 10.5, 18, 13.5), "text-max-width": 8,
      "symbol-sort-key": ["-", 0, ["get", "area"]], "text-padding": 4 },
    paint: { "text-color": "#46505a", "text-halo-color": "rgba(255,255,255,0.9)", "text-halo-width": 1.6 } });

  // ----- warning signs at each hazard stretch -----
  const signText = ["match", ["get", "kind"], "very_steep", "Very steep hill", "steep", "Steep hill", "gravel", "Gravel", "brick", "Brick", "stairs", "Stairs", ""];
  const hazardSigns = add({ id: "hazard-signs", type: "symbol", source: "hazards", minzoom: DETAIL_Z,
    // Stairs are everywhere on campus: their signs appear only close up, so hills stand out.
    filter: ["any", ["!=", ["get", "kind"], "stairs"], [">=", ["zoom"], 16.4]],
    layout: { "icon-image": ["concat", "wh-hz-", ["get", "kind"]], "icon-size": z(16, 0.7, 18, 0.95),
      "icon-allow-overlap": true, "symbol-sort-key": ["match", ["get", "kind"], "very_steep", 0, "steep", 1, "gravel", 2, "brick", 3, 4],
      "text-field": signText,
      "text-font": FONT_BOLD, "text-size": 12, "text-anchor": "left", "text-offset": [1.3, 0], "text-optional": true },
    paint: { "text-color": ["match", ["get", "kind"], "very_steep", "#9b0016", "steep", "#a34400", "gravel", "#6b4220", "#2b2f33"],
      "text-halo-color": "#fff", "text-halo-width": 2 } });
  HAZARD_LAYERS.push(hazardSigns);

  // ----- stops, entrances, parking -----
  add({ id: "stops", type: "symbol", source: "stops", minzoom: 15.8,
    layout: { "icon-image": "wh-bus", "icon-size": z(14.5, 0.5, 18, 0.8), "icon-allow-overlap": true,
      "text-field": ["step", ["zoom"], "", 16.2, ["concat", ["get", "name"], "\n", ["get", "service"]]], "text-font": FONT, "text-size": 11,
      "text-anchor": "left", "text-offset": [1.2, 0], "text-optional": true, "text-justify": "left" },
    paint: { "text-color": "#0b5359", "text-halo-color": "#fff", "text-halo-width": 1.8 } });
  add({ id: "entrances", type: "symbol", source: "entrances", minzoom: 17,
    layout: { "icon-image": "wh-entrance", "icon-size": z(17, 0.42, 19, 0.6), "icon-allow-overlap": true } });
  add({ id: "parking", type: "symbol", source: "parking", minzoom: 17.5,
    layout: { "icon-image": "wh-parking", "icon-size": z(17.5, 0.42, 19, 0.58), "icon-allow-overlap": false } });

  // ----- the route's own hazards, and hazards or stops right next to it: visible at every zoom -----
  map.addSource("route-hazards", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  add({ id: "route-hazard-signs", type: "symbol", source: "route-hazards",
    layout: { "icon-image": ["get", "icon"], "icon-size": ["case", ["get", "onRoute"], 0.78, 0.6], "icon-allow-overlap": true,
      "symbol-sort-key": ["case", ["get", "onRoute"], 0, 1],
      // The route's own hazards are labeled at every zoom; nearby ones only from street level.
      "text-field": ["step", ["zoom"], ["case", ["get", "onRoute"], ["get", "text"], ""], 16, ["get", "text"]], "text-font": FONT_BOLD, "text-size": 11.5,
      "text-anchor": "left", "text-offset": [1.1, 0], "text-optional": true, "text-max-width": 9 },
    paint: { "text-color": ["match", ["get", "kind"], "very_steep", "#9b0016", "steep", "#a34400", "gravel", "#6b4220", "stop", "#0b5359", "#2b2f33"],
      "text-halo-color": "#fff", "text-halo-width": 2 } });
  applyHazardVisibility();
  // Tap a warning sign: what it is and why it matters.
  map.on("click", "hazard-signs", (e) => {
    const p = e.features[0].properties;
    const why = { very_steep: "Steeper than a standard wheelchair ramp. Hard to push up and to control going down.",
      steep: "Close to the steepest a ramp is allowed to be. Tiring uphill.", gravel: "Loose surface. Much harder to push on.",
      brick: "Bumpy surface.", stairs: "Steps. Routes never use them." }[p.kind] || "";
    const length = p.kind !== "stairs" && p.length_ft ? ` About ${p.length_ft} ft.` : "";
    new maplibregl.Popup({ closeButton: true, maxWidth: "260px", className: "hazard-popup" })
      .setLngLat(e.features[0].geometry.coordinates)
      .setHTML(`<strong>${HAZARD_SHORT[p.kind] || "Hazard"}${p.near ? ` near ${escapeHtml(p.near)}` : ""}</strong><p>${why}${length}</p>`)
      .addTo(map);
  });
  // Tap a bus or shuttle stop: its name and service, with directions to it.
  const stopPopup = (e) => {
    const f = e.features[0];
    if (f.layer.id === "route-hazard-signs" && f.properties.kind !== "stop") return;
    const name = f.layer.id === "stops" ? f.properties.name : f.properties.text;
    const service = f.properties.service ?? "Bus or shuttle stop";
    const box = document.createElement("div");
    box.innerHTML = `<strong>${escapeHtml(name)}</strong><p>${escapeHtml(service)}. Check the operator's schedule before you go.</p>`;
    const popup = new maplibregl.Popup({ maxWidth: "260px", className: "hazard-popup" }).setLngLat(f.geometry.coordinates).setDOMContent(box).addTo(map);
  };
  map.on("click", "stops", stopPopup);
  map.on("click", "route-hazard-signs", stopPopup);
  for (const id of ["stops", "route-hazard-signs"]) {
    map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
  }
  map.on("mouseenter", "hazard-signs", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "hazard-signs", () => (map.getCanvas().style.cursor = ""));
}

// Called by app.js whenever routes change: the selected route's hazards (labeled, with length) and
// any hazard sign or stop within NEAR_M of a shown route (icon only, labeled when zoomed in).
const NEAR_M = 20; // signs and stops this close to the selected route
const NETWORK_NEAR_M = 25; // slope and surface detail this close to the selected route
const CLOSE_Z = 17; // from this zoom, all detail in view shows, route or not
let closeMode = false;
// Crossing the close-in zoom switches between "route only" and "everything in view".
map.on("zoomend", () => {
  if (!lastContext || !lastContext[0].length) return;
  if ((map.getZoom() >= CLOSE_Z) !== closeMode) setRouteContext(...lastContext);
});
let hazardPoints = null;
let stopPoints = null;
let networkLines = null;
let routeActive = false;
// Phones can get these after the route is drawn; re-apply the route filter when they arrive.
let lastContext = null;
const reapply = () => lastContext && setRouteContext(...lastContext);
fetch(DATA("hazards")).then((r) => r.json()).then((d) => { hazardPoints = d.features; reapply(); }).catch(() => {});
fetch(DATA("network")).then((r) => r.json()).then((d) => { networkLines = d.features; reapply(); }).catch(() => {});
fetch(DATA("stops")).then((r) => r.json()).then((d) => { stopPoints = d.features; reapply(); }).catch(() => {});

function nearLine(pt, coords, maxM) {
  const kx = 111320 * Math.cos((pt[1] * Math.PI) / 180), ky = 111132;
  for (let i = 0; i < coords.length - 1; i++) {
    const ax = (coords[i][0] - pt[0]) * kx, ay = (coords[i][1] - pt[1]) * ky;
    const bx = (coords[i + 1][0] - pt[0]) * kx, by = (coords[i + 1][1] - pt[1]) * ky;
    const dx = bx - ax, dy = by - ay, len = dx * dx + dy * dy;
    const t = len ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len)) : 0;
    if (Math.hypot(ax + t * dx, ay + t * dy) <= maxM) return true;
  }
  return false;
}

function setRouteContext(routes, selectedId) {
  lastContext = [routes, selectedId];
  const src = mapReady && map.getSource("route-hazards");
  if (!src) return;
  const features = [];
  const selected = routes.find((r) => r.id === selectedId);
  const SHORT = { very_steep: "Very steep", steep: "Steep", gravel: "Gravel", brick: "Brick", stairs: "Stairs" };
  // With a route on screen, slope and surface detail is drawn only for paths near it; other
  // hills across campus are noise for this trip (user feedback). No route: the whole campus.
  routeActive = routes.length > 0;
  // Only the chosen route's surroundings: the other route is a gray option, not this trip's path.
  const lines0 = (selected ? [selected] : routes).map((r) => r.geojson.coordinates);
  // Zoomed out with a route: only the route's surroundings. Close in (street level, as when
  // navigating or scrolling along the route): every path's detail in view.
  const filtered = routeActive && map.getZoom() < CLOSE_Z;
  closeMode = !filtered;
  if (!filtered) {
    map.getSource("network").setData(DATA("network"));
  } else if (!networkLines) {
    // Not loaded yet: show nothing campus-wide rather than everything; reapply() fills it in.
    map.getSource("network").setData({ type: "FeatureCollection", features: [] });
  } else {
    const nearRoute = (f) => f.geometry.coordinates.some((p) => lines0.some((c) => nearLine(p, c, NETWORK_NEAR_M)));
    map.getSource("network").setData({ type: "FeatureCollection", features: networkLines.filter(nearRoute) });
  }
  applyHazardVisibility();
  for (const h of (selected?.hazards ?? []).filter((h) => SHORT[h.kind])) {
    const ft = h.length_m >= 15 ? ` · ${Math.round((h.length_m * 3.28084) / 10) * 10} ft` : "";
    features.push({ type: "Feature", geometry: { type: "Point", coordinates: [h.lon, h.lat] },
      properties: { kind: h.kind, icon: `wh-hz-${h.kind}`, text: SHORT[h.kind] + ft, onRoute: true } });
  }
  const lines = lines0;
  const near = (f) => lines.some((c) => nearLine(f.geometry.coordinates, c, NEAR_M));
  for (const f of hazardPoints ?? []) {
    if (!near(f)) continue;
    if (features.some((x) => x.properties.onRoute && Math.abs(x.geometry.coordinates[0] - f.geometry.coordinates[0]) < 0.0004
      && Math.abs(x.geometry.coordinates[1] - f.geometry.coordinates[1]) < 0.0003)) continue; // already labeled
    features.push({ type: "Feature", geometry: f.geometry,
      properties: { kind: f.properties.kind, icon: `wh-hz-${f.properties.kind}`, text: SHORT[f.properties.kind] ?? "", onRoute: false } });
  }
  for (const f of stopPoints ?? []) {
    if (near(f)) features.push({ type: "Feature", geometry: f.geometry, properties: { kind: "stop", icon: "wh-bus", text: f.properties.name, onRoute: false } });
  }
  src.setData({ type: "FeatureCollection", features });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function applyHazardVisibility() {
  for (const id of HAZARD_LAYERS) {
    if (!map.getLayer(id)) continue;
    // With a route, campus-wide signs give way to the route's own and nearby ones (route-hazard-signs).
    const show = settings.hazards && !(routeActive && !closeMode && id === "hazard-signs");
    map.setLayoutProperty(id, "visibility", show ? "visible" : "none");
  }
  if (map.getLayer("route-hazard-signs")) map.setLayoutProperty("route-hazard-signs", "visibility", settings.hazards ? "visible" : "none");
  document.getElementById("legend-strip")?.toggleAttribute("hidden", !settings.hazards);
}

// Recolor everything that uses the difficulty palette (network, icons, routes).
async function applyPalette() {
  document.body.classList.toggle("colorblind", settings.colorblind);
  if (!mapReady) return;
  for (const id of ["net-moderate", "net-steep"]) map.setPaintProperty(id, "line-color", clsColor());
  await addIcons();
  window.recolorRoutes?.();
  renderMapKey();
  renderLegendStrip();
}

// Highlight the destination building (name match with the Dartmouth buildings layer).
function highlightDestination(name) {
  if (!mapReady || !map.getLayer("dest-building")) return;
  const f = ["==", ["get", "name"], (state.view === "3d" ? "" : name) || ""]; // flat tint only fits 2D
  map.setFilter("dest-building", f);
  map.setFilter("dest-building-edge", f);
}

// ---------- Map key + settings dialog ----------

const KEY_ROWS = () => {
  const p = palette();
  const line = (color, dash = "", width = 6, bed = "") =>
    `<svg viewBox="0 0 44 14" width="44" height="14" aria-hidden="true">${bed ? `<path d="M3 7h38" stroke="${bed}" stroke-width="10" stroke-linecap="round"/>` : ""}<path d="M3 7h38" stroke="${color}" stroke-width="${width}" stroke-linecap="round" ${dash ? `stroke-dasharray="${dash}"` : ""}/></svg>`;
  const icon = (name) => `<img src="data:image/svg+xml;charset=utf-8,${encodeURIComponent(iconSvgs()[name])}" width="26" height="26" alt="">`;
  return [
    ["Your route", [
      [line(p.gentle, "", 7), "Gentle"], [line(p.moderate, "", 7), "Moderate slope"],
      [line(p.steep, settings.colorblind ? "8 4" : "", 7), "Steep: close to the ramp limit"],
      [line(p.very_steep, settings.colorblind ? "3 3" : "", 7), "Very steep: steeper than a ramp"],
      [line("#9aa3ab", "6 5", 5), "The other route"],
    ]],
    ["Hazards on every path", [
      [icon("hz-very_steep"), "Very steep hill (arrows point uphill)"], [icon("hz-steep"), "Steep hill"],
      [icon("hz-gravel") + line(SURFACE.gravel, "1 7", 4, SURFACE.gravelBed), "Gravel path"],
      [icon("hz-brick"), "Brick path"], [icon("hz-stairs") + line(SURFACE.stairs, "2 2", 6), "Stairs (never used by routes)"],
    ]],
    ["Getting around", [
      [icon("entrance"), "Accessible entrance"], [icon("parking"), "Accessible parking"], [icon("bus"), "Bus or shuttle stop"],
    ]],
  ];
};

function renderMapKey() {
  const body = document.getElementById("mapkey-rows");
  if (!body) return;
  body.innerHTML = KEY_ROWS().map(([title, rows]) =>
    `<section class="key-group"><h3>${title}</h3><ul>${rows.map(([sym, text]) =>
      `<li><span class="key-sym">${sym}</span><span>${text}</span></li>`).join("")}</ul></section>`).join("");
  document.getElementById("set-colorblind").checked = settings.colorblind;
  document.getElementById("set-hazards").checked = settings.hazards;
}

document.body.classList.toggle("colorblind", settings.colorblind);
function renderLegendStrip() {
  const strip = document.getElementById("legend-strip");
  if (!strip) return;
  const svgs = iconSvgs();
  const img = (name) => `<img src="data:image/svg+xml;charset=utf-8,${encodeURIComponent(svgs[name])}" alt="">`;
  strip.innerHTML = [["hz-very_steep", "Very steep"], ["hz-steep", "Steep"], ["hz-gravel", "Gravel"], ["hz-stairs", "Stairs"]]
    .map(([n, t]) => `<span>${img(n)}${t}</span>`).join("");
}
renderLegendStrip();

(function wireMapKey() {
  const dialog = document.getElementById("mapkey-dialog");
  const open = document.getElementById("mapkey-btn");
  if (!dialog || !open) return;
  open.addEventListener("click", () => {
    renderMapKey();
    dialog.showModal();
  });
  document.getElementById("mapkey-close").addEventListener("click", () => dialog.close());
  document.getElementById("legend-strip")?.addEventListener("click", () => {
    renderMapKey();
    dialog.showModal();
  });
  dialog.addEventListener("click", (e) => e.target === dialog && dialog.close());
  document.getElementById("set-colorblind").addEventListener("change", (e) => {
    settings.colorblind = e.target.checked;
    saveSettings();
    applyPalette();
  });
  document.getElementById("set-hazards").addEventListener("change", (e) => {
    settings.hazards = e.target.checked;
    saveSettings();
    applyHazardVisibility();
  });
})();

// ---------- Barrier reports on the map: the exact path, and what's ruled out ----------

// Nearest point on an accessible Dartmouth path (the same network the backend attaches reports
// to), so the pin you place is the spot that gets reported. Returns null when nothing is close.
function nearestAccessiblePath([lon, lat], maxM = 30) {
  if (!networkLines) return null;
  const kx = 111320 * Math.cos((lat * Math.PI) / 180), ky = 111132;
  let best = null;
  for (const f of networkLines) {
    if (f.properties.cls === "stairs" || f.properties.main === false) continue; // same rule as the backend
    const c = f.geometry.coordinates;
    for (let i = 0; i < c.length - 1; i++) {
      const ax = (c[i][0] - lon) * kx, ay = (c[i][1] - lat) * ky;
      const bx = (c[i + 1][0] - lon) * kx, by = (c[i + 1][1] - lat) * ky;
      const dx = bx - ax, dy = by - ay, len = dx * dx + dy * dy;
      const t = len ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len)) : 0;
      const d = Math.hypot(ax + t * dx, ay + t * dy);
      if (d <= maxM && (!best || d < best.dist)) {
        best = { dist: d, coords: c, point: [c[i][0] + t * (c[i + 1][0] - c[i][0]), c[i][1] + t * (c[i + 1][1] - c[i][1])] };
      }
    }
  }
  return best;
}

// Blocked (confirmed) stretches drawn in red with white breaks; reported ones in dashed amber.
function setBlockedPaths(reports) {
  if (!mapReady) return;
  if (!map.getSource("blocked-paths")) {
    map.addSource("blocked-paths", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "blocked-casing", type: "line", source: "blocked-paths",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#ffffff", "line-width": 12 } });
    map.addLayer({ id: "blocked-line", type: "line", source: "blocked-paths",
      layout: { "line-cap": "butt", "line-join": "round" },
      paint: { "line-color": ["match", ["get", "status"], "active", "#d0021b", "#e8912d"], "line-width": 8 } });
    map.addLayer({ id: "blocked-breaks", type: "line", source: "blocked-paths",
      layout: { "line-cap": "butt" },
      paint: { "line-color": "#ffffff", "line-width": 3, "line-dasharray": [1.2, 1.2] } });
    map.addLayer({ id: "blocked-label", type: "symbol", source: "blocked-paths", minzoom: 15.5,
      layout: { "symbol-placement": "line", "symbol-spacing": 160,
        "text-field": ["match", ["get", "status"], "active", "Blocked", "Reported barrier"],
        "text-font": FONT_BOLD, "text-size": 12, "text-offset": [0, -1.2] },
      paint: { "text-color": ["match", ["get", "status"], "active", "#9b0016", "#8a4b00"], "text-halo-color": "#fff", "text-halo-width": 2 } });
  }
  const features = reports
    .filter((r) => r.path_coords && (r.status === "active" || r.status === "reported"))
    .map((r) => ({ type: "Feature", geometry: { type: "LineString", coordinates: r.path_coords }, properties: { status: r.status } }));
  map.getSource("blocked-paths").setData({ type: "FeatureCollection", features });
  for (const id of ["blocked-casing", "blocked-line", "blocked-breaks", "blocked-label"]) map.moveLayer(id);
}
