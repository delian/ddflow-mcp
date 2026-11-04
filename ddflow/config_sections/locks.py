"""The `[locks]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob
from .enforce import BLOCK_WARN_OFF


@declare("locks")
@dataclass
class LocksConfig:
    """What a task declares it will write versus what it wrote (LK-observed-globs)."""

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
