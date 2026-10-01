"""`update --resources` on a claimed item reaches the lease (Bc496508f6b).

The resource-capacity check (`schedule.resource_shortfall`) reads `lease.resources`, and
`update --resources` changed `item.resources` only -- the gap B209 closed for globs. A
holder that raised its reservation from nothing to `gpu:8` kept reserving nothing, so a
second agent was handed the same GPUs.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

from ddflow.api import items as AI
from ddflow.api import lifecycle as A
from ddflow.core import outcome as O
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

HOLDER, OTHER = "agent-holder", "agent-other"


def _items(repo: Path):
    return fold(EventLog(repo, "probe").read_all(), strict=False).items


def _gpus(repo: Path, n: int) -> None:
    run_cli(repo, "init")
    code, _o, err = run_cli(repo, "config", "--set", "schedule.resources", f'["gpu={n}"]')
    assert code == 0, err


def test_update_resources_on_a_claimed_item_moves_the_lease(repo):
    _gpus(repo, 2)
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    code, _o, err = run_cli(repo, "update", "T1", "--resources", "gpu:2", agent=HOLDER)
    assert code == 0, err
    it = _items(repo)["T1"]
    assert it.resources == ["gpu:2"]
    assert it.lease.resources == ["gpu:2"], "the capacity check reads the lease"
    # ...so a second claim wanting the same GPUs is refused.
    run_cli(repo, "task", "add", "T2", "--globs", "b.py")
    run_cli(repo, "update", "T2", "--resources", "gpu:1")
    out = A.claim(repo, "T2", no_worktree=True, agent=OTHER)
    assert out.exit == O.REFUSED, out.reason
    assert "gpu" in out.reason


def test_update_resources_beyond_free_capacity_is_refused_and_records_nothing(repo):
    _gpus(repo, 2)
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "b.py")
    run_cli(repo, "update", "T2", "--resources", "gpu:2")
    assert A.claim(repo, "T2", no_worktree=True, agent=OTHER).ok
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    before = len(EventLog(repo, "probe").read_all())
    out = AI.update(repo, "T1", AI.ItemEdit(resources=["gpu:1"]), agent=HOLDER)
    assert out.exit == O.REFUSED, out.reason
    assert "gpu" in out.reason
    assert len(EventLog(repo, "probe").read_all()) == before, "a refusal recorded something"
    it = _items(repo)["T1"]
    assert it.resources == [] and it.lease.resources == []


def test_update_resources_keeps_the_lease_life_and_globs(repo):
    _gpus(repo, 4)
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    renewed = _items(repo)["T1"].lease.renewed_at
    assert AI.update(repo, "T1", AI.ItemEdit(resources=["gpu:1"]), agent=HOLDER).ok
    lz = _items(repo)["T1"].lease
    assert lz.renewed_at == renewed, "an edit is not a renewal"
    assert lz.globs == ["a.py"] and lz.holder == HOLDER
