"""The orchestration capability fields (ORC-capability-fields): a descriptor grades
``[capabilities]`` as data -- model visibility, cost exposure, subagent-model control, the
patterns it offers, and the file and key that set each -- and the loader refuses a verified
value with no source (never guessed). These tests pin the vocabularies and the report.
"""

from __future__ import annotations

from ddflow.services import harnessreg as HR
from ddflow.services import harnessv2 as HV
from ddflow.services.parity import catalog as CAT
from ddflow.services.parity import orchestration as ORC


def _with_capabilities(extra: str) -> str:
    return (HR.HARNESS_DIR / "grok.toml").read_text("utf-8") + extra


def test_the_catalog_names_every_orchestration_capability():
    catalog = CAT.load()
    features = {r.feature for r in catalog.by_axis(CAT.ORCHESTRATOR)}
    assert {"model-visibility", "cost-exposed", "subagent-model-control", "patterns"} <= features


def test_a_default_capabilities_record_is_unknown_not_a_fact():
    cap = HV.Capabilities()
    assert cap.model_visibility == "unknown"
    assert cap.cost_exposed == "unknown"
    assert cap.subagent_model_control == "unknown"
    assert cap.patterns == ()
    assert ORC.verified(cap) == frozenset()


def test_a_verified_value_cites_its_source_and_is_verified():
    h = HR.parse(
        _with_capabilities(
            """
[capabilities]
model_visibility = "visible"
model_visibility_source = "settings.json@model_visible"
"""
        ),
        "grok",
    )
    assert h.capabilities.model_visibility == "visible"
    assert ORC.verified(ORC.record(h)) == frozenset({"model_visibility"})


def test_a_verified_value_with_no_source_is_refused_never_guessed():
    try:
        HR.parse(
            _with_capabilities(
                """
[capabilities]
model_visibility = "visible"
"""
            ),
            "grok",
        )
    except HV.DescriptorError as exc:
        assert "model_visibility_source" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a verified value with no source must be refused")


def test_a_value_outside_the_vocabulary_is_refused():
    try:
        HR.parse(
            _with_capabilities(
                """
[capabilities]
cost_exposed = "per_prompt"
cost_exposed_source = "settings.json@cost"
"""
            ),
            "grok",
        )
    except HV.DescriptorError as exc:
        assert "cost_exposed" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a value outside the vocabulary must be refused")


def test_patterns_are_a_subset_and_also_need_a_source():
    # shuffled with a documented source -> ok
    h = HR.parse(
        _with_capabilities(
            """
[capabilities]
patterns = ["subagents", "best_of_n"]
patterns_source = "config.yaml@patterns"
"""
        ),
        "grok",
    )
    assert set(h.capabilities.patterns) == {"subagents", "best_of_n"}

    try:
        HR.parse(
            _with_capabilities(
                """
[capabilities]
patterns = ["council"]
"""
            ),
            "grok",
        )
    except HV.DescriptorError as exc:
        assert "patterns_source" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("patterns with no source must be refused")

    try:
        HR.parse(
            _with_capabilities(
                """
[capabilities]
patterns = ["evergreen"]
patterns_source = "x@y"
"""
            ),
            "grok",
        )
    except HV.DescriptorError as exc:
        assert "patterns" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an unknown pattern must be refused")


def test_a_whitespace_only_source_is_not_a_source():
    try:
        HR.parse(
            _with_capabilities(
                """
[capabilities]
model_visibility = "visible"
model_visibility_source = " "
"""
            ),
            "grok",
        )
    except HV.DescriptorError as exc:
        assert "model_visibility_source" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a whitespace-only source records nothing, never a guess")


def test_a_non_string_pattern_element_is_rejected_not_crashed():
    try:
        HR.parse(
            _with_capabilities(
                """
[capabilities]
patterns = ["router", ["council"]]
patterns_source = "x@y"
"""
            ),
            "grok",
        )
    except HV.DescriptorError as exc:
        assert "patterns" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a nested list must be a DescriptorError, not a TypeError")


def test_the_report_grades_every_descriptor_without_guessing():
    report = ORC.report()
    ids = {h.id for h in HR.load()}
    assert set(report) == ids
    for entry in report.values():
        # a descriptor that declares nothing is unknown on every field, never guessed
        assert set(entry["verified"]) <= {
            "model_visibility",
            "cost_exposed",
            "subagent_model_control",
            "patterns",
        }
        assert entry["model_visibility"] in HV.MODEL_VISIBILITIES
        assert entry["cost_exposed"] in HV.COST_EXPOSURES
        assert entry["subagent_model_control"] in HV.SUBMODEL_CONTROLS
