"""B3c1a2491a3: `complete` validates a malformed `--regression-test` BEFORE it spends the
operator's single-use approval, so a typo in the flag leaves the agent with the approval
still usable instead of needing a fresh one."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api.knowledge.bug_close import bug_fixed
from ddflow.api.knowledge.regression import regression_arg_refusal
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


def _state(repo):
    return fold(EventLog(repo, "probe").read_all(), strict=False)


def _ask_id(text: str) -> str:
    found = re.search(r"approval (Q[0-9a-f]+)", text)
    assert found, text
    return found.group(1)


def _complete(repo, item, *extra):
    return run_cli(repo, "complete", item, "--model", "claude-opus-4", *extra, agent="bot")


def test_a_malformed_regression_test_is_refused_without_spending_the_approval(repo):
    run_cli(repo, "init")
    code, _o, err = run_cli(
        repo,
        "bug",
        "found",
        "--id",
        "B-validate-before-spend",
        "--summary",
        "red",
        "--title",
        "complete spends its approval before validating arguments",
    )
    assert code == 0, err
    item = "fix-B-validate-before-spend"

    # The bare `.py` path is what a wrong flag looks like; its `::test_x` node id is the
    # valid value the approval is requested against. The file must exist so the malformed
    # refusal is the node-id one, not the "exists in no worktree" one.
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n")

    # Unmet conditions -> the operator's approval. Request it WITH the valid closure, so
    # the approval's digest names the state the spend below acts on (the open-bug blocker
    # is lifted by the flag, request and spend alike).
    code, _o, err = _complete(
        repo, item, "--approval", "request", "--regression-test", "tests/test_x.py::test_x"
    )
    assert code == 3, err
    ask_id = _ask_id(err)
    assert asks_of(_state(repo))[ask_id]["verb"] == "complete.force"

    digest = asks_of(_state(repo))[ask_id]["digest"]
    code, _o, err = run_cli(repo, "approvals", "approve", ask_id, "--digest", digest[:12])
    assert code == 0, err

    code, _o, err = _complete(
        repo, item, "--approval", ask_id, "--regression-test", "tests/test_x.py"
    )
    assert code == 3 and "is not a pytest node id" in err, err
    # The refusal came from validating the flag, not from touching the approval: the single
    # use is unspent AND the ask is still the one the operator answered (same id, state and
    # digest, no second ask), so the agent can re-run with the corrected id unchanged.
    asks = asks_of(_state(repo))
    assert list(asks) == [ask_id], asks
    assert asks[ask_id]["used"] == 0
    assert asks[ask_id]["uses"] == 1
    assert asks[ask_id]["state"] == "answered"
    assert asks[ask_id]["digest"] == digest

    # Positive proof the approval survived the malformed flag: the corrected run with the
    # SAME approval id completes (a spent or invalidated approval would refuse here).
    code, _o, err = _complete(
        repo, item, "--approval", ask_id, "--regression-test", "tests/test_x.py::test_x"
    )
    assert code == 0, err


def test_precheck_and_bug_fixed_refuse_the_same_half_node_id(repo):
    """The lockstep contract (roborev): `regression_arg_refusal` and `bug_fixed` must refuse
    a half node id the same way, or the pre-approval guard silently stops protecting the
    single-use approval when one of the two sites drifts."""
    run_cli(repo, "init")
    code, _o, err = run_cli(
        repo,
        "bug",
        "found",
        "--id",
        "B-lockstep",
        "--summary",
        "red",
        "--title",
        "precheck and bug_fixed lockstep",
    )
    assert code == 0, err
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n")

    pre = regression_arg_refusal(repo, "tests/test_x.py")
    close = bug_fixed(repo, "B-lockstep", regression_test="tests/test_x.py", agent="bot")
    assert pre is not None and pre.exit == 3 and "is not a pytest node id" in pre.reason
    assert close.exit == pre.exit, (close.exit, close.reason)
    assert "is not a pytest node id" in close.reason
