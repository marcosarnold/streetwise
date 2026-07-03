# Streetwise — Roadmap

_Rewritten 2026-07-01 for the transit-first pivot._

**North star:** the trust layer for Chicago transit — the live verdict before you leave,
the alert when your line breaks during your commute window, and over time the public
record of how the system *actually* performs versus how it's scheduled to.

**The moat is data we accumulate, not code we write.** Two datasets compound from the day
the pipeline runs truthfully, and neither can be backfilled or copied by cloning the repo:

1. **Corroboration latency** — how far ahead of official alerts the street signal runs
   ("riders knew N minutes before the agency said so"). Our headline finding, once
   statistically real.
2. **True durations** — agencies announce starts, almost never ends. Measured clear times
   ("Blue Line signal problems typically clear in ~40 min") exist nowhere else.

This is why Phase 0's "delete nothing" rule outranks every feature.

## Phase 0 — Truthfulness (current)

Make the existing pipeline honest before building anything on it: preserve history,
flow updates, detect clearance, resolve locations via the GTFS gazetteer (no fabricated
pins), states over scores, latency capture, and the `/review` eval gate.
See [dev-plan.md](./dev-plan.md) Phase 0. **Exit gate:** one week of continuous data
measured against every PRD success criterion.

## Phase 1 — The verdict

The product surface: design tokens on Chicago's own transit vocabulary, the per-line
verdict board, the state-styled map, human-language event cards, designed empty/failure
states, liveness motion. See [dev-plan.md](./dev-plan.md) Phase 1.
**Exit gate:** the founder-user test — I consult it before my own commute, unforced.

## Phase 2 — The daily habit (post-validation)

Ship only onto a surface that has passed both gates — an alert product built on an
untrusted pipeline burns its one chance at 7 a.m.

- **Watch my line** — the retention feature. Pick line(s) + commute windows; Web Push
  when a watched line degrades or recovers within the window. Quiet by design: state
  *changes* only, never re-pings, always says Cleared.
- **Per-line permalinks** (`/line/red`, `/line/up-n`) — server-rendered status + history
  with proper meta tags, because "Blue Line is down" texted to a group chat is our
  distribution.
- **The data story** — publish the latency finding and the durations dataset when they
  are statistically real: our equivalent of a flagship study, and the launch narrative
  (Reddit, local press, transit Twitter).
- Instrument the funnel (visit → board glance → card open → watch armed) with a
  cookieless counter consistent with a no-accounts, no-tracking posture.

## Phase 3 — Hardening & expansion (earned, not scheduled)

- Auth on write-adjacent endpoints and rate limiting **before any public exposure**.
- Postgres + multi-process only when concurrent users demand it.
- **Road/civic signal promotion** — reconsidered from data: if Phase 0–2 archives show
  road events corroborating and drawing engagement, promote them to a first-class
  surface; otherwise they remain sensors.
- Pace bus, Divvy, additional feeds (traffic cameras, 311) as sensors first.
- **Other cities** only after Chicago demonstrably works — the playbook (official feeds +
  local subreddit + GTFS gazetteer + latency/durations archive) is portable; the brand
  earns portability by being right about Chicago first.

## Decision log

Major pivots, with date and rationale.

