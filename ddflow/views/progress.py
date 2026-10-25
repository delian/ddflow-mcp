"""What the queue's two task totals count, said where a reader meets them (B4b1150d89a).

`ddflow status` headlines ``D/T tasks complete across N phase(s)`` counting every task
record, abandoned tasks and fix tasks included. The ``Progress:`` block a completion
prints counts live work items only (fix tasks and abandoned tasks out), which is why
the two pairs differ. This module renders the one note that names both definitions;
the API (``api.reporting.overview.status``) and the block itself
(``services.progress_line``) keep their own tallies and do not import it.
"""

from __future__ import annotations


def status_tasks_note() -> str:
    """The note ``cmd_status`` prints under its headline.

    It carries no numbers of its own: it says which records the headline's ``D/T``
    counts, and which ones the completion ``Progress:`` block's tasks pair leaves out.
    """
    return (
        "Task counts: this headline counts every task record (abandoned tasks and fix "
        "tasks included); the `Progress:` block after a completion counts live work "
        "items only, so the two totals can differ"
    )
