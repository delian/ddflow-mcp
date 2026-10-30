"""A delta round that re-raises an already-triaged design point under a new digest keeps
the triage (bug B-review-delta-retriages-same-point).

On HX-facts-core the rubber_duck/critic delta rounds re-sent the same documented limit
(the falsy value) at a shifted line across four rounds, so each came back under a new
digest and the `confirmed`/`refuted` verdict -- keyed by the digest of the exact text
(decision D-review-triage) -- never carried: the budget was spent re-triaging one claim.
A delta finding citing the SAME file and function/symbol as an already-triaged earlier
finding is the same design point; its verdict carries instead of re-opening the triage.
"""

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
from ddflow.services import gates as G

OK = 0


def _setup(repo: Path, tmp_path: Path) -> Path:
    """A command reviewer that flags a `FINDME` line with a finding whose cited symbol and
    text come from `wording` (the file:symbol first, the paragraph after the `|`), so a test
    can move the symbol and reword the claim between rounds."""
    wording = tmp_path / "wording"
    wording.write_text("x.py:set_limit|the documented limit is falsy")
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        f"w=$(cat '{wording}')\n"
        'loc=${w%%|*}\n'
        'det=${w#*|}\n'
        'case "$in" in '
        "*FINDME*) printf 'FINDING HIGH %s\\n%s\\n\\nSTATUS: FINDINGS 1\\n' \"$loc\" \"$det\";; "
        '*) echo "STATUS: NO FINDINGS";; esac\n'
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n[review]\nmax_rounds = 0\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("def set_limit():\n    return 0  # FINDME\n" + "line = 0\n" * 8)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    return wording


def _state(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"]


def _ev(repo: Path) -> dict:
    return _state(repo).gates["critic"].evidence


def _review(repo: Path, **kw):
    return api.review(repo, gate="critic", item="T1", branch="feat", **kw)


def test_a_design_point_keeps_a_symbol_and_drops_a_bare_line():
    from ddflow.api.review import _design_point

    assert _design_point("ddflow/config.py:diff_fence") == "ddflow/config.py:diff_fence"
    assert _design_point("x.py:set_limit") == "x.py:set_limit"
    # a bare line names no function/symbol, so it is NOT a design point: two line-cited
    # findings in one file are distinct.
    assert _design_point("ddflow/config.py:123") == ""
    assert _design_point("ddflow/config.py:123-130") == ""
    assert _design_point("") == ""


def test_a_refuted_design_point_raised_again_reworded_inherits_the_verdict(repo, tmp_path):
    """The same claim at `x.py:set_limit` triaged refuted, then re-raised reworded with a new
    digest, is one claim: the delta carries the refutation, not a fresh one."""
    wording = _setup(repo, tmp_path)
    out = _review(repo)  # full round
    assert out.exit == OK and _ev(repo)["review_kind"] == "full", out.reason
    probe = "ran the suite: the limit is guarded on every path"
    assert api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe=probe).exit == OK

    wording.write_text("x.py:set_limit|the documented default is falsy and undocumented")
    (repo / "x.py").write_text("def set_limit():\n    return 1  # FINDME moved\n" + "line = 0\n" * 8)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "moved")
    out = _review(repo, delta=True)
    assert out.exit == OK, out.reason
    ev = _ev(repo)
    assert ev["review_kind"] == "delta", ev
    counts = G.triage_counts(_state(repo), "critic")
    # the earlier finding keeps its refutation, and the re-raised one INHERITS it: no
    # untriaged finding re-opens the triage.
    assert counts == {"findings": 2, "refuted": 2, "confirmed": 0, "untriaged": 0}, counts


def test_a_confirmed_design_point_raised_again_is_not_re_triaged(repo, tmp_path):
    """A `confirmed` verdict carries too: the author already agreed it is a defect and is
    fixing it; a delta re-raising the same point does not start the cycle over."""
    wording = _setup(repo, tmp_path)
    assert _review(repo).exit == OK
    probe = "fixed it: the limit is now truthy and documented"
    assert api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe=probe).exit == OK

    wording.write_text("x.py:set_limit|the default value is falsy")
    (repo / "x.py").write_text("def set_limit():\n    return 1  # FINDME moved\n" + "line = 0\n" * 8)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "moved")
    assert _review(repo, delta=True).exit == OK
    counts = G.triage_counts(_state(repo), "critic")
    assert counts["confirmed"] == 2 and counts["untriaged"] == 0, counts


def test_a_line_cited_finding_in_the_same_file_is_not_carried(repo, tmp_path):
    """A verdict does NOT carry across a bare `path:line`: without a function/symbol two
    line-cited findings in one file stay distinct, so an unrelated finding is not hidden."""
    wording = _setup(repo, tmp_path)
    wording.write_text("x.py:7|the documented limit is falsy")
    assert _review(repo).exit == OK
    api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="p").exit == OK
    wording.write_text("x.py:90|an unrelated new defect")
    (repo / "x.py").write_text("def set_limit():\n    return 1  # FINDME moved\n" + "line = 0\n" * 40)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "moved")
    assert _review(repo, delta=True).exit == OK
    counts = G.triage_counts(_state(repo), "critic")
    # the re-raised line-cited finding stays untriaged: no function/symbol to carry on.
    assert counts["untriaged"] == 1 and counts["refuted"] == 1, counts
