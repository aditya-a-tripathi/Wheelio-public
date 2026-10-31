// Wheelio barrier reports: demo sign-in, photo, "Is the barrier here?", result, pins on the map,
// "Still there?", and the facilities fix list. Built against docs/api.md (v3 trust rules, v4 fix list
// and photos). Uses app.js globals (map, state, nav, el, sheet, setSheet, requestRoute, ...).
// ?devreports=1 posts to /api/dev/reports (server started with WHEELIO_DEV=1) to stage statuses.

const DEV_REPORTS = new URLSearchParams(location.search).has("devreports");

const BARRIER_LABELS = {
  snow_at_curb_cut: "Snow at a curb cut",
  ice: "Ice",
  construction: "Construction",
  blocked_path: "Blocked path",
  elevator_out: "Elevator out of service",
  stairs: "Stairs",
  other: "Barrier",
  not_a_barrier: "Possible barrier",
};
const STATUS_LABELS = { needs_confirm: "Needs a person to check", reported: "Reported once", active: "Confirmed" };

// ---------- Account (demo sign-in) ----------
// Only reporting and voting need an account; routing never does.

const auth = loadAuth();

function loadAuth() {
  try {
    return JSON.parse(sessionStorage.getItem("wheelio:auth")) ?? {};
  } catch {
    return {};
  }
}

function saveAuth() {
  try {
    if (auth.token) sessionStorage.setItem("wheelio:auth", JSON.stringify(auth));
    else sessionStorage.removeItem("wheelio:auth");
  } catch {}
  renderAccount();
}

function signOut() {
  auth.token = null;
  auth.account = null;
  saveAuth();
}

function renderAccount() {
  document.getElementById("account-line").hidden = !auth.account;
  document.getElementById("account-name").textContent = auth.account?.name ?? "";
  document.getElementById("fixlist-open").hidden = auth.account?.role !== "facilities";
}
document.getElementById("sign-out").addEventListener("click", () => {
  signOut();
  toast("Signed out.");
});

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// Every backend error is {"detail": "<plain sentence>"} (v4); show it as-is.
async function api(path, { method = "GET", json, form } = {}) {
  const headers = {};
  if (auth.token) headers.Authorization = `Bearer ${auth.token}`;
  let body;
  if (form) body = form;
  else if (json) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  }
  let res;
  let data;
  try {
    res = await fetch(path, { method, headers, body });
    data = await res.json();
  } catch {
    throw new ApiError(0, "Lost connection to the server. Check your signal and try again.");
  }
  if (!res.ok) {
    if (res.status === 401) signOut(); // e.g. the server restarted and forgot the demo token
    throw new ApiError(res.status, data?.detail || "Something went wrong. Try again.");
  }
  return data;
}

// ---------- Dialogs ----------

let returnFocus = null;
function openDialog(dialog) {
  if (!dialog.open) {
    returnFocus = document.activeElement;
    dialog.showModal();
  }
}
for (const dialog of document.querySelectorAll("dialog")) {
  dialog.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("close", () => {
    if (!dialog.dataset.keepFocus) returnFocus?.focus?.();
    delete dialog.dataset.keepFocus;
  });
}
// Close without sending focus back (the flow continues somewhere else).
function handOff(dialog) {
  dialog.dataset.keepFocus = "1";
  dialog.close();
}

let toastTimer = 0;
function toast(text) {
  const t = document.getElementById("toast");
  t.replaceChildren(iconCircleCheck(), el("span", {}, text));
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 5000); // confirmations only; nothing to act on
}

// ---------- Report flow ----------

const reportDialog = document.getElementById("report-dialog");
const photoInput = document.getElementById("photo-input");
const report = { photo: null, previewUrl: null, device: null, pin: null };
let afterSignIn = () => showStep("photo");

function showStep(name) {
  for (const step of reportDialog.querySelectorAll(".step")) step.hidden = step.dataset.step !== name;
  const titles = { signin: "Sign in", photo: "Report a barrier", sending: "Report a barrier", result: "Report a barrier" };
  document.getElementById("report-title").textContent = titles[name];
  reportDialog.querySelector(`[data-step="${name}"]`).querySelector("button, label, [tabindex]")?.focus();
}

document.getElementById("report-btn").addEventListener("click", startReport);

// Reporting without an account: allowed (a warning only, until a campus account confirms it).
document.getElementById("report-anon")?.addEventListener("click", () => {
  const next = afterSignIn;
  afterSignIn = null;
  (next ?? (() => showStep("photo")))();
});

