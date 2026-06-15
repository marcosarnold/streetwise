const CHICAGO_CENTER = [41.8781, -87.6298];

const map = L.map("map").setView(CHICAGO_CENTER, 11);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

// event id -> Leaflet marker
const markers = new Map();

// Impact levels (low | moderate | high) drive marker color. The extraction
// pipeline doesn't currently populate impact_roads/transit/pedestrian, so
// fall back to a heuristic based on event_type.
const EVENT_TYPE_IMPACT = {
  accident: "high",
  police_activity: "high",
  weather_impact: "high",
  transit_disruption: "moderate",
  construction: "moderate",
  civic_event: "moderate",
  other: "low",
};

const IMPACT_COLOR = {
  high: "#d33",
  moderate: "#f0ad4e",
  low: "#999",
};

function getImpactLevel(event) {
  return (
    event.impact_roads ||
    event.impact_transit ||
    event.impact_pedestrian ||
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
  const sources = (event.sources || []).join(", ");
  const detected = new Date(event.detected_at).toLocaleString();

  return `
    <div class="event-popup">
      <h3>${event.event_type.replace(/_/g, " ")}</h3>
      <p>${event.summary}</p>
      <table>
        <tr><td>Impact (roads)</td><td>${event.impact_roads || "—"}</td></tr>
        <tr><td>Impact (transit)</td><td>${event.impact_transit || "—"}</td></tr>
        <tr><td>Impact (pedestrian)</td><td>${event.impact_pedestrian || "—"}</td></tr>
        <tr><td>Confidence</td><td>${event.confidence.toFixed(2)}</td></tr>
        <tr><td>Sources</td><td>${sources}</td></tr>
        <tr><td>Detected</td><td>${detected}</td></tr>
      </table>
    </div>
  `;
}

function upsertMarker(event) {
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
