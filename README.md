# Streetwise

Real-Time Mobility Intelligence — Chicagoland

Streetwise is a solo validation tool for a real-time mobility intelligence pipeline. It pulls
raw feeds from CTA, Metra, and Reddit, extracts structured mobility events with Claude, geocodes
and confidence-scores them, and displays them on a live map.

## Status
MVP build complete — all 10 steps in [docs/dev-plan.md](docs/dev-plan.md) are done. See that
doc's "Known gaps / follow-ups" section for what's left before this is fully validated
(Reddit credentials, live geocoding verification).

## MVP Goal
Verify that the event extraction pipeline produces accurate, well-structured, geocoded,
confidence-scored mobility events — accurate enough to be useful before investing in a
polished UI or production infrastructure.

## Running the Server

```bash
# one-time setup
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY (and REDDIT_* once available)

# run
./venv/bin/uvicorn backend.main:app --reload
```

Then open http://127.0.0.1:8000/ — the Leaflet map is served directly by FastAPI, and the
pipeline polls CTA/Metra/Reddit every 5 minutes (first cycle runs immediately on startup).

## Docs
- [Product Requirements (PRD)](docs/prd.md)
- [Architecture](docs/architecture.md)
- [Development Plan](docs/dev-plan.md)
- [Roadmap](docs/roadmap.md)
