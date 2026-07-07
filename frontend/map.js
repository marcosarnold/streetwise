// Streetwise frontend v2 — verdict board + verdict-first event sheet + honest chrome.
// The data layer (fetch /events, /lines, /status, SSE stream) is the same contract as
// v1; only rendering changed. All event-derived text is set via textContent — the
// pipeline is trusted, but the renderer shouldn't have to assume that.

const CHICAGO_CENTER = [41.8781, -87.6298];

const map = L.map("map", { zoomControl: false }).setView(CHICAGO_CENTER, 11);
L.control.zoom({ position: "bottomright" }).addTo(map);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

const markers = new Map();      // event id -> Leaflet marker
const eventCache = new Map();   // event id -> latest event dict (feeds sheet + empty state)
let lineColors = {};            // line id -> official color (from /lines)
let lastPollAt = null;

// network base layer: line id -> {casing, line} polylines; casing carries the state
const networkLayers = new Map();
let lineStates = new Map();     // line id -> "good" | "warn" | "down"
map.createPane("network").style.zIndex = 350;      // under markers, over tiles
map.createPane("netcasing").style.zIndex = 340;
map.createPane("stations").style.zIndex = 360;

const $ = (id) => document.getElementById(id);

// ---- state mapping: words and colors derive from severity/scope, never scores ----

const STATE_COLOR = { good: "#1f9e5a", warn: "#e08a1e", down: "#c1121f", muted: "#8c8678" };

function eventState(event) {
  if (event.scope !== "acute") return "muted";
  if (event.severity === "severe") return "down";
  return "warn"; // major, minor, or unset acute — something is happening
}

function eventHeadline(event) {
  if (event.scope === "acute") {
    const bySeverity = { severe: "Service disruption", major: "Major delays", minor: "Minor delays" };
    if (bySeverity[event.severity]) return bySeverity[event.severity];
  }
  // chronic/planned (and unset-severity acute): the event type, humanized
  return (event.event_type || "service note").replace(/_/g, " ");
}

// ---- network base layer: the system drawn in its own colors, state as treatment ----
// The line always keeps its identity color (repainting the Red Line amber would
// confuse riders); degradation shows as a thicker stroke over a state-colored casing.

const LINE_STYLE = {
  good: { line: { weight: 3, opacity: 0.55 }, casing: { opacity: 0 } },
  warn: { line: { weight: 4, opacity: 1 }, casing: { color: STATE_COLOR.warn, weight: 10, opacity: 0.85 } },
  down: { line: { weight: 4, opacity: 1 }, casing: { color: STATE_COLOR.down, weight: 10, opacity: 0.85 } },
};

async function loadNetwork() {
  const geo = await (await fetch("/lines.geojson")).json();
  for (const feature of geo.features) {
    const id = feature.properties.id;
    const casing = L.geoJSON(feature, {
      pane: "netcasing",
      style: { color: STATE_COLOR.warn, weight: 10, opacity: 0, lineCap: "round" },
    }).addTo(map);
    const line = L.geoJSON(feature, {
      pane: "network",
      style: { color: lineColors[id] || STATE_COLOR.muted, weight: 3, opacity: 0.55, lineCap: "round" },
    }).addTo(map);
    line.on("click", () => openLineEvent(id));
    casing.on("click", () => openLineEvent(id));
    networkLayers.set(id, { casing, line });
  }
  applyNetworkState();
}

function applyNetworkState() {
  for (const [id, layers] of networkLayers) {
    const style = LINE_STYLE[lineStates.get(id)] || LINE_STYLE.good;
    layers.line.setStyle({ color: lineColors[id] || STATE_COLOR.muted, ...style.line });
    layers.casing.setStyle(style.casing);
  }
}

// clicking a line opens its worst active event — the home for line-level events
// (geo_kind=line) that have no point to pin
function openLineEvent(lineId) {
  const rank = { severe: 3, major: 2, minor: 1 };
  const candidates = [...eventCache.values()]
    .filter((e) => (e.lines || []).includes(lineId))
    .sort((a, b) =>
      (b.scope === "acute") - (a.scope === "acute") ||
      (rank[b.severity] || 0) - (rank[a.severity] || 0));
  if (candidates.length) openSheet(candidates[0]);
}

// ---- station dots: gazetteer geometry, zoom-gated so the overview stays calm ----

const STATION_MIN_ZOOM = 13;
const stationLayer = L.layerGroup();

async function loadStations() {
  const gaz = await (await fetch("/gazetteer.json")).json();
  for (const st of gaz.stations) {
    const dot = L.circleMarker([st.lat, st.lng], {
      pane: "stations",
      radius: 3.5, color: "#17150f", weight: 1.5, fillColor: "#fffdf6", fillOpacity: 1,
    });
    dot.bindTooltip(`${st.name}${st.routes.length ? " · " + st.routes.join(", ") : ""}`, { direction: "top" });
    stationLayer.addLayer(dot);
  }
  syncStationVisibility();
}

