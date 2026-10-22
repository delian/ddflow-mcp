"""B-rule-add-no-enforcement-option: `rule add|edit --enforcement advisory|warn|block` and `--pin`.

A pinned rule (always-scope, `block`) could only come from a hand-written rules file, so the
rules-charter pin path could not be used or exercised from the CLI or MCP.
"""

from __future__ import annotations

import json

import pytest
from conftest import run_cli

from ddflow.services.guidance.inject import pinned
from ddflow.services.rules import RulesStorage

ADD = (
    "rule",
    "add",
    "--id",
    "r-one",
    "--title",
    "Keep names plain",
    "--content",
    "Name things by what they do.",
)


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "off")


def _rec(repo, rid="r-one"):
    return RulesStorage(repo).get(rid).to_record()


def test_add_with_enforcement_writes_it(repo):
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, *ADD, "--enforcement", "warn")
    assert code == 0, err
    assert _rec(repo).enforcement == "warn"


def test_add_without_it_stays_advisory(repo):
    run_cli(repo, "init")
    assert run_cli(repo, *ADD)[0] == 0
    assert _rec(repo).enforcement == "advisory"


def test_pin_is_block_for_all_work(repo):
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, *ADD, "--pin")
    assert code == 0, err
    rec = _rec(repo)
    assert rec.enforcement == "block" and pinned(rec)


def test_pin_with_globs_is_refused(repo):
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, *ADD, "--pin", "--globs", "src/**")
    assert code != 0 and "pin" in err
    assert run_cli(repo, "--json", "rule", "list")[1].count("r-one") == 0


def test_an_unknown_enforcement_is_refused(repo):
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, *ADD, "--enforcement", "strict")
    assert code != 0 and "advisory" in err and "block" in err


def test_edit_changes_enforcement_and_keeps_the_rest(repo):
    run_cli(repo, "init")
    assert run_cli(repo, *ADD, "--priority", "70")[0] == 0
    code, _out, err = run_cli(repo, "rule", "edit", "r-one", "--enforcement", "block")
    assert code == 0, err
    rec = _rec(repo)
    assert rec.enforcement == "block" and rec.priority == 70 and rec.title == "Keep names plain"
    code, _out, err = run_cli(repo, "rule", "edit", "r-one", "--priority", "40")
    assert code == 0, err
    assert _rec(repo).enforcement == "block"  # an edit that does not name it leaves it


def test_edit_pin_clears_the_globs(repo):
    run_cli(repo, "init")
    assert run_cli(repo, *ADD, "--globs", "src/**")[0] == 0
    code, _out, err = run_cli(repo, "rule", "edit", "r-one", "--pin")
    assert code == 0, err
    rec = _rec(repo)
    assert pinned(rec) and not rec.scope.globs


def test_show_reports_the_enforcement(repo):
    run_cli(repo, "init")
    assert run_cli(repo, *ADD, "--enforcement", "block")[0] == 0
    code, out, _err = run_cli(repo, "--json", "rule", "show", "r-one")
    assert code == 0 and json.loads(out)["enforcement"] == "block"


def test_edit_with_an_unknown_enforcement_is_refused_and_changes_nothing(repo):
    run_cli(repo, "init")
    assert run_cli(repo, *ADD)[0] == 0
    code, _out, err = run_cli(repo, "rule", "edit", "r-one", "--enforcement", "strict")
    assert code != 0 and "block" in err, err
    assert _rec(repo).enforcement == "advisory"
