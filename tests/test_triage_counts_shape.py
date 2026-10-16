"""Bug Bf4647f7d04: triage_counts raised AttributeError on findings that are not dicts.

A gate's evidence ``chunk_findings`` may hold strings or blob references; such a record
is not triageable, so ``triage_counts`` returns None instead of crashing `gate status`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ddflow.services import gates as G


def _item(found):
    rec = SimpleNamespace(evidence={"chunk_findings": found})
    return SimpleNamespace(gates={"critic": rec}, triage={})


def test_string_list_is_not_triageable():
    assert G.triage_counts(_item(["a finding", "another"]), "critic") is None


def test_blob_reference_dict_is_not_triageable():
    assert G.triage_counts(_item({"blob": "sha256:abc", "size": 10}), "critic") is None


def test_mixed_list_is_not_triageable():
    assert G.triage_counts(_item([{"digest": "d1"}, "stray string"]), "critic") is None


@pytest.mark.parametrize("found", [[], None, [{"title": "no digest"}], "text"])
def test_other_shapes_are_not_triageable(found):
    assert G.triage_counts(_item(found), "critic") is None


@pytest.mark.parametrize("digest", [["sha256:abc"], {"root": "sha256:abc"}, 7])
def test_unhashable_or_non_string_digest_is_not_triageable(digest):
    assert G.triage_counts(_item([{"digest": digest}]), "critic") is None


def test_dict_findings_with_digests_still_count():
    got = G.triage_counts(_item([{"digest": "d1"}, {"digest": "d2"}]), "critic")
    assert got == {"findings": 2, "refuted": 0, "confirmed": 0, "untriaged": 2}
