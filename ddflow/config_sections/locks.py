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
