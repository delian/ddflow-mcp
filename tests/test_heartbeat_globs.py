"""A heartbeat that revives a lapsed lease takes the item's CURRENT globs (Bb21d338f26).

`update --globs` retargets a LIVE lease, and deliberately leaves an expired one alone for
recovery. The holder's next heartbeat then revived that lease with its claim-time globs,
so the paths just added were reported at commit time as "not covered by a lease you
hold" -- with `update --globs`, the remedy the hook prints, already done. Seen live on
B-prepush-isolated (demos/, pyproject.toml added while its lease had lapsed).

The revival goes through the same overlap check as any widening: paths another agent has
taken in the meantime are not quietly granted.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _setup(repo: Path, monkeypatch) -> None:
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(re.sub(r"(?m)^\[lease\]$", "[lease]\ngrace_s = 0", cfg.read_text(), count=1))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "ddflow"], check=True)
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "i/*")
    monkeypatch.setenv("DDFLOW_LEASE_TTL_S", "1")
    code, out, err = run_cli(repo, "claim", "T1", "--no-worktree", agent="alpha")
    assert code == OK, out + err
    monkeypatch.delenv("DDFLOW_LEASE_TTL_S")
    time.sleep(2.5)  # past its TTL; grace is 0


def _lease_globs(repo: Path) -> list[str]:
    lease = fold(EventLog(repo).read_all(), strict=False).items["T1"].lease
    return list(lease.globs) if lease else []


def test_the_revived_lease_covers_the_globs_added_while_it_had_lapsed(repo, monkeypatch):
    _setup(repo, monkeypatch)
    assert run_cli(repo, "update", "T1", "--globs", "i/*,j/*", agent="alpha")[0] == OK
    code, out, err = run_cli(repo, "heartbeat", "T1", agent="alpha")
    assert code == OK, out + err
    assert sorted(_lease_globs(repo)) == ["i/*", "j/*"]


def test_a_revival_does_not_take_paths_another_agent_holds_now(repo, monkeypatch):
    _setup(repo, monkeypatch)
    assert run_cli(repo, "update", "T1", "--globs", "i/*,j/*", agent="alpha")[0] == OK
    run_cli(repo, "task", "add", "T2", "--title", "t2", "--globs", "j/*")
    code, out, err = run_cli(repo, "claim", "T2", "--no-worktree", agent="beta")
    assert code == OK, out + err
    code, out, err = run_cli(repo, "heartbeat", "T1", agent="alpha")
    assert code == OK, out + err  # the lease itself is renewed ...
    assert _lease_globs(repo) == ["i/*"]  # ... without the path beta holds
    assert "j/*" in out + err and "T2" in out + err  # and alpha is told why


def test_globs_cleared_while_it_had_lapsed_are_cleared_on_the_lease_too(repo, monkeypatch):
    """The mirror image: a revived lease left on paths the item no longer declares keeps
    holding them against every other agent. Found by the rubber_duck review."""
    _setup(repo, monkeypatch)
    assert run_cli(repo, "update", "T1", "--globs", "", agent="alpha")[0] == OK
    assert run_cli(repo, "heartbeat", "T1", agent="alpha")[0] == OK
    assert _lease_globs(repo) == []
