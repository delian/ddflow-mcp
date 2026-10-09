"""Every reader of the ready set goes through `plan_for` (B-uni-plan-for-all).

`next` asks the scheduler with the waiters' reservation hold and the parallelism limit.
`status`, the progress line after a completion, the MCP handshake and the refused-claim
alternatives asked the bare `plan()` (the hold missing), so they could call "ready" an item
`next` held back for the waiter in line. These tests build that situation -- agent B is
queued for a file, A has just let go of it -- and hold every surface to `next`'s answer.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as A
from ddflow.api import reporting as RP
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import leases as L
from ddflow.services import progress_line as PL
from ddflow.services import waits as WT

HOLDER, B, C = "agent-a", "agent-b", "agent-c"


@pytest.fixture
def proj(repo: Path) -> Path:
    """HOT is released; B is first in line for src/mcp.py (TB), so TC (same file) and TD
    (another file) are what C may be offered: TC is RESERVED for B, TD is free."""
    run_cli(repo, "init")
    for item, globs in (
        ("HOT", "src/mcp.py"),
        ("TB", "src/mcp.py"),
        ("TC", "src/mcp.py"),
        ("TD", "src/other.py"),
    ):
        run_cli(repo, "task", "add", item, "--globs", globs)
    assert A.claim(repo, "HOT", no_worktree=True, agent=HOLDER).ok
    WT.register(
        repo,
        WT.Waiter(
            agent=B, item="TB", waiting_on=["HOT"], since=time.time() - 120, until=time.time() + 600
        ),
    )
    assert A.release(repo, "HOT", agent=HOLDER).ok
    return repo


def _offered(repo: Path, agent: str) -> set[str]:
    out = A.next_(repo, agent=agent)
    return {r["id"] for r in out.data["ready"]}


def test_next_holds_the_reserved_item_back_from_a_younger_agent(proj):
    """The premise every other test builds on."""
    assert "TC" not in _offered(proj, C) and "TD" in _offered(proj, C)


def test_status_counts_ready_the_way_next_offers_it(proj):
    status = RP.status(proj, agent=C)
    assert status.data["tasks"]["ready"] == len(_offered(proj, C)), (
        "status called ready an item next withholds for the waiter in line"
    )


def test_the_progress_block_names_what_next_would_offer(proj):
    log, cfg = EventLog(proj, C), Config.load(proj)
    st = fold(log.read_all(), strict=False)
    text = PL.report(st, cfg, "", mode="on", plan=_view(proj, C))
    assert "Next:" in text and "TC" not in text.split("Next:")[1].split("\n")[0]


def _view(repo: Path, agent: str):
    from ddflow.api.lifecycle import plan_for

    log, cfg = EventLog(repo, agent), Config.load(repo)
    return plan_for(repo, log, cfg, fold(log.read_all(), strict=False), purpose="view", agent=agent)


def test_acquire_names_the_alternatives_its_caller_says_and_the_bare_scheduler_otherwise(proj):
    """`services.leases.acquire(offer=...)`: the API passes the offer `next` makes."""
    log, cfg = EventLog(proj, C), Config.load(proj)
    assert A.claim(proj, "TD", no_worktree=True, agent=B).ok  # B holds TD
    with pytest.raises(L.LeaseError) as bare:
        L.acquire(log, cfg, "TD", holder=C)
    assert bare.value.alternatives, "no hook: the scheduler's own answer, as before"

    said = []

    def offer(state, item_id, holder, now):
        said.append((item_id, holder))
        return ["X"]

    with pytest.raises(L.LeaseError) as hooked:
        L.acquire(log, cfg, "TD", holder=C, offer=offer)
    assert hooked.value.alternatives == ["X"] and said == [("TD", C)]


def test_a_claim_refused_for_a_held_item_names_what_next_offers(proj):
    """Through the API: the alternatives come from `plan_for`, so a reserved item is not one."""
    assert A.claim(proj, "TD", no_worktree=True, agent=B).ok
    out = A.claim(proj, "TD", no_worktree=True, agent=C)
    assert not out.ok
    named = set(out.data.get("alternatives") or [])
    assert "TC" not in named, "TC is reserved for the waiter in line: next withholds it"
    assert named <= _offered(proj, C) | {"TB"}, (named, _offered(proj, C))


def test_no_surface_asks_the_bare_scheduler(proj):
    """A source check: the readers that agree with `next` do not call `core.schedule.plan`."""
    root = Path(__file__).resolve().parents[1] / "ddflow"
    for rel in (
        "api/reporting/overview.py",
        "api/reporting/health.py",
        "services/progress_line.py",
    ):
        text = (root / rel).read_text()
        # the only bare call left is progress_line's fallback when the caller passes no plan
        text = text.replace("plan = plan or S.plan(st, cfg)", "")
        assert "plan(st, cfg" not in text and "S.plan(" not in text, rel
