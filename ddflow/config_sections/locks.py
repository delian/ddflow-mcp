"""The `[locks]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob
from .enforce import BLOCK_WARN_OFF

PROBE_AT = ("claim", "gate", "merge")


def _probe_at(value):
    """Keep the known places, report the rest (a newer release's place is ignored)."""
    items = (
        [value]
        if isinstance(value, str)
        else list(value)
        if isinstance(value, (list, tuple))
        else []
    )
    if not isinstance(value, (str, list, tuple)):
        return [], [f"probe_at must be a list of places, not {type(value).__name__}"]
    kept = [x for x in items if x in PROBE_AT]
    return kept, [f"unknown probe_at place {x!r}" for x in items if x not in PROBE_AT]


@declare("locks")
@dataclass
class LocksConfig:
    """What a task declares it will write versus what it wrote (LK-observed-globs)."""

    mode: str = knob(
        "enforce",
        doc="The write-time lease check (the pre-tool hook) on a write outside the agent's live lease (D-lock-lifecycle-answers): 'enforce' refuses it first, naming the holder and how to wait, send a suggestion or open a task; 'log' notes it and allows; 'off' skips it (the commit-time check still runs). Settable by file, --local, env (DDFLOW_LOCKS_MODE) and MCP; an organisation locks it.",
        choices=("enforce", "log", "off"),
        strictest=(
            "enforce",
            "a write to a file another agent's lease covers is refused, not logged",
        ),
    )
    undeclared_write: str = knob(
        "warn",
        doc="What `gate status`, `brief` and `complete` do about a task whose diff has files in none of its lease globs (measured: 45% of the files tasks touch are undeclared, so parallel agents cannot see the overlap). 'warn' names them and the exact call that declares them (`ddflow update <id> --globs ...`, MCP `ddflow_task action=update`), 'block' makes `complete` refuse until they are declared, 'off' disables. When git cannot say what the task changed the check could not run: a warning, never a block and never a pass. Change it for the project (`ddflow config locks.undeclared_write ...`), for this machine (add --local), per run (DDFLOW_LOCKS_UNDECLARED_WRITE), or over MCP with `ddflow_configure`.",
        choices=BLOCK_WARN_OFF,
        strictest=("block", "complete refuses"),
    )
    suggest_globs: bool = knob(
        True,
        doc="Offer glob suggestions: declared globs the task's diff never touched are named for narrowing when it is ready to merge (measured: 50% of declared files are never touched), and directories that finished tasks of the same tags keep writing outside their declaration are named at `claim` and `gate status`. Advice only; nothing is changed. Off: neither is offered.",
    )
    suggest_history: int = knob(
        30,
        doc="How many of the most recent finished tasks sharing a tag with this one are read to learn which undeclared directories to suggest (each read is one git diff of its landed range). 0 reads none.",
        check=int_at_least(0),
    )
    suggest_min_items: int = knob(
        2,
        doc="A directory is suggested only when at least this many finished tasks of the same tags wrote into it outside their declared globs: one task's miss is an accident, a repeated one is a habit.",
        check=int_at_least(1),
    )
    suggest_shown: int = knob(
        5,
        doc="At most this many suggested or undeclared paths are listed in one notice; the rest are counted.",
        check=int_at_least(1),
    )

    snapshot: bool = knob(
        True,
        doc="Keep `.ddflow/local/lease-snapshot.json`, a small machine-local file rewritten on every lease acquire, renewal, release and expiry, which the per-edit write hook reads instead of folding the whole event log (that fold cost 3.7 s per edit on a 108 MB log). Off: nothing is written and the hook cannot check a lease, so it allows the call and says the commit-time check is the only one. Change it for the project (`ddflow config locks.snapshot ...`), for this machine (add --local), per run (DDFLOW_LOCKS_SNAPSHOT), or over MCP with `ddflow_configure`; an organisation locks it in its shared config.",
    )
    snapshot_max_age_s: int = knob(
        1800,
        doc="A lease snapshot written longer ago than this many seconds is STALE: the write hook does not trust it, ALLOWS the call and says so on stderr (the commit-time lease check is then the only check), never a silent pass. A live agent's heartbeat rewrites the snapshot at least every `[lease].renew_log_min_s` plus its heartbeat, so the default (the lease TTL) lapses only when nothing has touched the leases for that long. Lower = the hook falls back to the commit-time check sooner; higher = it trusts an older picture of who holds what. 0 = never stale. Change it for the project (`ddflow config locks.snapshot_max_age_s ...`), for this machine (add --local), per run (DDFLOW_LOCKS_SNAPSHOT_MAX_AGE_S), or over MCP with `ddflow_configure`.",
        check=int_at_least(0),
    )

    probe: bool = knob(
        True,
        doc="The conflict probe: `ddflow probe ITEM [--against main|ITEM]` asks git (`merge-tree --write-tree`, 8 to 21 ms typical) whether the item's branch merges textually clean with main or a peer's branch, and records the answer as a `probe.recorded` event with both tip shas. Off: the command and the callers that use it (release at merge-ready, suggestions, peer notices) answer `unavailable` and the commit-time check decides, never `clean`. Clean means textually clean only: a renamed function the other side still calls needs the gates and ci on the merge result.",
    )
    probe_timeout_s: int = knob(
        30,
        doc="Seconds one probe may take before it is recorded `unavailable` (never `clean`). Measured: p90 1.3 s on branches 11k commits stale; a scratch-merge fallback on an old git takes about 1 s.",
        check=int_at_least(1),
    )
    probe_at: list[str] = knob(
        factory=lambda: ["claim", "gate", "merge"],
        doc="Where ddflow runs the probe on its own: `claim` (when an item is claimed), `gate` (at a gate boundary) and `merge` (before the merge). An empty list probes only when `ddflow probe` is called. A probe that could not run is recorded `unavailable` and the commit-time check decides.",
        members=_probe_at,
    )
    probe_merge_tree_min_git: str = knob(
        "2.38",
        doc="The oldest git whose `merge-tree --write-tree` the probe uses; an older git (or one that answers 129 to it, as 2.43 does to --quiet) makes the probe fall back to a scratch merge in a throwaway worktree, slower and the same answer.",
    )
    optimistic: bool = knob(
        False,
        doc="Optimistic concurrency (DX-lease-hunk-optimistic): when a plain claim's globs overlap a live holder's on a non-generated file, run the `git merge-tree` conflict probe between the holder's branch and this claim's base. A clean merge conflates the two branches' disjoint hunks, so the claim is GRANTED and the overlapping files are recorded `optimistic` on the lease (the merge then rebases, re-running pin regeneration, instead of waiting); a real conflict or a probe that could not run keeps the exclusive-lease refusal (D-lease-overlap-only-by-break). Generated/pinned files ([lease].shared_globs, append-only and export targets) stay exclusive. Off (default) keeps the plain refusal. Change it for the project (`ddflow config locks.optimistic ...`), for this machine (add --local), per run (DDFLOW_LOCKS_OPTIMISTIC), or over MCP with `ddflow_configure`.",
    )
    review_reuse: str = knob(
        "off",
        doc="Whether a recorded review may be REUSED after the tree it covered moved (main merged in or a rebase) instead of running it whole (LK-review-reuse). 'off' (default, until measured) never reuses: a review whose recorded tree no longer matches is re-reviewed. 'disjoint' classifies each review gate with `git patch-id --stable` of the item's own change plus disjoint changed files -- when the item's own change is unchanged and the files main changed are disjoint from the files the reviewer saw, the review is kept and the reason recorded; when they overlap, only the overlapping files are re-reviewed as a delta; anything git cannot tell invalidates (fail closed, never a guess). Measured by the wrong-keep rate (a kept review followed by a finding on the same files).",
        choices=("off", "disjoint"),
        strictest=("off", "reuse is off: a tree that moved invalidates the review"),
    )
