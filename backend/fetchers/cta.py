"""CTA Alerts XML fetcher."""

import xml.etree.ElementTree as ET

import httpx

FEED_URL = "https://www.transitchicago.com/api/1.0/alerts.aspx?outputType=XML"


def fetch_cta_alerts() -> list[dict]:
    """Fetch and parse the CTA alerts feed into a list of dicts."""
    response = httpx.get(FEED_URL, timeout=10)
    response.raise_for_status()
    return parse_cta_alerts(response.text)


def parse_cta_alerts(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)

    alerts = []
    for alert in root.findall("Alert"):
        service_ids = [
            service.findtext("ServiceId", "")
            for service in alert.findall("ImpactedService/Service")
        ]

        alerts.append({
            "alert_id": alert.findtext("AlertId", ""),
            "headline": alert.findtext("Headline", ""),
            "short_description": alert.findtext("ShortDescription", ""),
            "service_id": ", ".join(filter(None, service_ids)),
            "impact": alert.findtext("Impact", ""),
            "event_start": alert.findtext("EventStart", ""),
            "event_end": alert.findtext("EventEnd", ""),
        })

    return alerts


if __name__ == "__main__":
    for alert in fetch_cta_alerts():
        print(alert)
