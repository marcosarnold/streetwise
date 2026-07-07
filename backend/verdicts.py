"""Per-line verdicts — the product's core answer ("is my line okay right now?").

Derived at read time from active events; nothing stored. Two rules carry the trust
model here:
- Acute events only (A1): chronic elevator outages and planned work never degrade a
  verdict — a board that always reads "Delays" dies of alarm fatigue.
- States, not scores: the verdict is a word from CTA's own service vocabulary, chosen
  by the worst acute severity on the line.
"""

# Worst severity on the line wins. An acute event with no severity still means
# something is happening — it ranks lowest but never reads "Good service".
_SEVERITY_RANK = {None: 1, "minor": 2, "major": 3, "severe": 4}

# state → the CSS/state token; verdict → the rider-facing word (CTA's vocabulary).
_BY_RANK = {
    0: ("good", "Good service"),
    1: ("warn", "Delays"),
    2: ("warn", "Minor delays"),
    3: ("warn", "Major delays"),
    4: ("down", "Service disruption"),
}


def line_verdicts(lines_meta: dict, events: list[dict]) -> list[dict]:
    """One row per known line, CTA rail first then Metra, gazetteer order.

    lines_meta is the gazetteer's `lines` section; events are hydrated active events
    (the store's serialization — `lines` already parsed, `scope` present).
    """
    worst: dict[str, int] = {}
    counts: dict[str, int] = {}
    for event in events:
        if event.get("scope") != "acute":
            continue
        rank = _SEVERITY_RANK.get(event.get("severity"), 1)
        for line_id in event.get("lines", []):
            worst[line_id] = max(worst.get(line_id, 0), rank)
            counts[line_id] = counts.get(line_id, 0) + 1

    rows = []
    for agency, key in (("cta", "cta_rail"), ("metra", "metra")):
        for line in lines_meta.get(key, []):
            state, verdict = _BY_RANK[worst.get(line["id"], 0)]
            rows.append({
                "agency": agency,
                "id": line["id"],
                "name": line["name"],
                "color": line.get("color"),
                "state": state,
                "verdict": verdict,
                "events": counts.get(line["id"], 0),
            })
    return rows
