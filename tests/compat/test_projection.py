"""`projection.differences`, on inputs the committed fixtures may never contain: a matrix that
agrees only because the comparison went blind would pass vacuously."""

from __future__ import annotations

from projection import differences


def test_a_changed_field_is_a_difference() -> None:
    assert differences({"items": {"T1": {"state": "done"}}}, {"items": {"T1": {"state": "open"}}})


def test_a_field_only_the_new_release_has_is_not() -> None:
    assert differences({"items": {"T1": {"a": 1}}}, {"items": {"T1": {"a": 1, "b": 2}}}) == []


def test_a_record_only_the_new_release_folds_is() -> None:
    assert differences({"items": {"T1": {}}}, {"items": {"T1": {}, "T9": {}}}) == [
        "items.T9: unexpected record"
    ]


def test_a_missing_record_is() -> None:
    assert differences({"bugs": {"B1": {}}}, {"bugs": {}}) == ["bugs.B1: missing (expected {})"]


def test_values_compare_by_type() -> None:
    assert differences({"x": 1}, {"x": True}) == ["x: expected 1, got True"]
    assert differences({"x": 1}, {"x": 1.0})
    assert differences({"x": 1}, {"x": 1}) == []


def test_lists_compare_by_length_and_element() -> None:
    assert differences({"x": [1, 2]}, {"x": [1]})
    assert differences({"x": [{"a": 1}]}, {"x": [{"a": 2}]}) == ["x[0].a: expected 1, got 2"]
