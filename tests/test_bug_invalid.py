"""`bug invalid` closes a recorded bug as a FALSE finding -- never as fixed.

B97355c6d15 (a critic's "a cap-blocked item is refused as hopeless") was shown false by a
test. `bug fixed` was the only way to close a bug, and it claims a fix plus a regression
test that fails on the unfixed code; there was none, so the record stayed open forever
and inflated every open-bug count. Closing it as "fixed" instead would have been worse:
a false finding counted as a fix is a fabricated repair in the project's own history.

So the two closures are kept apart everywhere a bug is read: the fold, the status count,
recall's label, the scheduler's bugs-first set, history and the no-progress detector.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import knowledge as K
from ddflow.core.model import fold
from ddflow.infra.log import Event

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

SUITE = """
def test_cap_blocked_is_waitable():
    pass
"""


def _init(repo: Path) -> None:
    run_cli(repo, "init")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_probe.py").write_text(SUITE)
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "cap-blocked item refused")


def _state(repo: Path):
    from ddflow.api import _load

    return _load(repo)[2]


def _open_bugs(repo: Path) -> int:
    return json.loads(run_cli(repo, "--json", "status")[1])["open_bugs"]


def ev(lamport: int, kind: str, subject: str, data: dict) -> Event:
    e = Event(
        kind=kind,
        subject=subject,
        data=data,
        agent="a",
        lamport=lamport,
        ts=f"2026-01-01T00:00:{lamport:02d}Z",
    )
    return Event(**{**e.__dict__, "id": e.compute_id()})


# -- the closure itself ----------------------------------------------------------------


def test_an_open_bug_closed_as_invalid_is_not_open_and_not_fixed(repo):
    _init(repo)
    assert _open_bugs(repo) == 1
    out = K.bug_invalid(
        repo,
        "B1",
        reason="a released slot wakes the waiter; the finding misread the cap",
        evidence="tests/test_probe.py::test_cap_blocked_is_waitable",
    )
    assert out.exit == OK, out
    bug = _state(repo).bugs["B1"]
    assert not bug.open
    assert bug.resolution == "invalid"
    assert not bug.fixed_at, "a false finding must never read as fixed"
    assert bug.invalid_reason.startswith("a released slot")
    assert bug.evidence == "tests/test_probe.py::test_cap_blocked_is_waitable"
    assert _open_bugs(repo) == 0


def test_a_probe_command_is_accepted_as_evidence_unchecked(repo):
    _init(repo)
    out = K.bug_invalid(repo, "B1", reason="not reproducible", evidence="ddflow wait X --once")
    assert out.exit == OK, out
    assert out.data["unchecked"] == ["ddflow wait X --once"]


def test_evidence_naming_a_test_that_does_not_exist_is_refused(repo):
    """The same static resolution `bug fixed --regression-test` applies: a node id that
    names nothing would be evidence pointing at nothing."""
    _init(repo)
    out = K.bug_invalid(repo, "B1", reason="x", evidence="tests/test_probe.py::test_gone")
    assert out.exit == FAIL, out
    assert "tests/test_probe.py::test_gone" in out.reason
    assert _state(repo).bugs["B1"].open


def test_an_unknown_id_is_refused_with_the_bug_fixed_hint(repo):
    _init(repo)
    run_cli(repo, "bug", "found", "--id", "B2", "--item", "T9", "--summary", "y")
    out = K.bug_invalid(repo, "T9", reason="false")
    assert out.exit == REFUSED, out
    assert "B2" in out.reason, "the open bugs linked to that item are named"
    assert "T9" not in _state(repo).bugs, "a refusal folds no phantom bug"


def test_a_fixed_bug_is_refused_and_stays_fixed(repo):
    _init(repo)
    K.bug_fixed(repo, "B1", regression_test="tests/test_probe.py::test_cap_blocked_is_waitable")
    out = K.bug_invalid(repo, "B1", reason="actually false")
    assert out.exit == REFUSED, out
    bug = _state(repo).bugs["B1"]
    assert bug.resolution == "fixed" and not bug.invalid_at


def test_an_already_invalid_bug_is_refused(repo):
    _init(repo)
    assert K.bug_invalid(repo, "B1", reason="first").exit == OK
    again = K.bug_invalid(repo, "B1", reason="second")
    assert again.exit == REFUSED, again
    assert _state(repo).bugs["B1"].invalid_reason == "first"


def test_an_empty_reason_is_refused(repo):
    _init(repo)
    for reason in ("", "   "):
        out = K.bug_invalid(repo, "B1", reason=reason)
        assert out.exit == REFUSED, (reason, out)
    assert _state(repo).bugs["B1"].open


# -- the fold ----------------------------------------------------------------------------


def test_a_later_bug_found_does_not_reopen_an_invalid_bug():
    """Merge, never replace -- the rule `_h_bug_found` already follows for `bug.fixed`."""
    st = fold(
        [
            ev(1, "bug.found", "B1", {"summary": "s"}),
            ev(2, "bug.invalid", "B1", {"reason": "false", "evidence": ""}),
            ev(3, "bug.found", "B1", {"summary": "s again", "item": "T"}),
        ]
    )
    bug = st.bugs["B1"]
    assert not bug.open and bug.resolution == "invalid"
    assert bug.item == "T", "the re-report still merges its fields"


def test_a_re_report_of_an_invalid_bug_says_so(repo):
    """Not reopening is only half: the reporter must be TOLD the id is closed, or a real
    recurrence under the same id vanishes silently."""
    _init(repo)
    K.bug_invalid(repo, "B1", reason="false")
    out = K.bug_found(repo, summary="again", id="B1")
    assert out.data.get("resolution") == "invalid", out.data
    code, stdout, _ = run_cli(repo, "bug", "found", "--id", "B1", "--summary", "again")
    assert code == OK and "invalid" in stdout, stdout


def test_a_fix_wins_over_invalid_in_either_fold_order():
    """Shard merges reorder. A bug that was later shown real and fixed is fixed, whichever
    of the two events the merge sorts first."""
    fixed = ev(3, "bug.fixed", "B1", {"regression_test": "t.py::t"})
    invalid = ev(2, "bug.invalid", "B1", {"reason": "r", "evidence": ""})
    found = ev(1, "bug.found", "B1", {"summary": "s"})
    for order in ([found, invalid, fixed], [found, fixed, invalid]):
        bug = fold(order).bugs["B1"]
        assert bug.resolution == "fixed" and not bug.open, order


# -- every reader shows invalid distinctly from fixed ------------------------------------


def test_recall_labels_an_invalid_bug_invalid_with_its_reason(repo):
    _init(repo)
    K.bug_invalid(repo, "B1", reason="the waiter wakes on release")
    _code, out, _ = run_cli(repo, "recall", "cap-blocked item refused", "--sources", "bugs")
    assert "[invalid]" in out, out
    assert "the waiter wakes on release" in out, out
    assert "[fixed]" not in out and "[OPEN]" not in out, out


def test_history_names_the_closure_invalid_not_fixed(repo):
    _init(repo)
    K.bug_invalid(repo, "B1", reason="false")
    _code, out, _ = run_cli(repo, "history", "--kind", "bug")
    assert "bug closed as invalid" in out, out
    assert "bug fixed" not in out, out


def test_closing_as_invalid_is_progress_but_not_a_fix():
    from ddflow.core.progress import PROGRESS_KINDS

    assert "bug.invalid" in PROGRESS_KINDS


def test_an_invalid_bug_no_longer_marks_its_item_a_bug_fix():
    """`bugs_first` offers an item an OPEN bug names first. A false finding is not a bug."""
    from ddflow.config import Config
    from ddflow.core.schedule import bug_items

    st = fold(
        [
            ev(1, "task.added", "T", {"parent": "P"}),
            ev(2, "bug.found", "B1", {"summary": "s", "item": "T"}),
            ev(3, "bug.invalid", "B1", {"reason": "r", "evidence": ""}),
        ]
    )
    assert "T" not in bug_items(st, Config())


def test_the_open_bug_obligation_offers_the_invalid_closure():
    """The end-of-session reminder named only `ddflow_bug_fixed`, which pushes an agent
    holding a false finding toward claiming a fix it never made."""
    from ddflow.config import Config
    from ddflow.services.obligations import outstanding

    st = fold([ev(1, "bug.found", "B1", {"summary": "s"})])
    (ob,) = [o for o in outstanding(st, Config()) if o.kind == "open_bug"]
    assert "bug_invalid" in ob.render(), ob.render()
    st = fold(
        [
            ev(1, "bug.found", "B1", {"summary": "s"}),
            ev(2, "bug.invalid", "B1", {"reason": "r", "evidence": ""}),
        ]
    )
    assert not [o for o in outstanding(st, Config()) if o.kind == "open_bug"]


# -- the CLI and MCP surfaces ------------------------------------------------------------


def test_the_cli_closes_as_invalid_and_refuses_with_exit_3(repo):
    _init(repo)
    code, out, err = run_cli(repo, "bug", "invalid", "B1", "--reason", "false finding")
    assert code == OK, (out, err)
    assert "invalid" in out
    assert _open_bugs(repo) == 0
    code, _out, err = run_cli(repo, "bug", "invalid", "B1", "--reason", "again")
    assert code == REFUSED, err
    code, _out, err = run_cli(repo, "bug", "invalid", "NOPE", "--reason", "x")
    assert code == REFUSED, err


def test_the_cli_bug_fixed_does_not_swallow_a_refusal(repo):
    """B-cli-bugfixed-refusal-ok: `cmd_bug` checked only exit 1, so an unknown id printed
    'bug NOPE closed' and exited 0 while the API had refused and appended nothing."""
    _init(repo)
    code, out, err = run_cli(repo, "bug", "fixed", "NOPE", "--regression-test", "x")
    assert code == REFUSED, (code, out, err)
    assert "closed" not in out, out
    assert "no bug NOPE" in err, err


def _mcp(repo: Path, name: str, arguments: dict) -> dict:
    from ddflow.surfaces.mcp import Server

    return Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )["result"]


def test_mcp_and_cli_agree(repo):
    _init(repo)
    run_cli(repo, "bug", "found", "--id", "B2", "--summary", "other")
    reply = _mcp(
        repo,
        "ddflow_bug_invalid",
        {"id": "B1", "reason": "false", "evidence": "ddflow wait X"},
    )
    assert reply["_meta"]["exit"] == OK, reply
    from_mcp = json.loads(reply["content"][0]["text"])
    code, out, _ = run_cli(
        repo, "--json", "bug", "invalid", "B2", "--reason", "false", "--evidence", "ddflow wait X"
    )
    assert code == OK
    from_cli = json.loads(out)
    assert {**from_mcp, "id": "B2"} == from_cli, (from_mcp, from_cli)
    refused = _mcp(repo, "ddflow_bug_invalid", {"id": "B1", "reason": "again"})
    assert refused["_meta"]["exit"] == REFUSED, refused
