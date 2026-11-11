"""The reuse measure and its ratchet: a new agent is a descriptor plus fixtures, and Python
exists only for a real gap -- a hook normalizer or emitter used by exactly one descriptor.

``ddflow/services/parity/reuse.py`` is the one home for the measure. This test pins it and
ratchets the result: a descriptor may not add bespoke Python unless the gap is recorded
with a reason in its delta note, so the bespoke set is committed here and can only shrink.
"""

from __future__ import annotations

from types import SimpleNamespace

from ddflow.services import harnessreg as HR
from ddflow.services.parity import reuse as R

#: The committed bespoke set: an agent whose normalizer and/or emitter is used by no
#: other descriptor, and whose driver delta note records the gap with its reason. The set
#: only shrinks -- a gap closes when the piece is now shared, and a NEW bespoke agent must
#: first record the gap in its delta note, then be added here. Leaving an entry behind
#: that is no longer bespoke fails the same way a new unrecorded agent does.
BASELINE = frozenset({"antigravity", "cline", "copilot", "cursor", "goose", "windsurf"})


def _h(id: str, emitter: str, fields: dict | None = None, delta: str = ""):
    return SimpleNamespace(
        id=id, delta=delta, hooks=SimpleNamespace(emitter=emitter, fields=fields or {})
    )


def _hookless(id: str):
    return SimpleNamespace(id=id, hooks=None)


def test_the_default_field_map_is_the_shared_normalizer_key():
    # An absent or empty [hooks.fields] resolves to hookio's fallback dialect, so two
    # descriptors that both leave it out reuse one normalizer.
    assert R._normalizer_key({}) == ()
    assert R._normalizer_key(None) == ()


def test_a_field_maps_identity_is_its_paths_not_just_its_facts():
    # Two maps naming the same facts at different stdin paths are TWO normalizers, not one;
    # the fact names alone would call them shared when each still needs its own reading.
    a = {"session": ["session_id"]}
    b = {"session": ["conversation_id"]}
    assert R._normalizer_key(a) != R._normalizer_key(b)


def test_a_hookless_agent_has_no_axis():
    assert R.axes(_hookless("cody")) is None
    assert R.axes(_h("aider", "")) is None  # style = "none" declares no emit variant


def test_two_agents_that_share_an_emitter_and_normalizer_are_not_bespoke():
    hs = [_h("one", "plain"), _h("two", "plain")]
    b = R.bespoke(hs)
    assert b == {}, b


def test_an_emitter_used_by_one_descriptor_is_bespoke():
    hs = [_h("one", "plain"), _h("two", "plain"), _h("solo", "empty_json")]
    b = R.bespoke(hs)
    assert set(b) == {"solo"}
    assert R.EMITTER in b["solo"]


def test_a_normalizer_used_by_one_descriptor_is_bespoke():
    hs = [_h("one", "plain", {"prompt": ["prompt"]}), _h("two", "plain"), _h("three", "plain")]
    b = R.bespoke(hs)
    assert set(b) == {"one"}
    assert R.NORMALIZER in b["one"]


def test_an_agent_bespoke_on_either_axis_counts_once():
    hs = [_h("solo", "empty_json", {"prompt": ["prompt"]}), _h("two", "plain")]
    b = R.bespoke(hs)
    assert b["solo"] == {R.NORMALIZER, R.EMITTER}


def test_measure_names_every_agent_and_its_zero_python_rate():
    m = R.measure()
    ids = {h.id for h in HR.load()}
    assert m["agents"] == len(ids)
    assert 0 <= m["zero_python"] <= m["agents"]
    assert set(m["bespoke"]) <= ids
    assert m["rate"] == round(m["zero_python"] / m["agents"], 3)
    # every hooked descriptor resolves to a real emit variant (its "module") or a shared one
    assert m["emitter_variants"], "the report names each emit variant and its use count"


def test_the_bespoke_axis_sets_partition_their_agents():
    m = R.measure()
    assert set(m["normalizer_bespoke"]) <= set(m["bespoke"])
    assert set(m["emitter_bespoke"]) <= set(m["bespoke"])
    assert set(m["normalizer_bespoke"]) | set(m["emitter_bespoke"]) == set(m["bespoke"])


def test_the_bespoke_set_only_shrinks():
    """A new agent with bespoke Python fails here until the gap is recorded (with its
    reason) in its delta note AND added to BASELINE; a closed gap must leave it."""
    now = set(R.bespoke())
    assert not now - BASELINE, (
        f"a new agent added bespoke Python without recording the gap: {sorted(now - BASELINE)}"
    )
    assert not BASELINE - now, (
        f"a recorded gap closed (now shared); drop {sorted(BASELINE - now)} from BASELINE"
    )


def test_a_missing_delta_note_is_empty():
    assert R.delta_note(_hookless("cody")) == ""
    assert R.delta_note(_h("x", "plain", delta="")) == ""
    assert R.delta_note(_h("x", "plain", delta="note-that-does-not-exist.md")) == ""


def test_unrecorded_names_a_bespoke_agent_missing_from_the_recorded_set():
    assert R.unrecorded(recorded=BASELINE) == []
    # a new agent bespoke on the emitter axis, with its gap not yet recorded
    hs = [_h("one", "plain"), _h("two", "plain"), _h("novel", "novel_variant")]
    assert R.unrecorded(hs, recorded=frozenset()) == ["novel"]
    assert R.unrecorded(hs, recorded=frozenset({"novel"})) == ["novel"]  # still no delta note


def test_a_recorded_bespoke_agent_needs_its_delta_note(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "DELTA_DIR", tmp_path)
    hs = [_h("one", "plain"), _h("two", "plain")]
    bespoke_novel = _h("novel", "novel_variant", delta="novel.md")
    (tmp_path / "novel.md").write_text("emitter novel_variant: pending the fixture probe\nreason\n")
    assert R.unrecorded([*hs, bespoke_novel], recorded=frozenset()) == ["novel"]  # unrecorded
    assert (
        R.unrecorded([*hs, bespoke_novel], recorded=frozenset({"novel"})) == []
    )  # recorded + note


def test_a_noop_measure_of_nothing_is_zero_not_infinite():
    m = R.measure([])
    assert m["agents"] == 0 and m["zero_python"] == 0 and m["rate"] == 0.0