function syncStationVisibility() {
  const show = map.getZoom() >= STATION_MIN_ZOOM;
  if (show && !map.hasLayer(stationLayer)) map.addLayer(stationLayer);
  if (!show && map.hasLayer(stationLayer)) map.removeLayer(stationLayer);
}
map.on("zoomend", syncStationVisibility);

// ---- verdict board ----

async function refreshBoard() {
  const rows = await (await fetch("/lines")).json();
  lineColors = Object.fromEntries(rows.map((r) => [r.id, r.color]));
  lineStates = new Map(rows.map((r) => [r.id, r.state]));
  applyNetworkState();

  const board = $("board");
  board.replaceChildren();
  let problemCount = 0;

  for (const [agency, label] of [["cta", "CTA ‘L’"], ["metra", "Metra"]]) {
    const lines = rows.filter((r) => r.agency === agency);
    const problems = lines.filter((r) => r.state !== "good");
    problemCount += problems.length;

    const h = document.createElement("h2");
    h.textContent = label;
    board.appendChild(h);

    for (const line of problems) board.appendChild(lineRow(line));

    // all-good lines collapse to one quiet row — a wall of identical "GOOD SERVICE"
    // rows buries the answer the board exists to give
    const good = lines.filter((r) => r.state === "good");
    if (good.length) {
      const row = document.createElement("div");
      row.className = "allgoodrow";
      const pills = document.createElement("span");
      pills.className = "pills";
      for (const line of good.slice(0, 11)) {
        const i = document.createElement("i");
        i.style.background = line.color || STATE_COLOR.muted;
        pills.appendChild(i);
      }
      const text = document.createElement("span");
      text.textContent = problems.length
        ? `${good.length} lines · Good service`
        : "All lines · Good service";
      row.append(pills, text);
      board.appendChild(row);
    }
  }

  const count = $("board-count");
  count.textContent = problemCount;
  count.classList.toggle("zero", problemCount === 0);
  refreshEmptyState();
}

function lineRow(line) {
  const row = document.createElement("div");
  row.className = "linerow";
  const pill = document.createElement("span");
  pill.className = "pill";
  if (line.color) pill.style.background = line.color;
  const name = document.createElement("span");
  name.className = "lname";
  name.textContent = line.name;
  const verdict = document.createElement("span");
  verdict.className = `lv ${line.state}`;
  verdict.textContent = line.verdict;
  row.append(pill, name, verdict);
  return row;
}

$("board-toggle").addEventListener("click", () => {
  const open = $("board").classList.toggle("open");
  $("board-toggle").setAttribute("aria-expanded", String(open));
});

// ---- event sheet (verdict-first card; replaces popups) ----

function openSheet(event) {
  const kicker = $("sheet-kicker");
  kicker.replaceChildren();
  for (const lineId of event.lines || []) {
    const pill = document.createElement("span");
    pill.className = "pill";
    pill.style.background = lineColors[lineId] || STATE_COLOR.muted;
    pill.title = lineId;
    kicker.appendChild(pill);
  }
  const where = document.createElement("span");
  where.textContent = [
    (event.lines || []).join(" · "),
    event.station || event.location_name || "",
  ].filter(Boolean).join(" — ");
  kicker.appendChild(where);

  const head = $("sheet-head");
  head.textContent = eventHeadline(event);
  head.className = `head ${event.scope === "acute" ? eventState(event) : "muted"}`;

  $("sheet-summary").textContent = event.summary || "";

  const meta = $("sheet-meta");
  meta.replaceChildren();
  meta.appendChild(chip(
    event.verification === "confirmed" ? "Confirmed" : "Reported",
    event.verification === "confirmed" ? "verified" : "reported", true));
  if (event.scope !== "acute") meta.appendChild(chip(event.scope, "", false));
  for (const s of event.sources || []) {
    const label = { cta: "CTA official", metra: "Metra official", reddit: "Rider report" }[s.type] || s.type;
    meta.appendChild(chip(label, "", false));
  }
  if (event.age_minutes != null) meta.appendChild(chip(formatAge(event.age_minutes), "", false));

  $("sheet").classList.add("open");
}

function chip(text, extraClass, withDot) {
  const c = document.createElement("span");
  c.className = `chip ${extraClass}`.trim();
  if (withDot) {
    const d = document.createElement("i");
    d.className = "d";
    c.appendChild(d);
  }
  c.appendChild(document.createTextNode(text));
  return c;
}

