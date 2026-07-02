# Streetwise

**Is my line okay?** Live, cross-checked status for Chicago transit.

Streetwise watches CTA and Metra the way a seasoned commuter would: it reads official
alerts the moment they post, listens to what riders are saying on the street (Reddit),
cross-checks the two, and turns the mess into one honest verdict per line — plus a map of
exactly what's broken, where, since when, and whether it's getting better.

The product stands on the official feeds alone: readable verdicts, honest verification
states, and detected clearances are already a better surface than the agencies' own alert
pages. On top of that sits the bet: **the street usually knows before the agency does**,
and nobody measures the gap. Streetwise runs continuously, timestamps every signal, and
accumulates two datasets no one else has — how far ahead of official alerts rider reports
run, and how long disruptions *actually* last (agencies announce starts; they almost
never announce ends). The live product answers "is my line okay right now?"; the archive
becomes the record of how the system really performs.

## Product rules

Five rules govern every surface. They exist because a disruption product lives or dies on
trust, and they are non-negotiable:

1. **Verdict first.** The product answers the question before it shows the data.
2. **Never render a guess as a fact.** No map pin without a verified location. Unverified
   reports look unverified. States are named in words — *Reported / Confirmed / Cleared* —
   never as raw scores.
3. **Time is always visible.** Every event says how old it is; stale things look stale;
   cleared things say how long they lasted.
4. **The failure state is designed.** "All clear" is a celebrated state, not a blank map.
   A down feed is named, not hidden.
5. **Nothing is deleted.** Events leave the live view; they never leave the record. The
   archive is the moat.

## Status

The MVP pipeline is built and running end-to-end (CTA + Metra + Reddit → Claude
extraction → geocode → score → SQLite → SSE → Leaflet). The project pivoted on
2026-07-01 from a broad "mobility dashboard" to the transit-first product defined in
[docs/prd.md](docs/prd.md) — see the [decision log](docs/roadmap.md#decision-log) for the
full rationale. [docs/dev-plan.md](docs/dev-plan.md) is the exact build sequence from
here; Phase 0 (truthfulness fixes to the existing pipeline) is next.

## Running the server

```bash
# one-time setup
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY (and REDDIT_* once available)

# run
./venv/bin/uvicorn backend.main:app --reload
```

Open http://127.0.0.1:8000/ — FastAPI serves the Leaflet frontend directly, and the
pipeline polls CTA/Metra/Reddit every 5 minutes (first cycle runs immediately on startup).

## Docs

- [Product Requirements (PRD)](docs/prd.md) — the question, the user, the principles
- [Architecture](docs/architecture.md) — pipeline, event lifecycle, data model, gotchas
- [Development Plan](docs/dev-plan.md) — build sequence with "done when" criteria
- [Roadmap](docs/roadmap.md) — phases, the moat strategy, and the decision log
