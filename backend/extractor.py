"""Claude extraction: raw source items -> structured transit disruption events.

Prompt v2 (dev-plan 0.5; the verbatim spec lives in docs/architecture.md — keep them in
sync). Model output is untrusted input: _sanitize_event coerces every enum to its known
set before anything enters the pipeline. Two defaults are deliberate: unknown scope ->
acute (hiding a real disruption is worse than a noisy verdict row) and unknown
extraction_confidence -> low (unearned certainty is the one thing we never grant).
"""

import json
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2048

SYSTEM_PROMPT = """You are a transit disruption extractor for Chicagoland. You receive a JSON object
{"source": "cta" | "metra" | "reddit", "items": [...]} — raw items from CTA alerts,
Metra alerts, or Reddit posts — and return structured disruption events as a JSON array.

Input hints: CTA items carry "routes" (the agency's affected route ids) and "impact"
(the agency's own classification, e.g. "Planned Work", "Elevator Status" — trust it
for scope). Metra items carry "line" (the line's site slug).

For each current, real-world disruption, return an object with these exact fields:
 - event_type: one of [delay, suspension, reroute, station_issue, accessibility,
   crowding, incident, construction, weather_impact, other]
 - mode: one of [cta_rail, cta_bus, metra, road, other]
 - lines: array of affected route identifiers, using official names exactly:
   CTA rail: "Red","Blue","Brown","Green","Orange","Pink","Purple","Yellow"
   CTA bus: the route number as a string, e.g. "66","22"
   Metra: the line code, e.g. "UP-N","BNSF","MD-W"
   Empty array if no specific line is identifiable.
 - station: the official station/stop name if the event is anchored to one, else null.
   Use the agency's name ("Jefferson Park", "Clybourn"), not a paraphrase.
 - location_string: a specific geocodable address or intersection ONLY for events not
   anchored to a station or line (e.g. a crash on a street). Null otherwise.
   Never return vague strings like "downtown" or "north side".
 - severity: minor (residual delays, minor reroute) |
   major (significant delays, partial suspension, station closure) |
   severe (line suspension, derailment, service stopped)
 - scope: acute (happening now, unexpected — delays, incidents, suspensions) |
   chronic (long-running conditions — elevator/escalator outages, accessibility
   notices, construction lasting weeks) |
   planned (scheduled work announced in advance)
 - summary: one plain-language sentence, present tense, that a rider on a platform
   would find useful. Name the line and the consequence. No agency jargon.
 - is_clearance: true if this item announces that a disruption has ENDED or service
   has resumed; false otherwise.
 - extraction_confidence: high | medium | low
   high = clear event, specific line/place, unambiguous impact
   medium = event likely but line, place, or impact uncertain
   low = vague or speculative, minimal transit signal
 - source_id: the id or guid of the source item

Return ONLY a JSON array. No explanation, no markdown, no preamble.
If no disruption events are detected, return an empty array: []
Drop items that are questions, opinions, or historical references with no current impact."""

EVENT_TYPES = {"delay", "suspension", "reroute", "station_issue", "accessibility",
               "crowding", "incident", "construction", "weather_impact", "other"}
MODES = {"cta_rail", "cta_bus", "metra", "road", "other"}
SEVERITIES = {"minor", "major", "severe"}
SCOPES = {"acute", "chronic", "planned"}
CONFIDENCES = {"high", "medium", "low"}
MAX_LINES = 8  # a real event names a handful of routes; more is model noise

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def extract_events(items: list[dict], source_type: str | None = None) -> list[dict]:
    """Send a batch of raw source items to Claude; return sanitized structured events."""
    if not items:
        return []

    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user",
                   "content": json.dumps({"source": source_type, "items": items})}],
    )

    text = _strip_code_fence(response.content[0].text.strip())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Output was likely truncated by max_tokens. Salvage complete objects.
        parsed = _recover_truncated_array(text)

    if not isinstance(parsed, list):
        return []
    events = (_sanitize_event(e) for e in parsed)
    return [e for e in events if e is not None]


def _sanitize_event(raw) -> dict | None:
    """Coerce one model-emitted event to the schema contract, or reject it.

    Rejection (None) only for events we cannot use at all: no source_id (can't route
    or archive) or no summary (nothing to show). Everything else coerces to a safe
    default rather than dropping a real disruption over a malformed enum.
    """
    if not isinstance(raw, dict):
        return None
    source_id = str(raw.get("source_id") or "").strip()
    summary = str(raw.get("summary") or "").strip()
    if not source_id or not summary:
        return None

    lines = raw.get("lines")
    if not isinstance(lines, list):
        lines = []
    lines = [str(x).strip() for x in lines if str(x).strip()][:MAX_LINES]

    def _text(key):
        value = str(raw.get(key) or "").strip()
        return value or None

    return {
        "source_id": source_id,
        "summary": summary,
        "event_type": raw.get("event_type") if raw.get("event_type") in EVENT_TYPES else "other",
        "mode": raw.get("mode") if raw.get("mode") in MODES else None,
        "lines": lines,
        "station": _text("station"),
        "location_string": _text("location_string"),
        "severity": raw.get("severity") if raw.get("severity") in SEVERITIES else None,
        "scope": raw.get("scope") if raw.get("scope") in SCOPES else "acute",
        "is_clearance": bool(raw.get("is_clearance")),
        "extraction_confidence": raw.get("extraction_confidence")
        if raw.get("extraction_confidence") in CONFIDENCES else "low",
    }


def _recover_truncated_array(text: str) -> list:
    last_brace = text.rfind("}")
    if last_brace == -1:
        return []
    try:
        return json.loads(text[: last_brace + 1] + "]")
    except json.JSONDecodeError:
        return []


def _strip_code_fence(text: str) -> str:
    """Strip a leading/trailing markdown code fence, if present."""
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


if __name__ == "__main__":
    from backend.fetchers.cta import fetch_cta_alerts

    alerts = fetch_cta_alerts()[:5]
    batch = [
        {
            "id": a["alert_id"],
            "headline": a["headline"],
            "description": a["short_description"],
            "routes": a["service_id"],
            "impact": a["impact"],
            "event_start": a["event_start"],
            "event_end": a["event_end"],
        }
        for a in alerts
    ]

    for event in extract_events(batch, "cta"):
        print(event)
