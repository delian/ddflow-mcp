"""SE-kind-data.3: the shipped data kinds, and kinds a project defines for itself."""

from __future__ import annotations

import pytest

import ddflow.api.records as R
import ddflow.core.defs as D

DATA_KINDS = (
    "assumption attention attestation commlog contract fitness proposal requirement risk "
    "stakeholder vendor"
).split()
#: Minimal valid fields, and a record each links to, per kind.
MIN = {
    "requirement": {"title": "t", "source": "https://x/y"},
    "contract": {"title": "t", "vendor": "acme", "document": "d"},
    "attestation": {"title": "t", "attester": "ann", "evidence": "e"},
}


def test_every_data_kind_is_declared_and_searchable():
    shipped = {n for n, _ in KDA.packaged()} - {"rule"}
    assert shipped == set(DATA_KINDS)  # a new packaged kind joins the list below
    assert R.RULE.surfaced and not any(R.KINDS[n].surfaced for n in shipped)
    for name in shipped:
        kind = R.KINDS[name]
        assert kind.def_kind == name and name in D.DEF_KINDS
        assert kind.search and kind.recall and kind.resource
        assert kind.rules is None or kind.rules.stamp == 1


@pytest.mark.parametrize("name", DATA_KINDS)
def test_a_data_kind_adds_shows_edits_and_removes(name, repo):
    kind = R.KINDS[name]
    if name == "contract":
        R.record_add(repo, R.KINDS["vendor"], "acme", {"title": "Acme"})
    fields = MIN.get(name, {"title": "t"})
    assert R.record_add(repo, kind, "x1", fields).exit == 0
    shown = R.record_show(repo, kind, "x1").data
    assert shown["fields"]["title"] == "t"
    if kind.rules and kind.rules.lifecycle:
        assert shown["fields"][kind.rules.lifecycle.field] == kind.rules.lifecycle.initial
    assert R.record_edit(repo, kind, "x1", {"title": "t2"}).exit == 0
    if "remove" in kind.verbs:
        assert R.record_remove(repo, kind, "x1", reason="done").exit == 0
    else:
        assert "never removed" in R.record_remove(repo, kind, "x1", reason="x").reason


def test_requirement_needs_a_source_and_follows_its_lifecycle(repo):
    req = R.KINDS["requirement"]
    assert "source is required" in R.record_add(repo, req, "q1", {"title": "t"}).reason
    R.record_add(repo, req, "q1", MIN["requirement"])
    assert R.record_edit(repo, req, "q1", {"state": "verified"}).exit != 0
    assert R.record_edit(repo, req, "q1", {"state": "approved"}).exit == 0


def test_risk_links_and_bounds(repo):
    risk = R.KINDS["risk"]
    assert "at most 5" in R.record_add(repo, risk, "k1", {"title": "t", "impact": 9}).reason
    assert (
        "no stakeholder 'nobody'"
        in R.record_add(repo, risk, "k1", {"title": "t", "owner": "nobody"}).reason
    )
