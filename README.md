# Streetwise

Real-Time Mobility Intelligence — Chicagoland

Streetwise is a solo validation tool for a real-time mobility intelligence pipeline. It pulls
raw feeds from CTA, Metra, and Reddit, extracts structured mobility events with Claude, geocodes
and confidence-scores them, and displays them on a live map.

## Status
Not yet started. Planning complete — see [docs/dev-plan.md](docs/dev-plan.md) for the build sequence.

## MVP Goal
Verify that the event extraction pipeline produces accurate, well-structured, geocoded,
confidence-scored mobility events — accurate enough to be useful before investing in a
polished UI or production infrastructure.

## Docs
- [Product Requirements (PRD)](docs/prd.md)
- [Architecture](docs/architecture.md)
- [Development Plan](docs/dev-plan.md)
- [Roadmap](docs/roadmap.md)
