"""Digests that decide a duplicate or a changed definition (B-uni-compat-capabilities.3-digests,
decision D-compat): one canonical form, and redaction markers as one token, so clones on
different redaction profiles reach the same match."""

from __future__ import annotations

import pytest

from ddflow.core import defs as DEFS
from ddflow.core import digest as D
from ddflow.core import events as E
from ddflow.core import redact as R
from ddflow.core import textsim

SPELLINGS = ["[REDACTED]", "[REDACTED:secret]", "[REDACTED:host]", "[REDACTED:"]


# -- one canonical form ------------------------------------------------------------------


def test_events_canonical_is_the_digest_module_canonical():
    assert E.canonical is D.canonical
    assert D.canonical({"b": [1, "é"], "a": 1}) == '{"a":1,"b":[1,"é"]}'


def test_of_obj_is_a_hash_of_the_canonical_form():
    obj = {"z": 1, "a": {"y": [True, None], "x": "é"}}
    assert D.of_obj(obj) == D.content_digest(D.canonical(obj), "blake2b", size=12)
    assert D.of_obj(obj, size=16) == E.canonical_digest(obj, size=16)
    assert D.of_obj(obj, "sha256", length=12) == D.content_digest(D.canonical(obj), length=12)
    assert D.of_obj({"a": 1, "b": 2}) == D.of_obj({"b": 2, "a": 1})


# -- markers --------------------------------------------------------------------------


@pytest.mark.parametrize("marker", SPELLINGS)
def test_every_marker_spelling_is_one_token(marker):
    assert D.normalize_markers(f"key: {marker} here") == f"key: {D.REDACTED} here"


def test_the_redactor_and_the_digests_read_one_marker_grammar():
    assert R._MARKER is D.MARKER


def test_the_clipper_keeps_whole_the_marker_the_redactor_writes():
    from ddflow.core import textcut

    assert R._marker("secret").startswith(textcut.MARK_START)
    assert textcut._MARK.fullmatch(R._marker("secret"))


def test_the_markers_the_redactor_writes_are_all_normalized():
    for kind in ("secret", "host", "home", "ip", "name"):
        assert D.normalize_markers(R._marker(kind)) == D.REDACTED
    assert D.normalize_markers("api_key: [REDACTED]") == "api_key: [REDACTED]"


def test_normalized_reaches_every_string_and_nothing_else():
    obj = {"a [REDACTED:x]": ["t [REDACTED:host]", 3, None, {"k": "[REDACTED:secret]"}], "n": 1.5}
    assert D.normalized(obj) == {
        "a [REDACTED]": ["t [REDACTED]", 3, None, {"k": "[REDACTED]"}],
        "n": 1.5,
    }


# -- the duplicate digest -----------------------------------------------------------------


def test_the_text_digest_agrees_across_redaction_profiles_case_and_whitespace():
    a = textsim.digest("Token  leaks", "the key is [REDACTED:secret]\nsee host [REDACTED:host]")
    b = textsim.digest("token leaks", "the KEY is [REDACTED]   see host [REDACTED]")
    assert a == b
    assert a != textsim.digest("token leaks", "the key is [REDACTED] see host elsewhere")


def test_an_index_built_before_the_digest_changed_is_stale(repo, log, cfg, monkeypatch):
    from ddflow.infra.store import Store

    log.append("phase.added", "P1", {"title": "p"})
    st = Store(repo, cfg)
    with monkeypatch.context() as m:
        m.setattr(textsim, "VERSION", textsim.VERSION - 1)  # an index of the previous digest
        st.rebuild(log)
        assert not st.stale(log)
    assert st.stale(log)  # the running code's digest differs: the index re-derives


# -- the definition digest -------------------------------------------------------------


def test_a_definition_digest_agrees_across_redaction_profiles():
    a = DEFS.digest({"cmd": "run --token [REDACTED:secret]", "n": 1})
    b = DEFS.digest({"n": 1, "cmd": "run --token [REDACTED]"})
    assert a == b


def test_a_definition_digest_still_sees_case_and_whitespace():
    assert DEFS.digest({"cmd": "make"}) != DEFS.digest({"cmd": "Make"})
    assert DEFS.digest({"cmd": "a b"}) != DEFS.digest({"cmd": "a  b"})


def test_a_digest_recorded_before_normalizing_still_matches():
    fields = {"cmd": "make", "n": 1}
    legacy = E.canonical_digest(fields, size=16)
    assert DEFS.same(fields, legacy) and DEFS.same(fields, DEFS.digest(fields))
    assert not DEFS.same({**fields, "n": 2}, legacy)
    marked = {"cmd": "x [REDACTED:secret]"}
    assert DEFS.same(marked, E.canonical_digest(marked, size=16))  # recorded under that profile


def test_an_update_to_a_legacy_digested_definition_that_changes_nothing_is_no_change(repo):
    from ddflow.api import defs as API
    from ddflow.infra.log import EventLog

    log = EventLog(repo, "a1")
    fields = {"cmd": "make"}
    log.append(
        "def.recorded",
        "schedule:s1",
        {
            "kind": "schedule",
            "id": "s1",
            "fields": fields,
            "digest": E.canonical_digest(fields, size=16),
            "source": "",
            "provenance": {"by": "a1"},
        },
    )
    out = API.def_update(repo, "schedule", "s1", {"cmd": "make"}, agent="a1")
    assert out.exit == 2, out  # nothing changed
