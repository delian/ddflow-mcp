"""Egress at the doors that feed models (SE-sensitivity.3): search and recall."""

from __future__ import annotations

import ddflow.api.records as R
from ddflow.api._base import _load
from ddflow.api.knowledge.retrieval import recall
from ddflow.config import Config
from ddflow.services import search as SS
from ddflow.services.egress import Egress

KIND = R.KINDS["risk"]


def _hits(repo, word="zebrafish"):
    _log, cfg, st = _load(repo)
    res = SS.search(st, [], cfg, word, repo=repo)
    return {h["id"] for h in res.rows}


def _recalled(repo, word="zebrafish"):
    out = recall(repo, word, sources="risk")
    return {r["id"] for rows in out.data["results"].values() for r in rows}


def _put(repo, rid, label=""):
    fields = {"title": f"zebrafish {rid}"}
    if label:
        fields["sensitivity"] = label
    assert R.record_add(repo, KIND, rid, fields).exit == 0


def _config(repo, text):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text(text)


def test_a_restricted_record_is_withheld_from_search_and_a_public_one_is_not(repo):
    _put(repo, "open1", "public")
    _put(repo, "plain")
    _put(repo, "closed", "restricted")
    _put(repo, "secret1", "confidential")
    assert _hits(repo) == {"open1", "plain"}


def test_a_level_in_model_allowed_is_shown(repo):
    _put(repo, "closed", "restricted")
    _config(repo, '[sensitivity]\nmodel_allowed = ["restricted"]\n')
    assert _hits(repo) == {"closed"}


def test_the_doors_are_a_knob(repo):
    _put(repo, "closed", "restricted")
    _config(repo, "[sensitivity]\negress_doors = []\n")
    assert _hits(repo) == {"closed"}
    _config(repo, '[sensitivity]\negress_doors = ["recall"]\n')
    assert _hits(repo) == {"closed"}


def test_recall_withholds_too(repo):
    _put(repo, "open1", "public")
    _put(repo, "closed", "restricted")
    got = _recalled(repo)
    assert got == {"open1"}


def test_an_unknown_label_fails_closed(repo):
    gate = Egress(repo, Config())
    assert gate.withholds("search", {"sensitivity": "gone"})
    assert not gate.withholds("search", {})
    assert not gate.withholds("elsewhere", {"sensitivity": "confidential"})
