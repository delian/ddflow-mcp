"""Types shared by the lease submodules: the refusal error, one recovery finding,
and the ``Offer`` a refused claimant can be given instead of the item it asked for.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ...core.model import State


class LeaseError(RuntimeError):
    """Raised when a lease cannot be acquired. Carries the blocking holder."""

    exit_code = 3  # REFUSED (`core.outcome.exit_for`): someone else holds it

    def __init__(
        self, msg: str, holder: str = "", item: str = "", alternatives: list[str] | None = None
    ) -> None:
        super().__init__(msg)
        self.holder, self.item = holder, item
        self.alternatives = alternatives or []


@dataclass
class Recovery:
    """One recoverable situation found by ``scan``."""

    item: str
    holder: str
    kind: str  # "expired_lease" | "orphan_worktree" | "stale_running"
    worktree: str = ""
    branch: str = ""
    dirty_files: int = 0
    unmerged_commits: int = 0
    age_s: float = 0.0
    advice: str = ""
    #: True = work found · False = measured clean · None = COULD NOT MEASURE.
    #: Three-valued on purpose. Collapsing "unknown" into "clean" made an unmeasurable
    #: worktree (broken gitdir, git absent, NFS stall) report "safe to remove" -- and
    #: `sweep(apply=True)` acts on exactly this flag.
    salvageable: bool | None = False
    #: The tree is the HARNESS's (`Item.adopted`): the agent's own live working
    #: directory, which ddflow bound to the item and did not create. Never advised for
    #: removal -- `merge` refuses to delete one for the same reason (B930f5c6b7c).
    adopted: bool = False
    #: A stale `[worktree] base_ref` value (a CLI flag like ``--local``) read from the
    #: WORKTREE's OWN `.ddflow/config.toml`, which `claim`/`gate status` refuse to load.
    #: Empty means clean. Surfaced by `recover` and repaired by `recover --apply`
    #: (Bda0ba7b9eb); omitted from the wire when empty.
    stale_base_ref: str = field(default="", metadata={"omit_empty": True})


#: Who says what a refused claimant could take instead: `(state, item_id, holder, now)` ->
#: item ids. The scheduler's offer needs the repository (waiters' reservations, the load), which
#: this layer does not hold; the API that does passes it in.
Offer = Callable[[State, str, str, float], list[str]]
