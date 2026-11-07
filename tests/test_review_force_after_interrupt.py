"""A forced re-review after a SIGTERM'd one names the killed attempt instead of looking
like a fresh round (bug B-ddflow-review-force-duplicate-round).

`ddflow review --gate ... --force --reason` after a foreground review was cut short
re-ran the same reviewers and reported it as "rounds used 1/N, justified: asked for
explicitly", so a reviewer paid twice looked like the first. The terminated run records
its signal in the gate's evidence, and the forced follow-up says it repeats that attempt
-- the "way to tell it was repeating" the bug names.
"""

from __future__ import annotations

import shlex
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.api.review.gate import _terminated_gate
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import gates as GD

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _setup(repo: Path, tmp_path: Path) -> Path:
    """A command reviewer that flags FINDME; item T1 with an uncommitted change holding it."""
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        "case \"$in\" in *FINDME*) printf 'FINDING HIGH x.py:1\\nbad\\n\\nSTATUS: FINDINGS 1\\n';; "
        "*) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    q = shlex.quote(str(cli)).replace("\\", "\\\\").replace('"', '\\"')
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{q}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    (repo / "x.py").write_text("x = 1  # FINDME\n")
    return cli


def _last_run(repo: Path, gate: str):
    item = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    return item.runs_of(gate)[-1]


def test_a_terminated_review_with_nothing_reviewed_marks_its_signal(repo, tmp_path):
    """The SIGTERM record for a review that died before a reviewer finished says WHAT killed
    it, not only `review_kind = delta`: a later run over the same record can tell this is a
    repeat of an attempt whose reviewers were paid and their findings lost."""
    _setup(repo, tmp_path)
    log, cfg, st = api._load(repo, "")
    gates = GD.load_gates(repo, cfg)
    _terminated_gate(repo, log, cfg, st, "T1", "critic", "SIGTERM", None, gates)
    last = _last_run(repo, "critic")
    assert last.outcome == "unavailable"
    assert last.evidence.get("terminated_by") == "SIGTERM", last.evidence
    assert last.evidence.get("status") == "UNAVAILABLE", last.evidence


def test_a_forced_rereview_after_a_terminated_one_names_the_repeat(repo, tmp_path):
    """The follow-up `--force --reason` names the killed attempt it repeats instead of
    reporting itself as a fresh "asked for explicitly" round."""
    _setup(repo, tmp_path)
    log, cfg, st = api._load(repo, "")
    gates = GD.load_gates(repo, cfg)
    _terminated_gate(repo, log, cfg, st, "T1", "critic", "SIGTERM", None, gates)

    out = api.review(repo, gate="critic", item="T1", force=True, reason="finish the review")

    assert out.exit == OK, out.reason
    assert "repeats the review terminated by SIGTERM" in out.data["text"], out.data["text"]
