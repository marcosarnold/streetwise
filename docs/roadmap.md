# Streetwise — Roadmap

## Phase 1: MVP Validation (current)
Goal: verify the extraction pipeline is accurate enough to be useful.
See [prd.md](./prd.md) for success criteria and [dev-plan.md](./dev-plan.md) for build steps.

## Phase 2: Hardening (post-validation, if MVP succeeds)
- Add authentication to API endpoints before any public exposure.
- Swap SQLite for Postgres if moving to multi-user/multi-process.
- Revisit corroboration logic if false positives are high — consider a verification agent.
- Evaluate geocoding accuracy; switch to Google Maps if Nominatim is a blocker.

## Phase 3: Expansion (future)
- Broaden geography beyond Chicago proper + inner suburbs.
- Additional data sources beyond CTA, Metra, Reddit.
- Polished UI / framework-based frontend if vanilla JS becomes limiting.
- WebSockets if bidirectional communication becomes necessary.

## Decision Log
Track major pivots here as they happen, with date and rationale.
