"""Extractor output contract: model output is untrusted input.

Every enum coerces to its known set; the two deliberate defaults are unknown scope ->
acute (hiding a real disruption is worse than a noisy verdict row) and unknown
confidence -> low (unearned certainty is never granted).
"""

from backend.extractor import _recover_truncated_array, _sanitize_event, _strip_code_fence


def _raw(**overrides):
    base = {
        "event_type": "delay",
        "mode": "cta_rail",
        "lines": ["Red"],
        "station": "Howard",
        "location_string": None,
        "severity": "minor",
        "scope": "acute",
        "summary": "Red Line trains delayed near Howard.",
        "is_clearance": False,
        "extraction_confidence": "high",
        "source_id": "1001",
    }
    base.update(overrides)
    return base


def test_well_formed_event_passes_through():
    assert _sanitize_event(_raw()) == _raw()


def test_rejects_only_the_unusable():
    assert _sanitize_event("not a dict") is None
    assert _sanitize_event(_raw(source_id="")) is None   # can't route or archive
    assert _sanitize_event(_raw(summary="  ")) is None   # nothing to show
    # A malformed enum never drops a real disruption — it coerces.
    assert _sanitize_event(_raw(event_type="explosion")) is not None


def test_enum_coercion_defaults():
    e = _sanitize_event(_raw(
        event_type="explosion", mode="hovercraft", severity="catastrophic",
        scope="permanent", extraction_confidence="absolutely",
    ))
    assert e["event_type"] == "other"
    assert e["mode"] is None
    assert e["severity"] is None
    assert e["scope"] == "acute"                  # unknown scope must not hide a disruption
    assert e["extraction_confidence"] == "low"    # unknown confidence earns nothing


def test_lines_and_text_fields_are_shaped():
    e = _sanitize_event(_raw(lines=["Red", "", 66, "  "], station="  ", location_string=""))
    assert e["lines"] == ["Red", "66"]            # strings, stripped, empties dropped
    assert e["station"] is None
    assert e["location_string"] is None
    e = _sanitize_event(_raw(lines="Red"))        # non-list -> empty, not a crash
    assert e["lines"] == []
    e = _sanitize_event(_raw(lines=[str(n) for n in range(20)]))
    assert len(e["lines"]) == 8                   # MAX_LINES cap: more is model noise


def test_is_clearance_coerces_to_bool():
    assert _sanitize_event(_raw(is_clearance="yes"))["is_clearance"] is True
    assert _sanitize_event(_raw(is_clearance=None))["is_clearance"] is False


def test_fence_and_truncation_recovery():
    assert _strip_code_fence("```json\n[1, 2]\n```") == "[1, 2]"
    assert _strip_code_fence("[1, 2]") == "[1, 2]"
    # max_tokens truncation mid-object: salvage the complete ones.
    assert _recover_truncated_array('[{"a": 1}, {"b": 2}, {"c":') == [{"a": 1}, {"b": 2}]
    assert _recover_truncated_array("no objects here") == []
