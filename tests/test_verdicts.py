"""Line verdicts: acute-only derivation, worst-severity wins, CTA vocabulary."""

from backend.verdicts import line_verdicts

LINES_META = {
    "cta_rail": [
        {"id": "Red", "name": "Red Line", "color": "#C60C30"},
        {"id": "Blue", "name": "Blue Line", "color": "#00A1DE"},
    ],
    "metra": [
        {"id": "UP-N", "name": "Union Pacific North", "color": "#008000"},
    ],
}


def _event(**overrides) -> dict:
    base = {"scope": "acute", "severity": "minor", "lines": ["Red"]}
    base.update(overrides)
    return base


def test_no_events_means_good_service_everywhere():
    rows = line_verdicts(LINES_META, [])
    assert len(rows) == 3
    assert all(r["state"] == "good" and r["verdict"] == "Good service" for r in rows)
    assert [r["agency"] for r in rows] == ["cta", "cta", "metra"]  # CTA first, gazetteer order


def test_chronic_and_planned_never_degrade_a_verdict():
    events = [
        _event(scope="chronic", severity="severe"),   # the elevator-outage wall (A1)
        _event(scope="planned", severity="severe"),
    ]
    rows = line_verdicts(LINES_META, events)
    assert all(r["verdict"] == "Good service" for r in rows)


def test_worst_acute_severity_wins_and_events_are_counted():
    events = [
        _event(severity="minor"),
        _event(severity="severe"),
        _event(severity="major", lines=["Blue"]),
    ]
    by_id = {r["id"]: r for r in line_verdicts(LINES_META, events)}
    assert by_id["Red"]["verdict"] == "Service disruption"
    assert by_id["Red"]["state"] == "down"
    assert by_id["Red"]["events"] == 2
    assert by_id["Blue"]["verdict"] == "Major delays"
    assert by_id["Blue"]["state"] == "warn"
    assert by_id["UP-N"]["verdict"] == "Good service"


def test_acute_without_severity_still_reads_delays_never_good():
    rows = line_verdicts(LINES_META, [_event(severity=None, lines=["UP-N"])])
    up_n = next(r for r in rows if r["id"] == "UP-N")
    assert up_n["verdict"] == "Delays"
    assert up_n["state"] == "warn"


def test_unknown_line_ids_are_ignored_not_invented():
    # An extraction naming a line we don't know must not conjure a board row.
    rows = line_verdicts(LINES_META, [_event(lines=["Hogwarts Express"])])
    assert len(rows) == 3
    assert all(r["verdict"] == "Good service" for r in rows)
