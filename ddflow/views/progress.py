"""What the queue's totals count, said where a reader meets them (B4b1150d89a, B6341fe42ca).

Every number comes from `core.progress.totals.queue_totals`, the one function that owns the
task, phase and bug totals; this module only words them. `ddflow status` prints the
definition under its headline, with every bucket of every task record summing to the total,
and the completion `Progress:` block labels its live-tasks pair from the same function.
"""

from __future__ import annotations

from ..core.progress.totals import Totals


def status_tasks_note(t: Totals) -> str:
    """The lines `cmd_status` prints under its headline: the buckets, phases and bugs."""
    return "\n".join(t.lines())
