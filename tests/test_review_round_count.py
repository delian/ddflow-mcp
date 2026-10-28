"""Every view of one gate's review rounds counts the SAME fold (DX-review-round-count).

`gate status` used to read the CURRENT gate record's evidence while `review triage`
counted the gate's runs in history, so a duplicate critic round left `gate status` at
"1 full round" where `review triage` said "2 of 2 rounds" for the same gate. Now
`gate status`, `review show` and `review triage` all count the history
(`ddflow.services.gates.rounds`), and `review show` names the count. Run N rounds and
every view reports N -- even when the current record's evidence undercounts.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

import ddflow.api.review as api
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.core.records import GateRun, Item
from ddflow.infra.log import EventLog
from ddflow.services import gates as G
from ddflow.services.gates.rounds import delta_rounds, full_rounds, rounds_used

OK, FAIL = 0, 1


def _item(*runs: GateRun) -> Item:
    return Item(id="T1", kind="task", gate_history=list(runs))


def _run(evidence: dict, outcome: str = "failed") -> GateRun:
    return GateRun(gate="critic", outcome=outcome, evidence=evidence)


def test_a_free_retry_of_a_partial_delta_round_charges_no_round(repo):
    """The fold's delta count excludes a `retry_of_partial` just like the full count does:
    two delta runs where the second is the free retry of the first charge one round."""
    it = _item(
        _run({"review_kind": "delta", "status": "PARTIAL"}),
        _run({"review_kind": "delta", "status": "REVIEWED", "retry_of_partial": True}),
    )
    assert delta_rounds(it, "critic") == 1
    assert rounds_used(it, "critic") == 1


def test_a_free_retry_of_a_partial_full_round_charges_no_round(repo):
    """SYMMETRIC: a full-kind `retry_of_partial` is a round of the partial one it retries,
    so two full runs charge one round, not two."""
    it = _item(
        _run({"review_kind": "full", "status": "PARTIAL"}),
        _run({"review_kind": "full", "status": "REVIEWED", "retry_of_partial": True}),
    )
    assert full_rounds(it, "critic") == 1
    assert rounds_used(it, "critic") == 1


def test_a_kept_copy_marked_only_by_an_empty_unavailable_deltas_charges_no_round(repo):
    """`unavailable_deltas` is a MARKER: an empty list still says 'kept copy', so the
    pre-`round_kept` era's re-emitted round is not counted twice."""
    it = _item(
        _run({"review_kind": "full", "status": "REVIEWED"}),
        _run({"review_kind": "full", "status": "REVIEWED", "unavailable_deltas": []}),
    )
    assert full_rounds(it, "critic") == 1
    assert rounds_used(it, "critic") == 1


def test_a_pre_status_era_declared_outcome_charges_a_delta_round_too(repo):
    """The fold's pair of rules is SYMMETRIC: a delta run with no `status` evidence but a
    declared outcome charges a round, exactly like the same shaped full run."""
    it = _item(
        _run({"review_kind": "delta"}, outcome="reviewed"),
        _run({"review_kind": "delta", "status": "REVIEWED"}),
    )
    assert delta_rounds(it, "critic") == 2
    assert rounds_used(it, "critic") == 2
    # the FULL shape charges too: the era rule is one pair, not a delta-only rule
    it = _item(_run({"review_kind": "full"}, outcome="reviewed"))
    assert full_rounds(it, "critic") == 1
    # an outcome-less or unavailable-era record charges nothing, delta or full
    it = _item(
        _run({"review_kind": "delta"}, outcome="unavailable"),
        _run({"review_kind": "full"}, outcome="unavailable"),
    )
    assert rounds_used(it, "critic") == 0


def test_a_carried_status_verdict_under_an_unavailable_outcome_charges_nothing(repo):
    """`status` REVIEWED can be carried evidence; the run's OUTCOME is what became of it.
    An unavailable, errored or refused record charges no round, status or not."""
    it = _item(
        _run({"review_kind": "full", "status": "REVIEWED"}, outcome="unavailable"),
        _run({"review_kind": "delta", "status": "REVIEWED"}, outcome="error"),
        _run({"review_kind": "full", "status": "PARTIAL"}, outcome="refused"),
    )
    assert rounds_used(it, "critic") == 0


def _setup(repo: Path, tmp_path: Path) -> None:
    """A captured project whose command reviewer flags one `FINDME` finding, with the
    SHIPPED round cap (2): the cap the three views must count against."""
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        'case "$in" in *FINDME*) printf \'FINDING HIGH a.py:1\\nbad line\\n\\n'
        "STATUS: FINDINGS 1\\n';; "
        "*) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n[review]\nmax_rounds = 2\ndelta_default = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\n'
    )
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    (repo / "a.py").write_text("x = 1  # FINDME\n")


def _full_review(repo: Path):
    out = api.review(repo, gate="critic", item="T1")
    assert out.data["outcome"] == "failed", out.reason
    return out


def _status(repo: Path) -> str:
    code, out, err = run_cli(repo, "gate", "status", "T1")
    assert code == OK, err
    return out


def test_every_view_reports_n_after_n_rounds(repo, tmp_path):
    """Run N=2 full rounds: gate status, review show and review triage all report 2."""
    _setup(repo, tmp_path)
    _full_review(repo)
    _full_review(repo)
    assert "2 full rounds" in _status(repo), _status(repo)
    shown = api.review_show(repo, "T1", gate="critic")
    assert shown.exit == OK, shown.reason
    assert "2 of 2 rounds -- 2 full, 0 delta" in shown.data["text"], shown.data["text"]
    assert (
        shown.data["rounds"]["critic"]["used"] == shown.data["rounds"]["critic"]["max_rounds"] == 2
    )
    out = _refute(repo, 1)
    assert "after 2 of 2 review rounds" in out, out


def _refute(repo: Path, n: int) -> str:
    code, out, err = run_cli(
        repo,
        "review",
        "triage",
        "T1",
        "--gate",
        "critic",
        "--finding",
        str(n),
        "--refuted",
        "--probe",
        "ran it: no such bug",
    )
    assert code == OK, err
    return out


def test_every_view_still_reports_n_when_the_current_record_undercounts(repo, tmp_path):
    """The incident fold: a duplicate critic round whose record carries `rounds: 1` over
    two history rounds. Gate status must count the history like the budget does."""
    _setup(repo, tmp_path)
    _full_review(repo)
    st = fold(EventLog(repo).read_all(), strict=False)
    rec = st.items["T1"].gates["critic"]
    evidence = {
        **dict(rec.evidence or {}),
        "rounds": 1,
        "delta_rounds": 0,
        "review_kind": "full",
        "status": "REVIEWED",
    }
    G.record(
        EventLog(repo),
        Config.load(repo),
        "T1",
        "critic",
        "failed",
        by=rec.by or "dup-agent",
        evidence=evidence,
    )
    code, out, err = run_cli(repo, "gate", "status", "T1")
    assert code == OK, err
    assert "2 full rounds" in out, out
    shown = api.review_show(repo, "T1", gate="critic")
    assert shown.exit == OK, shown.reason
    assert "2 of 2 rounds -- 2 full, 0 delta" in shown.data["text"], shown.data["text"]
    assert shown.data["rounds"]["critic"]["used"] == 2
    assert "after 2 of 2 review rounds" in _refute(repo, 1)
