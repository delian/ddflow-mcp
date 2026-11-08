"""DX-merge-force-flag: `[merge].require_gates` and `merge --force`.

`warn` (default) lands the branch and names the unmet gates; `refuse` needs `--force`, an agent's
with an operator approval bound to the exact unmet gates, spent only when nothing else refuses
the merge, and recorded as a `merge.forced` event. The merge record carries every gate's real
state (Bb55340e14b: a post-merge `passed` was read as the ci gate's)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

from ddflow import api
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.services import merge_force as MF


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def refuse(monkeypatch):
    monkeypatch.setattr(MF, "mode_of", lambda cfg: "refuse")


def _claimed(repo: Path) -> Path:
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add a", "--globs", "a.py")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "a.py").write_text("x = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "add a")
    return tree


def _events(repo, kind):
    return [e for e in EventLog(repo).read_all() if e.kind == kind]


def _asks(repo):
    return asks_of(fold(EventLog(repo).read_all(), strict=False))


def _merge(repo, **kw):
    return api.merge_item(repo, "T1", agent=kw.pop("agent", "bot"), **kw)


def _approved_ask(repo) -> str:
    out = _merge(repo, force=True, approval="request")
    assert out.exit == 3, out.reason
    ask = re.search(r"(Q[0-9a-f]+)", out.reason).group(1)
    code, _o, err = run_cli(
        repo, "approvals", "approve", ask, "--digest", _asks(repo)[ask]["digest"][:12]
    )
    assert code == 0, err
    return ask


def test_warn_lands_and_names_the_unmet_gates(repo):
    _claimed(repo)
    out = _merge(repo)
    assert out.exit == 0, out.reason
    assert out.data["awaiting_gates"], "the unmet required gates are named"
    assert "merge" not in {g.split("=")[0] for g in out.data["awaiting_gates"]}
    assert not _events(repo, "merge.forced")


def test_the_merge_record_carries_each_gates_real_state(repo):
    _claimed(repo)
    code, _o, err = run_cli(
        repo, "gate", "record", "T1", "ci", "--outcome", "unavailable", "--reason", "never ran"
    )
    assert code == 0, err
    out = _merge(repo)
    assert out.exit == 0, out.reason
    assert out.data["gate_states"]["ci"] == "unavailable"
    (ev,) = _events(repo, "worktree.merged")
    assert ev.data["gates"]["ci"] == "unavailable"
    # the post-merge check of the base has its own name, apart from the ci GATE above
    if "ci" in out.data:
        assert out.data["post_merge_check"] == out.data["ci"]


def test_refuse_without_force_names_the_gates_and_the_next_call(repo, refuse):
    tree = _claimed(repo)
    out = _merge(repo)
    assert out.exit == 3
    assert "require_gates = refuse" in out.reason
    assert "--force --approval request" in out.reason
    assert not _events(repo, "worktree.merged")
    assert (tree / "a.py").exists()


def test_an_agent_force_without_approval_is_refused_and_files_nothing(repo, refuse):
    _claimed(repo)
    out = _merge(repo, force=True)
    assert out.exit == 3
    assert "--approval request" in out.reason
    assert not _asks(repo)
    assert not _events(repo, "worktree.merged")


def test_a_forced_merge_with_an_approval_lands_and_records_merge_forced(repo, refuse):
    _claimed(repo)
    ask = _approved_ask(repo)
    out = _merge(repo, force=True, approval=ask)
    assert out.exit == 0, out.reason
    (forced,) = _events(repo, "merge.forced")
    assert forced.subject == "T1"
    assert forced.data["unmet_gates"], "names the gates it overrode"
    assert forced.data["approval"]["ask"] == ask
    assert _asks(repo)[ask]["used"] == 1
    assert _events(repo, "worktree.merged")


def test_the_approval_is_single_use(repo, refuse):
    _claimed(repo)
    ask = _approved_ask(repo)
    from ddflow.api._base import _load

    log, cfg, st = _load(repo, "bot")
    a = MF.assess(repo, cfg, st, "T1")  # the very assessment the approval is bound to
    assert _merge(repo, force=True, approval=ask).exit == 0
    assert _asks(repo)[ask]["used"] == 1
    # same command, same agent, same gates: only the spend count differs
    stamp, why, pending = MF.authorise(log, cfg, "T1", a, force=True, approval=ask, agent="bot")
    assert stamp is None and "is spent" in why, why
    assert _asks(repo)[ask]["used"] == 1


def test_a_refused_call_does_not_spend_the_approval(repo, refuse):
    tree = _claimed(repo)
    ask = _approved_ask(repo)
    (tree / "stray.txt").write_text("not committed\n")
    out = _merge(repo, force=True, approval=ask)
    assert out.exit == 3 and "uncommitted" in out.reason
    assert _asks(repo)[ask]["used"] == 0
    (tree / "stray.txt").unlink()
    assert _merge(repo, force=True, approval=ask).exit == 0


def test_a_person_force_needs_no_approval(repo, refuse):
    _claimed(repo)
    out = _merge(repo, force=True, agent="")
    assert out.exit == 0, out.reason
    (forced,) = _events(repo, "merge.forced")
    assert forced.data["unmet_gates"]


def test_an_approval_for_other_gates_does_not_cover_this_merge(repo, refuse):
    _claimed(repo)
    ask = _approved_ask(repo)
    run_cli(repo, "gate", "record", "T1", "implement", "--outcome", "unavailable", "--reason", "x")
    out = _merge(repo, force=True, approval=ask)
    assert out.exit == 3
    assert _asks(repo)[ask]["used"] == 0


def test_the_knob_is_registered_and_defaults_to_warn(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "config")
    assert code == 0, err
    line = next(ln for ln in out.splitlines() if "merge.require_gates" in ln)
    assert "warn" in line


def test_the_knob_reads_refuse_from_config_and_agent_force_still_refuses(repo):
    """`mode_of` reads the knob through `cfg.merge.require_gates` (not a bare getattr), so
    setting it to `refuse` makes an agent `--force` without an approval refuse."""
    _claimed(repo)
    run_cli(repo, "config", "merge.require_gates", "refuse")
    out = _merge(repo, force=True)
    assert out.exit == 3
    assert "--approval request" in out.reason
    assert not _events(repo, "worktree.merged")


def test_a_failed_git_landing_does_not_spend_the_approval(repo, refuse, monkeypatch):
    """B527b0a518c: an agent's single-use approval is spent only when the landing lands. A
    git landing that fails (a conflict) must leave the approval unspent, so the agent can
    retry once the conflict is resolved, instead of the `merge --force --approval` spending
    it before git ran."""
    from ddflow.infra import git as G
    from ddflow.infra import worktree as W

    _claimed(repo)
    ask = _approved_ask(repo)
    monkeypatch.setattr(
        W, "merge", lambda *_a, **_kw: G.GitResult(W.GIT_REFUSED, "", "conflict: a.py", attempted=True)
    )
    out = _merge(repo, force=True, approval=ask)
    assert out.exit != 0, out.reason
    assert not _events(repo, "worktree.merged")
    assert _asks(repo)[ask]["used"] == 0


def test_authorise_checks_but_spend_records_the_use(repo, refuse):
    """B527b0a518c: `authorise` validates the approval without recording a use; the landing's
    `spend` records the one use. Between the two a landing may still fail, which is the whole
    point -- a spent-then-failed is what this bug forbids."""
    from ddflow.api._base import _load

    _claimed(repo)
    ask = _approved_ask(repo)
    log, cfg, st = _load(repo, "bot")
    a = MF.assess(repo, cfg, st, "T1")
    proof, why, pending = MF.authorise(log, cfg, "T1", a, force=True, approval=ask, agent="bot")
    assert proof and not why and pending == ask
    assert _asks(repo)[ask]["used"] == 0  # validation alone does not spend
    assert MF.spend(log, cfg, "T1", a, ask).ok
    assert _asks(repo)[ask]["used"] == 1  # the landing's spend records the single use


def test_a_person_or_policy_stamp_is_returned_without_a_spend(repo, refuse):
    """B527b0a518c regression (roborev): only a real `--approval <id>` is spent after landing;
    a person's `--force` and a standing-policy stamp pass to `merge.forced` untouched, never
    to `spend` (which would refuse the empty id and overwrite the proof)."""
    from importlib import import_module

    M = import_module("ddflow.api.lifecycle.merge")

    person = {"by": "person"}
    policy = {"ask": "Qx", "approved_by": "policy:auto-after-retries", "via": "policy", "digest": "d"}
    assert M._spend_after_land(None, None, "T1", None, person, "") is person
    assert M._spend_after_land(None, None, "T1", None, policy, "") is policy


def test_authorise_needs_nothing_when_nothing_refuses():
    a = MF.Assessment(mode="refuse", unmet={})
    assert MF.authorise(None, None, "T1", a, force=False, approval="", agent="bot") == (None, "", "")
    warn = MF.Assessment(mode="warn", unmet={"ci": "not run"})
    assert MF.authorise(None, None, "T1", warn, force=False, approval="", agent="bot") == (None, "", "")
