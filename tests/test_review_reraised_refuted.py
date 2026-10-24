"""An identical finding another gate raises again is auto-linked to the refuted triage
(bug B74d5c39e6e).

fix-B1d51fb9da1's critic finding (wait_max_s KeyError) was refuted with a passing test
probe; the next round's rubber_duck raised the identical claim and failed the gate, so
the author was asked to settle the same refutation twice, once per gate. Word-for-word
identical findings share a digest, and a triage keyed by that digest is a triage of the
one claim (decision D-review-triage): when a review round of ANY gate raises a finding
byte-identical to one the item already refuted, the triage is recorded on that gate too,
carrying the original probe -- one claim, one refutation.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import gates as G

OK = 0


def _setup(repo: Path, tmp_path: Path) -> Path:
    """A command reviewer that reports FINDA's finding in the wording of `wording` (so a
    test can change what it claims) and a fixed one for FINDB, on critic and rubber_duck."""
    wording = tmp_path / "wording"
    wording.write_text("wait_max_s KeyError")
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        f"w=$(cat '{wording}')\n"
        'case "$in" in *FINDA*) printf "FINDING HIGH a.py:1\\n$w\\n\\n";; esac\n'
        'case "$in" in *FINDB*) printf "FINDING LOW b.py:1\\nsecond finding\\n\\n";; esac\n'
        'case "$in" in *FIND*) echo "STATUS: FINDINGS 1";; *) echo "STATUS: NO FINDINGS";; esac\n'
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n[review]\nmax_rounds = 0\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\nhedge = 1\nmax_chunk_chars = 150\n'
    )
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    for name, mark in (("a.py", "FINDA"), ("b.py", "FINDB"), ("c.py", "clean")):
        (repo / name).write_text(f"x = 1  # {mark} " + "p" * 60 + "\n")
    return wording


def _state(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"]


def _triaged(repo: Path, gate: str):
    return [
        e for e in EventLog(repo).read_all() if e.kind == "review.triaged" and e.data["gate"] == gate
    ]


def _gui(repo: Path, gate: str, probe: str):
    out = api.review(repo, gate=gate, item="T1")
    assert out.data["outcome"] == "failed", out.reason
    api.triage(repo, "T1", gate=gate, finding=1, verdict="refuted", probe=probe)
    api.triage(repo, "T1", gate=gate, finding=2, verdict="refuted", probe=probe)


def test_an_identical_finding_another_gate_raised_is_auto_linked(repo, tmp_path):
    """The SAME claim rubber_duck raises again needs no second refutation: it is triaged
    with the critic's probe when the round is recorded."""
    _setup(repo, tmp_path)
    probe = "ran the suite twice: wait_max_s is set on every path"
    _gui(repo, "critic", probe)
    out = api.review(repo, gate="rubber_duck", item="T1")
    assert out.data["outcome"] == "failed", out.reason
    counts = G.triage_counts(_state(repo), "rubber_duck")
    assert counts["refuted"] == 2, counts
    assert counts["untriaged"] == 0, counts
    got = _triaged(repo, "rubber_duck")
    assert len(got) == 2 and {e.data["verdict"] for e in got} == {"refuted"}
    assert all(e.data["probe"] == probe for e in got), got
    out = api.review(repo, gate="critic", item="T1")
    # The record of the settled gate is untouched: the auto-links landed on rubber_duck.
    assert len(_triaged(repo, "critic")) == 2, _triaged(repo, "critic")
    assert type(out).__name__ == "Outcome"


def test_a_differently_worded_finding_is_not_auto_linked(repo, tmp_path):
    """Auto-linking is keyed by the digest of the EXACT text: a rubber_duck claim the refuted
    one is only almost reads as a new claim and stays untriaged."""
    wording = _setup(repo, tmp_path)
    probe = "ran the suite twice: wait_max_s is set on every path"
    _gui(repo, "critic", probe)
    wording.write_text("wait_max_s KeyError in the retry loop")
    out = api.review(repo, gate="rubber_duck", item="T1")
    assert out.data["outcome"] == "failed", out.reason
    counts = G.triage_counts(_state(repo), "rubber_duck")
    assert counts == {"findings": 2, "refuted": 1, "confirmed": 0, "untriaged": 1}, counts
    got = _triaged(repo, "rubber_duck")
    assert len(got) == 1 and got[0].data["probe"] == probe
