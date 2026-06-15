"""Claude API call + JSON parsing for mobility event extraction."""

import json
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024

SYSTEM_PROMPT = """You are a mobility event extractor for the Chicagoland area. You receive raw
text from transit alerts, RSS feeds, or Reddit posts and return structured
mobility events as a JSON array.

For each mobility event detected, return an object with these exact fields:
 - event_type: one of [accident, construction, transit_disruption, police_activity,
 civic_event, weather_impact, other]
 - location_string: a specific, geocodable address or intersection in Chicago.
 Example: "I-90 westbound near Cicero Ave, Chicago, IL"
 Do NOT return vague strings like "downtown" or "north side".
 - summary: one sentence describing the event and its mobility impact.
 - estimated_duration: short | hours | ongoing | unknown
 - extraction_confidence: high | medium | low
 high = clear event, specific location, unambiguous impact
 medium = event likely but location or impact uncertain
 low = vague or speculative, minimal mobility signal
 - source_id: the id or guid of the source item

Return ONLY a JSON array. No explanation, no markdown, no preamble.
If no mobility events are detected, return an empty array: []
Drop posts that are questions, opinions, or historical references with no
current mobility impact."""

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def extract_events(items: list[dict]) -> list[dict]:
    """Send a batch of raw source items to Claude and return structured events."""
    if not items:
        return []

    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(items)}],
    )

    text = response.content[0].text.strip()
    text = _strip_code_fence(text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Output was likely truncated by max_tokens. Salvage complete objects.
        return _recover_truncated_array(text)


def _recover_truncated_array(text: str) -> list[dict]:
    last_brace = text.rfind("}")
    if last_brace == -1:
        return []
    return json.loads(text[: last_brace + 1] + "]")


def _strip_code_fence(text: str) -> str:
    """Strip a leading/trailing markdown code fence, if present."""
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: -3]
    return text.strip()


if __name__ == "__main__":
    from backend.fetchers.cta import fetch_cta_alerts

    alerts = fetch_cta_alerts()[:5]
    batch = [
        {
            "id": a["alert_id"],
            "headline": a["headline"],
            "description": a["short_description"],
            "impact": a["impact"],
            "event_start": a["event_start"],
            "event_end": a["event_end"],
        }
        for a in alerts
    ]

    events = extract_events(batch)
    for event in events:
        print(event)
