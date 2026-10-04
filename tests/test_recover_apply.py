"""What `recover --apply` does, and what the recovery help says it does (Bdb449f37cd).

help/recovery.md said "`ddflow recover --apply` adopts it, keeping the work". It does
not: `sweep(apply=True)` records expiry only for a lease whose tree it measured EMPTY and
never touches one holding work. What adopts the tree is `claim --force` on the expired
lease, which binds the existing worktree, work and all. The help now says that, and this
pins both halves: the behaviour and the words.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.api import reporting as RP
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _crashed_with_work(repo: Path) -> Path:
    """T1 claimed by an agent that wrote work and died; its 1 s lease has expired."""
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    text = re.sub(r"(?m)^(ttl_s|grace_s) = .*\n", "", cfg.read_text())
    cfg.write_text(text.replace("[lease]\n", "[lease]\nttl_s = 1\ngrace_s = 0\n", 1))
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t", "globs": ["src/*"]})
    out = LC.claim(repo, "T1", agent="crashed")
    assert out.exit == 0, out.reason
    wt = Path(out.data["worktree"])
    (wt / "work.py").write_text("x = 1  # the only copy\n")
    time.sleep(2.2)
    return wt


def test_apply_leaves_a_tree_holding_work_alone_and_claim_force_adopts_it(repo):
    wt = _crashed_with_work(repo)
    rec = RP.recover(repo, apply=True, agent="op")
    assert [(r.item, r.salvageable) for r in rec.data["_render"]["found"]] == [("T1", True)]
    lease = fold(EventLog(repo, "r").read_all()).items["T1"].lease
    assert lease is not None and lease.holder == "crashed" and not lease.expired_at
    # Not stolen by a plain claim...
    assert LC.claim(repo, "T1", agent="next").exit == 3
    # ...adopted by a forced one: the same tree, with the work in it.
    out = LC.claim(repo, "T1", agent="next", force=True)
    assert out.exit == 0, out.reason
    assert Path(out.data["worktree"]).resolve() == wt.resolve()
    assert (wt / "work.py").read_text().startswith("x = 1")


def test_the_recovery_help_says_what_apply_and_claim_do(repo):
    run_cli(repo, "init")
    code, text, err = run_cli(repo, "help", "recovery")
    assert code == 0, err
    flat = " ".join(text.split())
    assert "`ddflow recover --apply` adopts" not in flat, "the promise the code never kept"
    assert "measured empty" in flat.lower(), flat
    assert "ddflow claim <item> --force" in flat, flat
