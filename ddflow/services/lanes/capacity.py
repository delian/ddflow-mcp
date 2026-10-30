"""How many lanes a local endpoint or a GPU pool can serve at once (R-swarm-heterogeneous-lanes, gap 2).

Three parts, one idea: a pool is ``(name, limit, need)`` and a launched lane holds ``need``
slots of it for as long as it runs.

* **Pure view** (`pools_for`, `view`, `lane_figures`, `refusals`, `headroom_term`): the limit of
  each pool and how many slots are busy go in as numbers; the figures SW-score reads come out
  (``Lane.live``/``Lane.cap``, a refusal reason, a headroom term in [0, 1]). No I/O.
* **Slot probe and hold** (`busy_counts`, `acquire`): the filesystem half, `slots.Slots` (one
  flock per slot, so a holder that dies frees its slot with the process; no second slot
  implementation). A lane takes all its slots of all its pools or none in one sweep and holds nothing between
  sweeps, so two lanes wanting the same pools cannot deadlock on each other.
* **No lease while waiting** (r-lock-cycle-selfheal): nothing here reads or writes a lease or
  the event log, and nothing starts a daemon (D-daemons): the wait is a bounded poll of the
  caller's own process, ``timeout_s`` long.

Where the limit comes from, first that exists: the route's ``concurrency`` fact (the catalog),
the ``[schedule] resources`` capacity of the pool's name (the same ``name=count`` knob GPUs
use), then ``[lanes] endpoint_slots``. A GPU pool is named by the task ``--resources`` syntax
(``gpu:4`` is need 4 of pool ``gpu``). Endpoint addresses never enter a pool name: it is the
route name.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ...config import Config
from ...core.schedule import parse_resources
from .. import slots as SLOTS
from .endpoint import Endpoint

#: Pool name of an endpoint route: a plain file-name stem (a route is data, never a path).
#: Longest route stem before it is cut to a head plus a digest (a lock-file name stays short).
_STEM_MAX = 120
_STEM = re.compile(r"[^A-Za-z0-9-]")


@dataclass(frozen=True)
class Pool:
    name: str
    limit: int
    need: int = 1  #: slots one lane holds of this pool


@dataclass(frozen=True)
class Headroom:
    pool: str
    limit: int
    busy: int
    need: int = 1

    @property
    def free(self) -> int:
        return max(0, self.limit - self.busy)

    @property
    def fits(self) -> bool:
        return self.need <= self.free


@dataclass(frozen=True)
class Settings:
    """The ``[lanes]`` knobs of this module as one value (the one reader of them)."""

    default_slots: int = 1
    wait_s: int = 600

    @classmethod
    def from_config(cls, cfg: Config) -> Settings:
        return cls(cfg.lanes.endpoint_slots, cfg.lanes.slot_wait_s)


def endpoint_pool(route: str) -> str:
    """The pool name of an endpoint route (``ep-<route>``), safe as a lock-file stem and
    one-to-one: every character outside ``[A-Za-z0-9-]`` becomes ``_<hex>``, so ``a/b`` and
    ``a_b`` stay two pools."""
    stem = _STEM.sub(lambda m: f"_{ord(m.group()):x}_", route)
    if (
        len(stem) > _STEM_MAX
    ):  # a file-name component is 255 bytes at most: keep the head, add a digest
        stem = f"{stem[: _STEM_MAX - 17]}_{hashlib.sha256(route.encode()).hexdigest()[:16]}"
    return "ep-" + stem


def pools_for(
    ep: Endpoint,
    default_slots: int,
    caps: Mapping[str, int],
    resources: Sequence[str] = (),
) -> tuple[Pool, ...]:
    """The pools a lane on ``ep`` takes a slot of: the endpoint's own, then each resource in
    ``resources`` (task syntax, ``gpu:4``). Name order, which is also the acquisition order."""
    own = endpoint_pool(ep.route)
    limit = ep.concurrency or caps.get(own) or default_slots
    pools = {own: Pool(own, max(1, int(limit)))}
    for name, need in parse_resources(list(resources)).items():
        pools[name] = Pool(name, max(1, int(caps.get(name, 1))), need)
    return tuple(pools[k] for k in sorted(pools))


def view(pools: Sequence[Pool], busy: Mapping[str, int]) -> tuple[Headroom, ...]:
    """Each pool with how many of its slots are busy (an unprobed pool counts as idle)."""
    return tuple(
        Headroom(p.name, p.limit, min(p.limit, busy.get(p.name, 0)), p.need) for p in pools
    )


def lane_figures(heads: Sequence[Headroom]) -> tuple[int, int]:
    """``(live, cap)`` for ``score.Lane``: the tightest pool decides (smallest cap, in lanes)."""
    if not heads:
        return 0, 1
    h = min(heads, key=lambda x: (x.limit // max(1, x.need), x.pool))
    need = max(1, h.need)
    return -(-h.busy // need), max(1, h.limit // need)


def refusals(heads: Sequence[Headroom]) -> list[str]:
    """Hard-constraint reasons for SW-score: a pool without room for one more lane."""
    out = []
    for h in heads:
        if h.need > h.limit:
            out.append(f"pool {h.pool} needs {h.need} slot(s) but has {h.limit}")
        elif not h.fits:
            out.append(f"pool {h.pool} full: {h.busy} of {h.limit} slots busy, {h.need} needed")
    return out


def headroom_term(heads: Sequence[Headroom]) -> float:
    """SW-headroom's figure in [0, 1]: the smallest free share among the pools (0 when full)."""
    if not heads:
        return 1.0
    return min(max(0.0, (h.free - h.need + 1) / h.limit) if h.limit else 0.0 for h in heads)


