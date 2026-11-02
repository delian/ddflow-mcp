"""The `Git` port: every git call, as a value rather than an exception.

Home of the port, and (as the family moves in, ADP-migrate-homes) of its default adapter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .ports import port


@port
@runtime_checkable
class Git(Protocol):
    """Every git call, as a value rather than an exception where the exit code is the answer.

    Home: `ddflow.adapters.git` (compat re-export `ddflow.infra.git`). Single adapter.
    """

    def run(self, repo: Path | str, *args: str, **kwargs: Any) -> Any:
        """`git -C repo *args`; a `GitResult` (`ok`, `unavailable`, `timed_out`)."""

    def paths(self, repo: Path | str, *args: str, **kwargs: Any) -> list[str] | None:
        """NUL-separated names as the filesystem spells them; None when git could not tell."""

    def unmerged(self, repo: Path | str, timeout: float | None = None) -> list[str] | None:
        """The unmerged paths of an in-progress merge; None when git could not answer."""
