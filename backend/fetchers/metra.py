"""Metra service alerts fetcher.

Note: metrarail.com/rss/alerts (the feed URL in the original spec) no longer
resolves -- metrarail.com now redirects to metra.com, which serves alerts via
a per-line AJAX endpoint instead of RSS. This fetcher adapts to that endpoint
while still producing the guid/title/description/pubDate/link fields the
extractor expects.
"""

import re
from datetime import datetime, timezone
from html import unescape

import httpx

BASE_URL = "https://www.metra.com"
SYSTEM_ALERTS_URL = f"{BASE_URL}/service_alerts/update"
LINE_ALERTS_URL = f"{BASE_URL}/service_alerts/modal/{{line}}"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_LINE_RE = re.compile(r'href="/service_alerts/modal/([A-Za-z0-9-]+)"')

_ALERT_RE = re.compile(
    r'data-alert-id="([^"]+)"\s+data-last-updated="(\d+)">\s*'
    r'<h3 class="service-alert-title">\s*(.*?)\s*</h3>\s*'
    r'<div[^>]*class="service-alert-message">\s*(.*?)\s*</div>',
    re.DOTALL,
)


def fetch_metra_alerts() -> list[dict]:
    """Fetch and parse Metra service alerts across all affected lines."""
    lines = _get_lines_with_alerts()

    alerts = []
    for line in lines:
        alerts.extend(_fetch_line_alerts(line))
    return alerts


def _get(url: str) -> dict:
    response = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
    response.raise_for_status()
    return response.json()


def _get_lines_with_alerts() -> list[str]:
    data = _get(SYSTEM_ALERTS_URL)
    html = data[0]["data"]
    return _LINE_RE.findall(html)


def _fetch_line_alerts(line: str) -> list[dict]:
    data = _get(LINE_ALERTS_URL.format(line=line))
    html = data[0]["data"]

    alerts = []
    for guid, last_updated_ms, title, description in _ALERT_RE.findall(html):
        pub_date = datetime.fromtimestamp(
            int(last_updated_ms) / 1000, tz=timezone.utc
        ).isoformat()

        alerts.append({
            "guid": guid,
            "title": _clean_html(title),
            "description": _clean_html(description),
            "pubDate": pub_date,
            "link": f"{BASE_URL}/service_alerts/modal/{line}",
            # Which line's modal served this alert — a deterministic `lines` hint for
            # the extractor. System-wide alerts repeat across modals; guid dedup keeps
            # the first occurrence.
            "line": line,
        })

    return alerts


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


if __name__ == "__main__":
    for alert in fetch_metra_alerts():
        print(alert)
