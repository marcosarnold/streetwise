# Streetwise — Product Requirements Document

## Summary
Streetwise is a real-time mobility intelligence platform for Chicagoland. It explains what city
events (accidents, construction, transit disruptions, police activity, civic events, weather
impacts) are affecting movement across roads, transit, and public spaces.

The MVP is a **solo validation tool**: a working pipeline + minimal map UI used to verify that
event extraction is accurate and useful before any investment in production infra or a polished UI.

## Core Question Being Validated
Given raw feeds from CTA, Metra, and Reddit — can the pipeline reliably extract structured,
geocoded, confidence-scored mobility events with enough accuracy to be useful?

## Success Criteria (MVP)
- Events appear on the map within 5–10 minutes of occurring.
- Geocoding resolves to correct Chicago neighborhoods and corridors.
- Confidence scores correctly filter noise (low-signal Reddit posts dropped).
- CTA and Metra official alerts score ≥ 0.6 consistently.
- Corroborated events are visually distinct from unverified ones.

## Geography
Chicago proper + inner suburbs.

## Non-Goals (MVP)
- No authentication / multi-user support.
- No production infrastructure (SQLite is fine, no Postgres).
- No verification agent beyond simple proximity + time corroboration.
- No build step or framework on the frontend.
- No bidirectional comms (SSE, not WebSockets).

## Out of Scope for Later Consideration
See [roadmap.md](./roadmap.md) for what comes after MVP validation succeeds.
