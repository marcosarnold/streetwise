# Streetwise — Roadmap

## Phase 1: MVP Validation (current)
Goal: verify the extraction pipeline is accurate enough to be useful.
See [prd.md](./prd.md) for success criteria and [dev-plan.md](./dev-plan.md) for build steps.

Build is complete; validation against the PRD's success criteria (Section 1.2) is the
remaining work for this phase, pending the follow-ups in
[dev-plan.md](./dev-plan.md#known-gaps--follow-ups).

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

- **2026-06-15**: Metra's RSS feed (`metrarail.com/rss/alerts`, per the original spec) no
  longer resolves. `fetchers/metra.py` adapts to Metra's current per-line AJAX endpoint
  instead. See [architecture.md](./architecture.md#metra-service-alerts).
