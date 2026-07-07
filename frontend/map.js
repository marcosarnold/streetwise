const CHICAGO_CENTER = [41.8781, -87.6298];

const map = L.map("map").setView(CHICAGO_CENTER, 11);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

// event id -> Leaflet marker
const markers = new Map();

// Marker color: severity once the v2 extractor populates it (dev-plan 0.5);
// until then an event_type heuristic — a documented stopgap, not product truth.
const EVENT_TYPE_IMPACT = {
  accident: "high",
  police_activity: "high",
  weather_impact: "high",
  transit_disruption: "moderate",
  construction: "moderate",
  civic_event: "moderate",
  other: "low",
};

const SEVERITY_IMPACT = { severe: "high", major: "moderate", minor: "low" };

const IMPACT_COLOR = {
  high: "#d33",
  moderate: "#f0ad4e",
  low: "#999",
};

function getImpactLevel(event) {
  return (
    SEVERITY_IMPACT[event.severity] ||
    EVENT_TYPE_IMPACT[event.event_type] ||
    "low"
  );
}

function getOpacity(confidence) {
  return confidence >= 0.6 ? 1.0 : 0.4;
}

function createIcon(event) {
  const color = IMPACT_COLOR[getImpactLevel(event)] || IMPACT_COLOR.low;
  const opacity = getOpacity(event.confidence);
  const dimmed = opacity < 1.0;

  const badge = dimmed ? '<div class="marker-badge">?</div>' : "";

  return L.divIcon({
    className: "event-marker",
    html: `<div class="marker-dot" style="background:${color}; opacity:${opacity}"></div>${badge}`,
    iconSize: [20, 20],
    iconAnchor: [10, 10],
    popupAnchor: [0, -10],
  });
}

function popupContent(event) {
  // Sources are typed records: [{type: "cta", id: "..."}].
  const sources = (event.sources || []).map((s) => s.type).join(" + ");
  const detected = new Date(event.detected_at).toLocaleString();

  return `
    <div class="event-popup">
      <h3>${event.event_type.replace(/_/g, " ")}</h3>
      <p>${event.summary}</p>
      <table>
        <tr><td>Status</td><td>${event.verification || "—"}</td></tr>
        <tr><td>Sources</td><td>${sources}</td></tr>
        <tr><td>Detected</td><td>${detected}</td></tr>
      </table>
    </div>
  `;
}

function upsertMarker(event) {
  // No pin without a verified place: geo_kind=none events carry no coordinates
  // and are list-only (dev-plan 1.3) — never a fabricated point.
  if (event.lat == null || event.lng == null) return;

  const existing = markers.get(event.id);
  const latlng = [event.lat, event.lng];
  const icon = createIcon(event);
  const popup = popupContent(event);

  if (existing) {
    existing.setLatLng(latlng);
    existing.setIcon(icon);
    existing.setPopupContent(popup);
  } else {
    const marker = L.marker(latlng, { icon }).addTo(map);
    marker.bindPopup(popup);
    markers.set(event.id, marker);
  }
}

function removeMarker(eventId) {
  const marker = markers.get(eventId);
  if (marker) {
    map.removeLayer(marker);
    markers.delete(eventId);
  }
}

async function loadInitialEvents() {
  const response = await fetch("/events");
  const events = await response.json();
  events.forEach(upsertMarker);
}

function subscribeToStream() {
  const source = new EventSource("/events/stream");

  source.onmessage = (e) => {
    const message = JSON.parse(e.data);

    if (message.type === "new_event" || message.type === "update_event") {
      upsertMarker(message.event);
    } else if (message.type === "clear_event" || message.type === "remove_event") {
      // Both endings drop the marker (the v1 leak fix). Distinct paths on purpose:
      // clear_event carries a full event with cleared_at, so Phase 1.6 can render the
      // greyed "cleared · lasted N min" fade before removal; remove_event never will.
      removeMarker(message.event.id);
    } else if (message.type === "ping") {
      refreshStatus();
    }
  };

  source.onerror = () => {
    // EventSource auto-reconnects; nothing else to do.
  };
}

async function refreshStatus() {
  try {
    const response = await fetch("/status");
    const status = await response.json();

    const timeEl = document.getElementById("status-time");
    if (status.last_poll_at) {
      timeEl.textContent = `Last updated: ${new Date(status.last_poll_at).toLocaleTimeString()}`;
    } else {
      timeEl.textContent = "Last updated: pending first poll…";
    }

    // A source absent from the response is dormant (deliberately unconfigured) —
    // hide its dot entirely. A permanently grey dot would read as "broken".
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

loadInitialEvents();
subscribeToStream();
refreshStatus();
