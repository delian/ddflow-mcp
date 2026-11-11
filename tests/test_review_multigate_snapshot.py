"""A second gate in one `review --gate rubber_duck,critic` call must review the tree as it
is when THAT gate runs, not the snapshot the first gate saw (bug B-review-critic-restarts-
after-rubber-duck-fail).

The two gates are served by different reviewers, so the combined review does not apply and
the gates run one after the other. While the first gate's reviewer runs it fixes what it
found; the second gate, run by the same call once the first finishes, must then see the
fixed tree -- or it re-reports a finding already fixed (the operator watched the same
7-chunk review run twice for ~11 and ~17 minutes and come back near-identical).
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_review_snapshot_at_start import _git

from ddflow.api import review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _state(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"]


def test_the_second_gate_sees_a_fix_the_first_gate_made_while_it_ran(repo, tmp_path):
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(
        "[worktree]\nenabled = true\n[review]\nmax_rounds = 0\n"
        f'[[reviewer]]\nname = "duck"\nkind = "command"\ncommand = "{tmp_path}/duck"\n'
        'model = "gemini-2.5-pro"\ngates = ["rubber_duck"]\nhedge = 1\nmax_chunk_chars = 80\n'
        f'[[reviewer]]\nname = "crit"\nkind = "command"\ncommand = "{tmp_path}/crit"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\nhedge = 1\nmax_chunk_chars = 80\n'
    )
    duck = tmp_path / "duck"
    duck.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        "case \"$in\" in *FINDA*) printf 'FINDING HIGH a.py:1\\nduck saw it\\n\\n';; esac\n"
        "case \"$in\" in *FIND*) echo 'STATUS: FINDINGS 1';; *) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    crit = tmp_path / "crit"
    crit.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        "case \"$in\" in *FINDA*) printf 'FINDING HIGH a.py:1\\ncrit saw it\\n\\n';; esac\n"
        "case \"$in\" in *FIND*) echo 'STATUS: FINDINGS 1';; *) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    duck.chmod(duck.stat().st_mode | stat.S_IXUSR)
    crit.chmod(crit.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    run_cli(repo, "claim", "T1")
    wt = (repo / _state(repo).worktree).resolve()
    (wt / "a.py").write_text("x = 1  # FINDA " + "p" * 40 + "\n")
    _git(wt, "add", "-A")
    # The first gate's duck reviewer repairs a.py while it runs (the fix a reviewer
    # prompts, COMMITTED so the item's diff carries it): the second gate is sent only
    # after duck finishes, and must then see a.py without the FINDA it found.
    duck.write_text(
        duck.read_text().replace(
            "case \"$in\" in *FINDA*) printf",
            f"sed -i 's/FINDA/FIXED/' '{wt}/a.py'; git -C '{wt}' add a.py; case \"$in\" in *FINDA*) printf",
            1,
        )
    )
    api.review(repo, gate="rubber_duck,critic", item="T1", full=True)
    crit_found = [
        f["title"]
        for f in _state(repo).gates["critic"].evidence.get("chunk_findings", [])
    ]
    assert "crit saw it" not in crit_found, crit_found