function startReport() {
  resetPhoto();
  findDevice();
  afterSignIn = () => showStep("photo");
  if (auth.token) showStep("photo");
  else showSignIn();
  openDialog(reportDialog);
}

// The phone's own location, sent with the report (the backend checks it is near the spot).
function findDevice() {
  report.device = nav.fix ? [...nav.fix] : null;
  if (report.device || !("geolocation" in navigator)) return;
  navigator.geolocation.getCurrentPosition(
    (pos) => (report.device = [pos.coords.longitude, pos.coords.latitude]),
    () => {},
    { enableHighAccuracy: true, maximumAge: 10000, timeout: 15000 },
  );
}

async function showSignIn() {
  showStep("signin");
  const list = document.getElementById("account-list");
  list.replaceChildren(el("li", { class: "hint" }, "Loading accounts…"));
  let accounts;
  try {
    accounts = await api("/api/auth/demo-accounts");
  } catch (err) {
    list.replaceChildren(el("li", { class: "hint" }, err.message));
    return;
  }
  list.replaceChildren(
    ...accounts.map((a) => {
      const button = el(
        "button",
        { type: "button", class: "account-row" },
        el("span", { class: `avatar ${a.role}`, "aria-hidden": "true" }, a.name.charAt(0)),
        el("span", { class: "account-text" }, el("span", { class: "account-row-name" }, a.name.replace(/ \(demo.*\)$/, "")), el("span", { class: "account-row-role" }, a.role === "facilities" ? "Campus facilities staff" : "Student")),
      );
      button.addEventListener("click", () => signIn(a.id));
      return el("li", {}, button);
    }),
  );
  list.querySelector("button")?.focus();
}

async function signIn(accountId) {
  try {
    const data = await api("/api/auth/demo-signin", { method: "POST", json: { account_id: accountId } });
    auth.token = data.token;
    auth.account = data.account;
    saveAuth(); // the sheet's account line shows who is signed in; no toast mid-flow
    afterSignIn();
  } catch (err) {
    document.getElementById("account-list").prepend(el("li", { class: "hint error" }, err.message));
  }
}

function resetPhoto() {
  report.photo = null;
  if (report.previewUrl) URL.revokeObjectURL(report.previewUrl);
  report.previewUrl = null;
  photoInput.value = "";
  document.getElementById("photo-preview").hidden = true;
  document.getElementById("photo-take").hidden = false;
  document.getElementById("photo-retake").hidden = true;
  document.getElementById("photo-next").disabled = true;
}

photoInput.addEventListener("change", () => {
  const file = photoInput.files?.[0];
  if (!file) return;
  report.photo = file;
  if (report.previewUrl) URL.revokeObjectURL(report.previewUrl);
  report.previewUrl = URL.createObjectURL(file);
  const img = document.getElementById("photo-preview");
  img.src = report.previewUrl;
  img.hidden = false;
  document.getElementById("photo-take").hidden = true;
  document.getElementById("photo-retake").hidden = false;
  document.getElementById("photo-next").disabled = false;
  document.getElementById("photo-next").focus();
});

document.getElementById("photo-next").addEventListener("click", () => {
  handOff(reportDialog);
  enterPlaceMode();
});

// ---------- "Is the barrier here?" ----------
// GPS can pick the wrong path at an intersection, so the user confirms the spot on the map: a
// draggable pin (default: your location) with the nearest path highlighted. Tapping the map moves
// the pin too.

let placing = false;
window.isPlacing = () => placing; // app.js skips "tap to set start" while placing

function enterPlaceMode() {
  placing = true;
  document.body.classList.add("placing");
  document.getElementById("place-ui").hidden = false;
  const start = report.device ?? nav.fix ?? (state.start ? [state.start.lon, state.start.lat] : map.getCenter().toArray());
  if (!report.pin) {
    report.pin = new maplibregl.Marker({ element: barrierPinElement("new"), draggable: true });
    report.pin.on("drag", () => highlightNearestPath(false));
    report.pin.on("dragend", () => highlightNearestPath(true)); // let go: the pin snaps onto the path
    report.pin.getElement().removeAttribute("aria-label");
  }
  report.pin.setLngLat(start).addTo(map);
  map.easeTo({ center: start, zoom: Math.max(map.getZoom(), 18), duration: reduceMotion ? 0 : 600 });
  map.once("moveend", () => highlightNearestPath(true));
  document.getElementById("place-send").focus();
}

