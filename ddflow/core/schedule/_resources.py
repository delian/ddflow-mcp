"""Physical resources, interrupted work, and the no-worktree tag."""

from __future__ import annotations

from ...config import Config
from ..flow import FEATURE, branch_kind
from ..model import RUNNING, Item, Lease, State
from ..records import SIGHTINGS_THAT_CONFIRM


def parse_resources(specs: list[str]) -> dict[str, int]:
    """`["gpu:4", "vllm-fleet"]` -> `{"gpu": 4, "vllm-fleet": 1}`. Repeats add up.

    A count that is not a positive integer is a ValueError: silently reading `gpu:four`
    as 1 would let a claim through that the operator meant to be large.
    """
    out: dict[str, int] = {}
    for spec in specs:
        name, _, count = spec.strip().partition(":")
        name = name.strip()
        if not name:
            continue
        n = int(count) if count.strip() else 1
        if n < 1:
            raise ValueError(f"resource {spec!r}: the count must be a positive integer")
        out[name] = out.get(name, 0) + n
    return out


def problem(specs: list[str]) -> str:
    """Why ``specs`` cannot be an item's resources, or ""."""
    # Only NEW input: a stored `gpu=1` (from before this check) must still parse, as a name.
    for spec in specs:
        if "=" in spec:
            name, _, cap = spec.partition("=")
            return (
                f"resource {spec!r}: an item names its resources as name:count "
                f"(`{name.strip()}:{cap.strip() or 1}`); name=count is the "
                "[schedule] resources capacity form"
            )
    try:
        parse_resources(specs)
    except ValueError as exc:
        return str(exc)
    return ""


def capacities(cfg: Config) -> dict[str, int]:
    """`[schedule] resources` as a dict. Undeclared resources are exclusive (capacity 1).

    Raises ValueError naming the knob for an entry that is not `name=integer`: written
    in the ITEM syntax (`gpu:8`) it became a resource literally named "gpu:8" and left
    `gpu` at capacity 1, and `mem=64GB` raised an int() error that the claim then
    reported as a resource conflict (roborev 828).
    """
    out: dict[str, int] = {}
    for spec in cfg.schedule.resources:
        name, sep, cap = spec.partition("=")
        name, cap = name.strip(), cap.strip()
        if not sep or not name or ":" in name or not cap.isdigit() or int(cap) < 1:
            raise ValueError(
                f"[schedule] resources entry {spec!r} is not `name=capacity` with a "
                f'positive integer capacity (e.g. "gpu=8")'
            )
        out[name] = int(cap)
    return out


def resource_shortfall(
    want: list[str], live: dict[str, Lease], caps: dict[str, int], exclude: str = ""
) -> str:
    """Why `want` does not fit beside the live leases, or "" when it does.

    Every live lease counts, the caller's own included: resources are PHYSICAL. Two
    items held by one agent still want two sets of GPUs, unlike two items writing one
    file, where one author cannot collide with itself.
    """
    need = parse_resources(want)
    if not need:
        return ""
    used: dict[str, list[tuple[str, int]]] = {}
    for other_id, lease in live.items():
        if other_id == exclude:
            continue
        for name, n in parse_resources(lease.resources).items():
            used.setdefault(name, []).append((other_id, n))
    for name, n in need.items():
        cap = caps.get(name, 1)
        taken = sum(k for _i, k in used.get(name, []))
        if n > cap:
            return f"needs {n} {name} but the capacity is {cap} ([schedule] resources)"
        if taken + n > cap:
            holders = ", ".join(f"{i} ({k})" for i, k in used[name])
            return f"needs {n} {name}; {taken} of {cap} in use by {holders}"
    return ""


def interrupted(state: State, it: Item, live: dict[str, Lease]) -> str:
    """Why this item looks mid-flight with nobody on it. ``""`` when it does not.

    RUNNING with no live lease means the agent died between starting and claiming, or
    released without finishing. `lease.scan` has always called that `stale_running` and
    told an operator to recover it; `plan()` handed the same item to the next agent as
    ordinary ready work, with no mention that someone had been there. Both behaviours
    are defensible. Deciding them in two modules that never consult each other is not —
    the second agent starts from scratch on work that may exist, uncommitted, in a
    worktree nobody mentioned.

    So the classification lives here, once, and both callers ask it. The scheduler does
    NOT refuse the item — someone has to resume it, and refusing would strand it — it
    annotates the offer. `lease.scan` measures the worktree, which is I/O and stays in
    the service layer; this says only what the log says.
    """
    if it.state != RUNNING or it.id in live:
        return ""
    where = f" in {it.worktree}" if it.worktree else ""
    return (
        f"{it.id} is RUNNING with no live lease{where}: an agent started it and did not "
        f"finish. Run `ddflow recover --item {it.id}` before starting from scratch — "
        f"its worktree may hold uncommitted work."
    )


def bug_items(state: State, cfg: Config) -> set[str]:
    """Ids of the items that fix a bug, which `[schedule] bugs_first` offers first.

    A bug fix is what `branch_kind` already calls one -- a `bugfix_tags` or `hotfix_tags`
    tag, the same test gitflow names its branches by -- or an item an OPEN bug record
    names, or the fix task `bug found` filed for it (`fix_task`). A closed record stops
    counting -- fixed, or invalid (a false finding was never a bug): its item is ordinary
    work again. An UNCONFIRMED one (its repro command ran and did not fail,
    DX-bug-repro-verify) counts for nothing either: such a bug is not offered ahead of
    confirmed ones -- until a second sighting or a run that failed makes it confirmed.
    """
    ids = {i.id for i in state.items.values() if branch_kind(i, cfg) != FEATURE}
    for b in state.bugs.values():
        if not b.open or (b.reproduced == "no" and b.sightings < SIGHTINGS_THAT_CONFIRM):
            # An UNCONFIRMED bug counts for NOTHING -- no promotion of the item or fix
            # task it names: its repro command RAN and did not fail ('no'). Only a
            # 'no' demotes: a '' (no repro command ran) or a missing record is no
            # evidence either way and is NOT demoted -- such a bug is still offered,
            # it has simply never been shown reproducible. It counts for nothing
            # NEGATIVE either: an item that is a bug branch on its own rank never gets
            # subtracted because an unconfirmed record happens to name it.
            continue
        ids.update(x for x in (b.item, b.fix_task) if x)
    return ids


NO_TREE_TAG = "no-worktree"


def needs_tree(it: Item) -> bool:
    """False for an item that declares it takes no worktree (`no-worktree` tag): a review,
    a research task. Such an item counts against `schedule.max_parallel_tasks` only."""
    return NO_TREE_TAG not in (t.strip().lower() for t in it.tags)
