# Streetwise — Product Requirements Document

_Rewritten 2026-07-01 after the transit-first pivot. Supersedes the "broad mobility
intelligence" framing; see [roadmap.md → Decision Log](./roadmap.md#decision-log)._

## The question

> **"Is my line okay right now?"**

Every Streetwise surface is either the answer to that question or evidence for it: a
one-word verdict per CTA and Metra line, what's wrong when it isn't good, whether that's
confirmed or just reported, how long it's been going on, and whether it's getting better.

## Who it's for

**The Chicago transit commuter** — someone who rides the same line(s) roughly twice a day,
five days a week, and makes the same decision each time: *do I trust my usual ride, or do
I need a plan B?* Today they answer it by juggling the CTA alerts page (jargon walls),
Metra's per-line popups, Twitter/Reddit, and platform gossip. The problem is not
information scarcity — it's information shape, latency, and trust.

Explicitly **not** the MVP user: drivers (Waze and Google own road disruption with
crowdsourcing scale we cannot match, and "is my drive okay?" has no verdict without route
input) and the general "what's happening in the city" browser (curiosity produces weekly
visits; a commute decision produces daily ones).

## Why transit is the wedge

1. **The reliable feeds are transit feeds.** CTA and Metra publish structured, always-on,
   free alerts; every event the pipeline has stored to date is a transit event. Reddit —
   the road/civic signal — is the noisiest source and needs corroboration to be usable at
   all. The product's face should stand on the strongest data, not the weakest.
2. **The incumbents' gap is shape, not data.** Agencies tell you what they've admitted, in
   prose designed for liability rather than decisions. Turning that into a verdict is
   exactly what an extraction pipeline is good at.
3. **A ritual, not a visit.** The commute decision recurs twice daily. That is the habit
   substrate everything in Phase 2 (alerts, watch-my-line) attaches to.
4. **A measurable story.** "The street knows before the agency — by a median of N
   minutes" is a finding we can own, publish, and be known for. It requires only that we
   run continuously and timestamp everything — which we already do.

Scope note: "transit" = **CTA rail + CTA bus + Metra**. CTA bus alerts arrive in the same
feed we already parse; excluding them is extra work to make the product worse.

**Street-level social signal is deferred out of the MVP entirely** (decision log
2026-07-02): Reddit's API is not accessible, and road/weather/civic events had no other
source. The machinery that makes unofficial signal safe — Reported vs Confirmed states,
corroboration promotion, expiry without clearance claims, latency capture — is built,
unit-tested, and credential-gated dormant; it self-activates when an accessible source
(Bluesky is the likely candidate) is wired in. Until then Streetwise is an
official-feeds product — which this PRD always required it to stand as.

## Product principles

These are the trust rules. Every design and engineering decision defers to them.

1. **Verdict first.** The line-status board is the primary surface; the map is the detail
   view. A user who reads one line of Streetwise gets an answer, not a dataset.
2. **Never render a guess as a fact.**
   - No map pin without a verified location. An event we can't place goes to a list or
     anchors to its line/station geometry — never to a fake point.
   - Verification is expressed as named states — **Reported** (single unofficial source),
     **Confirmed** (official source, or independent corroboration), plus **Cleared** as a
     lifecycle overlay — with distinct visual treatments. Numeric confidence never
     appears in the UI. (Internally, verification and lifecycle are separate axes so a
     cleared event keeps its verification history — see architecture.md.)
   - Estimates and heuristics are labeled as such wherever they surface.
3. **Time is always visible.** Every event carries "detected N min ago"; display presence
   decays with age; cleared events state their actual duration ("cleared · lasted 47 min").
   The status strip states the true polling cadence ("checked 2m ago"), never implying a
   live wire we don't have.
4. **The failure state is designed.** No disruptions = "All clear on CTA & Metra" — the
   best screen in the product, not a blank map. A down source is named in the status
   strip. A filter that empties the view says which filter did it.
5. **Nothing is deleted.** Events age out of the live view but never leave the database.
   The archive (real durations, latency gaps, per-line reliability) is the long-term moat
   and cannot be backfilled.
6. **One fact, once.** No field renders as "—". No number appears twice on one card. If
   the schema has a column the pipeline doesn't populate, the column is wrong.

## MVP definition

A working pipeline plus a verdict-shaped surface, used first by me, that I would honestly
consult before my own commute:

- **Line-status board**: every CTA rail line + Metra line with a current verdict
  (Good service / Minor / Major / Severe), derived from **active acute events only**.
  Chronic and planned items (elevator outages, ADA notices, long construction — most of
  the official feed on a quiet day) surface as a quiet "ongoing conditions" tier and
  never degrade a verdict: a board that always shows "Minor" trains riders to ignore it.
- **Live map**: events drawn at stations, along line geometry, or at verified points —
  colored by the affected line, styled by state (solid = confirmed, dashed = reported,
  faded = cleared/aging).
- **Event cards**: state badge, plain-language summary, location, freshness, provenance
  in human words ("Metra alert + 2 rider reports").
- **Honest chrome**: status strip with real poll timing and per-source health; designed
  empty state.

## Success criteria

Validation targets, measured over at least one full week of continuous operation:

| # | Criterion | Target |
|---|---|---|
| 1 | Extraction accuracy (event real + summary faithful, judged in `/review`) | ≥ 90% of official-source events |
| 2 | Location resolution for CTA/Metra events (station/line matched via gazetteer) | ≥ 90%; **zero** fabricated points rendered |
| 3 | Latency: event visible in Streetwise after source publication | ≤ 1 poll cycle (5 min) |
| 4 | State machinery: unofficial signal can never render Confirmed without independent corroboration | 100% (unit-tested; no live social source in the MVP) |
| 5 | Clearance: events marked Cleared within 2 cycles of source clearance | ≥ 80% |
| 6 | Score distribution is discriminating (not all events at one value) | distinct scores across sources/paths |
| 7 | The founder-user test | I check it before my own commute without forcing myself |
| 8 | Chronic/planned items never degrade a line verdict | 100% (verdicts derive from acute events only) |

Criterion 6 exists because the current DB holds sixteen events at exactly 0.75 — a scorer
that outputs one value is not yet measuring anything.

## Non-goals (MVP)

- No authentication / accounts (add before any public exposure).
- No production infrastructure (SQLite is fine; no Postgres, no queue).
- No push notifications yet (Phase 2 — they must be built on a pipeline already proven
  truthful, or they burn trust at 7am).
- No route planning, arrival predictions, or trip times — Transit/Citymapper own that;
  we answer *disruption*, not *navigation*.
- No frontend framework or build step.
- No geographic expansion beyond Chicagoland.

## Out of scope for later consideration

See [roadmap.md](./roadmap.md): watch-my-line alerts, per-line permalink pages, the
latency/durations data story, road-signal promotion, other cities.