function exitPlaceMode() {
  placing = false;
  document.body.classList.remove("placing");
  document.getElementById("place-ui").hidden = true;
  report.pin?.remove();
  if (mapReady) map.getSource("place-path")?.setData({ type: "FeatureCollection", features: [] });
}

map.on("click", (e) => {
  if (!placing) return;
  report.pin.setLngLat(e.lngLat);
  highlightNearestPath(true);
});

// The accessible path the report will attach to: the nearest one in Dartmouth's path network, the
// same network and rule the backend uses (within 30 m). Its whole stretch is highlighted, since
// that's what routing will avoid; when snap is true the pin moves onto the path, so the spot you
// see is exactly the spot that gets reported.
function highlightNearestPath(snap = false) {
  if (!mapReady || !report.pin) return;
  if (!map.getSource("place-path")) {
    map.addSource("place-path", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({
      id: "place-path",
      type: "line",
      source: "place-path",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#e8912d", "line-width": 10, "line-opacity": 0.85 },
    });
  }
  map.moveLayer("place-path"); // on top while placing: over the route lines and buildings
  const best = window.nearestAccessiblePath?.(report.pin.getLngLat().toArray());
  if (best && snap) report.pin.setLngLat(best.point);
  map.getSource("place-path").setData({
    type: "FeatureCollection",
    features: best ? [{ type: "Feature", geometry: { type: "LineString", coordinates: best.coords }, properties: {} }] : [],
  });
  document.getElementById("place-send").disabled = !best;
  document.querySelector(".place-hint").textContent = best
    ? "Drag the pin onto the path where the barrier is. The highlighted path is what your report affects."
    : "No accessible path near the pin. Drag it onto the path where the barrier is.";
}

function stretchAround(line, along, meters) {
  const { coords, cum } = routeGeometry(line);
  const from = along - meters;
  const to = along + meters;
  const out = [pointAtMeters(line, from)];
  for (let i = 0; i < coords.length; i++) if (cum[i] > from && cum[i] < to) out.push(coords[i]);
  out.push(pointAtMeters(line, to));
  return out;
}

document.getElementById("place-back").addEventListener("click", () => {
  exitPlaceMode();
  openDialog(reportDialog);
  showStep("photo");
});
document.getElementById("place-send").addEventListener("click", () => {
  const spot = report.pin.getLngLat();
  exitPlaceMode();
  openDialog(reportDialog);
  submitReport(spot);
});

async function submitReport(spot) {
  showStep("sending");
  const form = new FormData();
  form.append("photo", report.photo);
  form.append("lat", spot.lat);
  form.append("lon", spot.lng);
  if (report.device) {
    form.append("device_lon", report.device[0]);
    form.append("device_lat", report.device[1]);
  }
  try {
    const created = await api(DEV_REPORTS ? "/api/dev/reports" : "/api/reports", { method: "POST", form });
    showResult(created);
    refreshPins();
    if (!nav.active && state.routes.length) requestRoute(); // routes may now warn about or avoid it
  } catch (err) {
    showError(err);
  }
}

const NEXT_STEPS = {
  needs_confirm: "It's on the map for others to see. Routes change only after another student or campus staff confirms it.",
  reported: "Routes that pass it now warn people and steer around it when they can. A campus account, or three reports in all, confirm it.",
  active: "It's confirmed. Routes now go around it when it can't be passed.",
};

function showResult(r) {
  showStep("result");
  const confident = r.status !== "needs_confirm";
  setResultIcon(confident ? "ok" : "wait");
  document.getElementById("result-title").textContent = "Report sent";
  document.getElementById("result-text").textContent = "Thank you for helping keep campus routes accessible.";
  document.getElementById("result-card").hidden = false;
  document.getElementById("result-status").textContent = r.status_text || STATUS_LABELS[r.status] || "";
  document.getElementById("result-next").textContent = `${r.description ? `${r.description} ` : ""}${NEXT_STEPS[r.status] ?? ""}`;
  setResultAction("Done", () => {
    reportDialog.close();
    toast("Map updated. Your report is on the map.");
  });
}

function showError(err) {
  showStep("result");
  setResultIcon("error");
  document.getElementById("result-title").textContent = "Couldn't send the report";
  document.getElementById("result-text").textContent = err.message;
  document.getElementById("result-card").hidden = true;
  if (err.status === 401) {
    setResultAction("Sign in", () => showSignIn());
    afterSignIn = () => {
      openDialog(reportDialog);
      showStep("photo");
    };
  } else if (err.status === 422 && report.photo) {
    setResultAction("Back to the map", () => {
      handOff(reportDialog);
      enterPlaceMode();
    });
  } else {
    setResultAction("Done", () => reportDialog.close());
  }
}

function setResultAction(label, handler) {
  const old = document.getElementById("result-action");
  const button = old.cloneNode(false); // drop the previous handler
  button.textContent = label;
  button.addEventListener("click", handler);
  old.replaceWith(button);
  button.focus();
}

function setResultIcon(kind) {
  const icon = document.getElementById("result-icon");
  icon.className = `result-icon ${kind}`;
  const paths = {
    ok: '<path d="M7 12.5l3.4 3.4L17.5 8.8" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>',
    wait: '<circle cx="12" cy="12" r="6.5" fill="none" stroke="#fff" stroke-width="2.2"/><path d="M12 8.5V12l2.4 1.6" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/>',
    error: '<path d="M12 7v6" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/><circle cx="12" cy="16.6" r="1.5" fill="#fff"/>',
  };
  icon.replaceChildren(iconSvg(48, paths[kind]));
}

function iconCircleCheck() {
  return iconSvg(22, '<circle cx="12" cy="12" r="11" fill="#1e7b45"/><path d="M7 12.5l3.2 3.2L17 9" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>');
}

// ---------- Barrier pins ----------
// Every open report on the map. Confirmed = filled red, reported once = outlined amber, needs a
// person = dashed gray "?". Pins on the selected route get a ring. Each pin is a button.

let barrierReports = [];
let pinMarkers = [];

async function refreshPins() {
  try {
    barrierReports = await api("/api/reports");
  } catch {
    return; // pins are extra; keep the last ones
  }
  renderBarrierPins();
}

function barrierPinElement(status) {
  const mark = status === "needs_confirm" ? '<text x="12" y="17" text-anchor="middle" font-size="12" font-weight="700" fill="currentColor" font-family="system-ui, sans-serif">?</text>'
    : '<path d="M12 7.5v5" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="16.2" r="1.4" fill="currentColor"/>';
  const pin = el("div", { class: `barrier-pin ${status}` });
  pin.append(iconSvg(22, mark));
  return pin;
}

function renderBarrierPins() {
  for (const m of pinMarkers) m.remove();
  pinMarkers = [];
  window.setBlockedPaths?.(barrierReports); // the stretch each report rules out, drawn on the path
  const onRoute = new Set(selectedRoute()?.report_ids ?? []);
  for (const r of barrierReports) {
    const button = el("button", { type: "button", class: `barrier-pin-btn${onRoute.has(r.id) ? " on-route" : ""}` });
    button.append(barrierPinElement(r.status));
    button.addEventListener("click", (e) => {
      e.stopPropagation();
      openBarrier(r);
    });
    // On the path it's attached to (v8), not wherever the finger let go.
    const marker = new maplibregl.Marker({ element: button }).setLngLat([r.path_lon ?? r.lon, r.path_lat ?? r.lat]).addTo(map);
    // MapLibre names markers "Map marker"; this one says what it is.
    button.setAttribute("aria-label", `${BARRIER_LABELS[r.barrier_type] ?? "Barrier"}. ${r.status_text || STATUS_LABELS[r.status] || ""}${onRoute.has(r.id) ? " On your route." : ""}`);
    pinMarkers.push(marker);
  }
}
window.renderBarrierPins = renderBarrierPins; // app.js calls this when the selected route changes

// ---------- Barrier details and "Still there?" ----------

const barrierDialog = document.getElementById("barrier-dialog");
let openReport = null;

function openBarrier(r) {
  openReport = r;
  const photo = document.getElementById("barrier-photo");
  photo.hidden = !r.photo_url;
  if (r.photo_url) {
    photo.src = r.photo_url;
    photo.alt = `Photo of the ${(BARRIER_LABELS[r.barrier_type] ?? "barrier").toLowerCase()}`;
  }
  document.getElementById("barrier-type").textContent = BARRIER_LABELS[r.barrier_type] ?? "Barrier";
  document.getElementById("barrier-desc").textContent = r.description || "";
  document.getElementById("barrier-status").textContent = r.status_text || STATUS_LABELS[r.status] || "";
  document.getElementById("vote-msg").textContent = "";
  openDialog(barrierDialog);
}

async function vote(stillThere) {
  const r = openReport;
  if (!auth.token) {
    handOff(barrierDialog);
    afterSignIn = () => {
      handOff(reportDialog);
      openBarrier(r);
    };
    openDialog(reportDialog);
    showSignIn();
    return;
  }
  const msg = document.getElementById("vote-msg");
  try {
    const updated = await api(`/api/reports/${r.id}/vote`, { method: "POST", json: { still_there: stillThere } });
    openReport = updated;
    document.getElementById("barrier-status").textContent = updated.status_text || STATUS_LABELS[updated.status] || "";
    // The reporter's own "yes" doesn't count as a confirmation (backend rule), so don't claim it did.
    const counted = updated.confirmations > r.confirmations || updated.status !== r.status;
    msg.textContent = !stillThere
      ? "Thanks. If someone else agrees, it comes off the map."
      : counted
        ? "Thanks. That confirms it for everyone."
        : "Thanks. A report can't be confirmed by the person who sent it; another student or campus staff can.";
    await refreshPins();
    if (!nav.active && state.routes.length) requestRoute();
  } catch (err) {
    msg.textContent = err.message;
  }
}
document.getElementById("vote-yes").addEventListener("click", () => vote(true));
document.getElementById("vote-no").addEventListener("click", () => vote(false));

// ---------- Facilities fix list ----------
// Signed in as facilities staff: the ordered list (order computed by code; Claude only writes the
// summary, titles and reasons), with Confirm / Not a barrier / Fixed.

const fixlistDialog = document.getElementById("fixlist-dialog");
document.getElementById("fixlist-open").addEventListener("click", () => {
  openDialog(fixlistDialog);
  loadFixlist();
});

async function loadFixlist(message = "") {
  const items = document.getElementById("fixlist-items");
  const msg = document.getElementById("fixlist-msg");
  msg.textContent = message || "Loading…";
  let data;
  try {
    data = await api("/api/facilities/fixlist");
  } catch (err) {
    msg.textContent = err.message;
    if (err.status === 401 || err.status === 403) fixlistDialog.close();
    return;
  }
  msg.textContent = message;
  document.getElementById("fixlist-ai").hidden = data.drafted_by !== "claude";
  document.getElementById("fixlist-summary").textContent = data.summary || (data.items.length ? "" : "Nothing needs attention right now.");
  items.replaceChildren(
    ...data.items.map((item) => {
      const actions = [
        ["confirm", "Confirm", "btn-confirm"],
        ["reject", "Not a barrier", "btn-reject"],
        ["fixed", "Fixed", "btn-secondary"],
      ].map(([action, label, cls]) => {
        const b = el("button", { type: "button", class: cls }, label);
        b.addEventListener("click", () => verify(item, action, label));
        return b;
      });
      const photo = item.photo_url ? el("img", { class: "fix-photo", src: item.photo_url, alt: "" }) : el("span", { class: "fix-photo empty", "aria-hidden": "true" });
      return el(
        "li",
        { class: "fix-item" },
        el("div", { class: "fix-top" },
          photo,
          el("div", { class: "fix-text" },
            el("p", { class: "fix-title" }, el("span", { class: "fix-rank" }, String(item.priority)), item.title),
            el("p", { class: "fix-why" }, item.why),
            el("p", { class: "fix-meta" }, [item.near && `Near ${item.near}`, fmtAgo(item.reported_at), STATUS_LABELS[item.status]].filter(Boolean).join(" · ")),
          ),
        ),
        el("div", { class: "fix-actions", role: "group", "aria-label": `Decision for ${item.title}` }, ...actions),
      );
    }),
  );
}

async function verify(item, action, label) {
  try {
    await api(`/api/reports/${item.report_id}/verify`, { method: "POST", json: { action } });
    await loadFixlist(`Marked "${label}": ${item.title}.`);
    refreshPins();
    if (!nav.active && state.routes.length) requestRoute();
  } catch (err) {
    document.getElementById("fixlist-msg").textContent = err.message;
  }
}

function fmtAgo(iso) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (!Number.isFinite(minutes)) return "";
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)} h ago`;
  return new Date(iso).toLocaleDateString();
}

// ---------- Start ----------

renderAccount();
refreshPins();
setInterval(refreshPins, 60000); // other people's reports and votes