function formatAge(minutes) {
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const h = Math.floor(minutes / 60);
  return `${h} h ${minutes % 60} min ago`;
}

function closeSheet() {
  $("sheet").classList.remove("open");
}
$("sheet-close").addEventListener("click", closeSheet);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

// ---- markers ----

function createIcon(event, fresh = false) {
  const classes = ["marker-dot"];
  if (event.scope !== "acute") classes.push("chronic");
  if (event.verification !== "confirmed") classes.push("reported");
  if (fresh) classes.push("fresh");
  const color = STATE_COLOR[eventState(event)];
  return L.divIcon({
    className: "event-marker",
    html: `<div class="${classes.join(" ")}" style="background:${color};border-color:${event.verification === "confirmed" ? "#17150f" : color}"></div>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10],
  });
}

function upsertMarker(event, fresh = false) {
  eventCache.set(event.id, event);
  refreshEmptyState();

  // No pin without a verified place: geo_kind=none events carry no coordinates —
  // they exist on the board and in the sheet, never as a fabricated point.
  if (event.lat == null || event.lng == null) return;

  const existing = markers.get(event.id);
  const latlng = [event.lat, event.lng];
  if (existing) {
    existing.setLatLng(latlng);
    existing.setIcon(createIcon(event));
  } else {
    const marker = L.marker(latlng, { icon: createIcon(event, fresh) }).addTo(map);
    marker.on("click", () => openSheet(eventCache.get(event.id)));
    markers.set(event.id, marker);
  }
}

function removeEvent(eventId) {
  eventCache.delete(eventId);
  const marker = markers.get(eventId);
  if (marker) {
    map.removeLayer(marker);
    markers.delete(eventId);
  }
  refreshEmptyState();
}

// ---- designed empty state ----

function refreshEmptyState() {
  const acute = [...eventCache.values()].filter((e) => e.scope === "acute").length;
  $("allclear").hidden = acute > 0;
  if (acute === 0 && lastPollAt) {
    $("allclear-sub").textContent = `No active disruptions · checked ${relTime(lastPollAt)}`;
  }
}

// ---- data flow (contract unchanged from v1) ----

async function loadInitialEvents() {
  const events = await (await fetch("/events")).json();
  events.forEach((e) => upsertMarker(e));
}

function subscribeToStream() {
  const source = new EventSource("/events/stream");
  source.onmessage = (e) => {
    const message = JSON.parse(e.data);
    if (message.type === "new_event" || message.type === "update_event") {
      upsertMarker(message.event, message.type === "new_event");
      refreshBoard();
    } else if (message.type === "clear_event" || message.type === "remove_event") {
      // Both endings drop the marker. Distinct paths on purpose: clear_event carries
      // a full event with cleared_at for the future "cleared · lasted N min" fade.
      removeEvent(message.event.id);
      refreshBoard();
    } else if (message.type === "ping") {
      beat();
      refreshStatus();
    }
  };
  source.onopen = beat;
  source.onerror = () => $("beacon").classList.add("stale"); // EventSource auto-reconnects
}

// the beacon breathes only on real heartbeats — liveness shown, never simulated
function beat() {
  const beacon = $("beacon");
  beacon.classList.remove("stale", "pulse");
  void beacon.offsetWidth; // restart the animation
  beacon.classList.add("pulse");
}

function relTime(iso) {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  return minutes < 1 ? "just now" : `${minutes} min ago`;
}

async function refreshStatus() {
  try {
    const status = await (await fetch("/status")).json();
    lastPollAt = status.last_poll_at;
    renderStatusTime();

    // A source absent from the response is dormant (deliberately unconfigured) —
    // hidden entirely. A permanently grey dot would read as "broken".
    document.querySelectorAll(".source-dot").forEach((dot) => {
      dot.style.display = dot.dataset.source in status.sources_healthy ? "" : "none";
    });
    for (const [source, healthy] of Object.entries(status.sources_healthy)) {
      const dot = document.querySelector(`.source-dot[data-source="${source}"]`);
      if (!dot) continue;
      dot.classList.remove("healthy", "unhealthy");
      if (healthy === true) dot.classList.add("healthy");
      if (healthy === false) dot.classList.add("unhealthy");
    }
  } catch (err) {
    console.error("Failed to refresh status", err);
  }
}

function renderStatusTime() {
  $("status-time").textContent = lastPollAt
    ? `checked ${relTime(lastPollAt)}`
    : "waiting for first poll…";
  refreshEmptyState();
}
setInterval(renderStatusTime, 30_000); // "2 min ago" must not silently rot

loadInitialEvents();
refreshBoard().then(loadNetwork); // network styling needs the colors /lines carries
loadStations();
subscribeToStream();
refreshStatus();