- **2026-06-15** — Metra's RSS feed (`metrarail.com/rss/alerts`, per the original spec)
  no longer resolves. `fetchers/metra.py` adapts to Metra's current per-line AJAX
  endpoint instead. See [architecture.md](./architecture.md#metra-service-alerts).
- **2026-07-01** — **Pivot: transit-first.** Narrowed the MVP from "real-time mobility
  intelligence across roads, transit, and public space" to one question — *"Is my line
  okay right now?"* — for CTA + Metra riders. Rationale: (a) the reliable, structured,
  always-on feeds are the transit feeds, and every event stored to date is a transit
  event; (b) drivers are over-served by Waze/Google, whose crowdsourcing scale we cannot
  match, while transit riders face jargon walls and per-line popups — a shape problem an
  extraction pipeline actually fixes; (c) the commute decision recurs twice daily — the
  habit substrate alerts attach to; (d) the defensible assets (street-before-agency
  latency, true durations) are transit-native measurements. Road/weather/civic signals
  remain in the pipeline as sensors and corroborators, not as the product face. Broad
  "city mobility" returns, if ever, as a Phase 3 promotion decided from archived data.
- **2026-07-01** — **Nothing is deleted.** Abolished the 24-hour retention DELETE: it was
  destroying the durations/latency archive that constitutes the moat. "Active" is a
  query, not a lifecycle.
- **2026-07-01** — **States over scores.** UI expresses Reported / Confirmed / Cleared;
  numeric confidence stays internal (components stored, freshness computed at read time).
  Users act on words, not probabilities.
- **2026-07-01** — **No pin without a verified place.** Abolished the Chicago-center
  geocode fallback (it rendered a Kenosha elevator outage as a Loop incident). Location
  resolution is gazetteer-first (GTFS stations/lines), Nominatim fallback, else
  list-only.
- **2026-07-01** — **Pre-implementation review amendments** (staff-engineer pass over
  the v2 spec before any code): (a) added `scope: acute|chronic|planned` — line verdicts
  compute from acute events only, because the official feeds are dominated by chronic
  elevator/construction notices and a board that always reads "Minor" dies of alarm
  fatigue; (b) added the `event_sources` table — dedup, update routing, and clearance
  all need per-source `last_seen` tracking that the JSON sources column couldn't
  provide; (c) feed-down ≠ clearance — a source's clearance evaluation runs only on
  polls where its fetch succeeded, so a broken fetcher can never paint the map green;
  (d) latency measured from source-published timestamps (fetch-time fallback flagged and
  excluded from headline stats) — poll-interval noise would drown the finding;
  (e) split `status` into `verification` (reported|confirmed) + `cleared_at` — a single
  enum erased verification history on clearance and corrupted the durations archive;
  (f) corroboration no longer requires equal `event_type` (different sources label the
  same incident differently; the old rule starved the latency dataset); (g) deferred:
  acting on `is_clearance` (capture-only; vanish-detection does the work) and map
  line-geometry rendering (1.3b, out of the Phase 1 gate).
- **2026-07-02** — **Clearance refined during 0.3: official anchors + expired ≠
  cleared.** Vanishing means different things per feed class. CTA/Metra are
  current-state feeds (alert removal = real "resolved" signal); Reddit is an occurrence
  feed (posts always drop out of the new/hot window, so absence proves nothing and
  presence keeps nothing alive). Therefore: only official sources anchor liveness — an
  event clears when all of them are confirmed vanished; reported-only events never get
  `cleared_at` (there is no signal to detect) and instead expire from the live view
  after 3 h via a separate `expired_at` column with no duration claim (`remove_event`,
  not `clear_event`). The naive per-spec rule would have both fabricated durations and
  let lingering Reddit posts block real clearances. "Vanished" is defined as ≥ 2
  successful polls since `last_seen_at` (append-only `poll_log`), making the feed-down
  guard structural rather than an `if`.
- **2026-07-02** — **Gazetteer scope (0.4):** (a) CTA stations sourced from the city's
  L-Stops open dataset rather than raw GTFS — a 176 KB keyless JSON beats a 98 MB zip
  whose station→route join needs `stop_times.txt`; (b) Metra's GTFS is
  credential-gated (all public URLs 404/503, probed live), so the build is env-gated
  and ships CTA-only until keys exist — Metra alerts fall back to Nominatim, honestly;
  (c) `lines.geojson` deferred with its only consumer (1.3b); (d) Nominatim stays
  synchronous — post-gazetteer it serves only rare road events, and a deferred-geocode
  worker is complexity that path doesn't justify (revisit trigger: 0.7 validation-week
  cycle-duration or rate-limit pressure); (e) ambiguous station names resolve to
  nothing, never a guess — the no-fake-pins rule applied to name matching.
