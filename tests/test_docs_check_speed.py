"""Speed ratchet for `ddflow docs check` on this repository (DX-docs-check-speed).

The help walk used to spawn `ddflow <verb> --help` once per verb (~4 minutes). ddflow's own
help is answered in process now; the equivalence with the spawned walk is pinned in
test_docscheck_help_in_process.py. This ratchet pins the COST on the real tree: the number
of subprocesses the walk spawns for ddflow is zero, and the walk stays well under a budget.
"""

import time
from pathlib import Path

from ddflow.infra import proc as P
from ddflow.services import docscheck as D
from ddflow.services import docscheck_help as H

ROOT = Path(__file__).resolve().parents[1]

#: Subprocesses the ddflow help walk may spawn. A ratchet: lower it, never raise it.
MAX_SPAWNS = 0
#: Generous wall budget for the whole walk; the walk takes seconds, the bug took minutes.
MAX_SECONDS = 60


def test_the_real_tree_help_walk_spawns_nothing_and_stays_fast(monkeypatch):
    spawned = []

    def capture(argv, **kwargs):
        spawned.append(list(argv))
        return P.Captured(error=RuntimeError("spawned"))

    monkeypatch.setattr(P, "capture", capture)
    start = time.monotonic()
    D.check_docs(ROOT, runner=H.command_runner(ROOT, 60), commands=["ddflow"])
    elapsed = time.monotonic() - start
    assert len(spawned) <= MAX_SPAWNS, spawned[:5]
    assert elapsed < MAX_SECONDS, elapsed
