"""Shared setup helpers of the delta re-review tests (split from test_review_delta.py)."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _delta_on(review_toml: str) -> str:
    """``review_toml`` with `delta_default = true` unless it says otherwise: the delta is
    what these tests are about, and it is no longer the shipped default."""
    # Unlimited rounds unless the test sets them: since D-gate-economy 2 deltas count
    # against review.max_rounds, and these tests are about the delta, not the cap.
    extra = ("" if "delta_default" in review_toml else "delta_default = true\n") + (
        "" if "max_rounds" in review_toml else "max_rounds = 0\n"
    )
    if not extra:
        return review_toml
    if "[review]\n" in review_toml:
        return review_toml.replace("[review]\n", "[review]\n" + extra, 1)
    return "[review]\n" + extra + review_toml


def _setup(repo: Path, tmp_path: Path, review_toml: str = "") -> Path:
    """A project with a command reviewer that flags every `FINDME` line (the finding's
    text is the line's own, so two reviews of the same line are byte-identical findings),
    and a feature branch `feat` with a large committed change."""
    seen = tmp_path / "seen.txt"
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        f"printf 'call\\n' >> '{seen}'\n"
        'case "$in" in *FINDME-H*) printf \'FINDING HIGH z.py:1\\nnew high problem\\n\\n'
        "STATUS: FINDINGS 1\\n';; "
        "*FINDME-B*) printf 'FINDING MEDIUM y.py:1\\nsecond problem\\n\\nSTATUS: FINDINGS 1\\n';; "
        "*FINDME*) printf 'FINDING HIGH x.py:1\\nbad\\n\\nSTATUS: FINDINGS 1\\n';; "
        "*) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n' + _delta_on(review_toml)
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "big.py").write_text("".join(f"line_{n} = {n}\n" for n in range(400)))
    (repo / "x.py").write_text("x = 1  # FINDME\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    return seen


def _review(repo: Path, **kw):
    return api.review(repo, gate=kw.pop("gate", "critic"), item="T1", branch="feat", **kw)


def _fix(repo: Path, name: str = "fix.py", text: str = "ok = 1\n") -> None:
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", f"fix {name}")


def _ev(repo: Path, gate: str = "critic") -> dict:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates[gate].evidence


def _record(repo: Path, gate: str = "critic"):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates[gate]


def _calls(seen: Path) -> int:
    return seen.read_text().count("call") if seen.exists() else 0


# -- the knob ----------------------------------------------------------------------------
