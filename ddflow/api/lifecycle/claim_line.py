"""A refused claim's place in line (`waits.queue`) and giving it up once the claim lands.

Part of `ddflow.api.lifecycle`; split from `claim` to keep that module under its size cap."""

from __future__ import annotations

from pathlib import Path

from ...services import waits as WT


def _join_line(repo: Path, cfg, agent: str, item: str, waiting_on: list[str], why: str) -> None:
    """A refused claim is a place in line (`waits.queue`). Never fails the refusal."""

    if cfg.lease.waiter_reservation_s <= 0:
        return
    try:
        WT.queue(
            repo,
            agent,
            item,
            waiting_on=waiting_on,
            reason=why,
            window_s=cfg.lease.waiter_reservation_s,
        )
    except (OSError, ValueError, TypeError):
        pass  # advisory: no queue place is better than no refusal


def _leave_line(repo: Path, agent: str, item: str) -> None:
    """The claim landed: the place in line it held is spent."""

    try:
        WT.clear(repo, agent, item)
    except (OSError, ValueError, TypeError):
        pass  # advisory