def slot_dir() -> Path:
    """Host-wide: a local model serves every project of the user (like `suitelock.state_dir`)."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "ddflow" / "lane-slots"


def busy_counts(pools: Sequence[Pool], directory: Path | str | None = None) -> dict[str, int]:
    """Busy slots per pool right now: a probe (`Slots.busy`), stale as it is returned."""
    d = Path(directory) if directory else slot_dir()
    return {p.name: SLOTS.Slots(d, p.name).busy(p.limit) for p in pools}


@dataclass(frozen=True)
class Held:
    """What `acquire` yields: the slots taken (pool -> indexes) and the total wait, for the
    swarm baseline's measured queue wait."""

    slots: dict[str, tuple[int, ...]]
    waited_s: float


def _try_all(
    stack: contextlib.ExitStack, pools: Sequence[Pool], d: Path
) -> tuple[dict[str, tuple[int, ...]], Pool | None]:
    """Every slot of every pool in one sweep, or nothing: on a miss the stack is unwound and
    the pool that was full is returned."""
    got: dict[str, tuple[int, ...]] = {}
    for p in pools:
        sl = SLOTS.Slots(d, p.name)
        try:
            got[p.name] = tuple(
                stack.enter_context(sl.try_hold(p.limit)).index for _ in range(p.need)
            )
        except SLOTS.SlotsTimeout:
            stack.close()
            return {}, p
    return got, None


@contextlib.contextmanager
def acquire(
    pools: Sequence[Pool],
    timeout_s: float | None,
    directory: Path | str | None = None,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = SLOTS.POLL_S,
) -> Iterator[Held]:
    """Hold every pool's ``need`` slots for the block, all or none; `SlotsTimeout` when they
    are not all free after ``timeout_s`` (0: one sweep, None: as long as it takes).

    All-or-none: a sweep takes everything with a no-wait try and, on a miss, lets go of what it
    took before it sleeps. A lane that held part of its set while waiting for the rest could
    deadlock with another (two ``gpu:2`` lanes each holding one of two slots), and would be
    holding while waiting (r-lock-cycle-selfheal). The price is no queue order among waiters.
    The caller launches the lane inside the block; the slots live as long as the block."""
    d = Path(directory) if directory else slot_dir()
    d.mkdir(parents=True, exist_ok=True)
    ordered = sorted(pools, key=lambda x: x.name)
    for p in ordered:
        if p.need > p.limit:
            raise SLOTS.SlotsTimeout(p.name, p.limit, 0.0)
    start = clock()
    deadline = None if timeout_s is None else start + max(0.0, timeout_s)
    with contextlib.ExitStack() as stack:
        while True:
            got, full = _try_all(stack, ordered, d)
            if full is None:
                break
            now = clock()
            if deadline is not None and now >= deadline:
                raise SLOTS.SlotsTimeout(full.name, full.limit, round(now - start, 3))
            # never sleep past the deadline: the last sweep happens at it, not after it
            sleep(poll_s if deadline is None else min(poll_s, deadline - now))
        yield Held(got, round(clock() - start, 3))
