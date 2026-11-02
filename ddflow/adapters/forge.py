"""The `Forge` port: a forge reached through its CLI (multi-adapter family).

Home of the port, and (as the family moves in, ADP-migrate-homes) of its default adapter.
"""

from __future__ import annotations

from typing import Any, Protocol

from .ports import port


@port
class Forge(Protocol):
    """A forge reached through its CLI, holding no token and speaking no HTTP.

    The worked example of a multi-adapter family: `GitHub` and `GitLab` are its two
    adapters, each with a `name`, registered in `ddflow.adapters.forge`'s
    ``Registry[Forge]`` (keyed by `name`) — the pattern `ci`, `notify` and `supervisors`
    follow. `name` is data, so this port is not `runtime_checkable`: a runtime `isinstance`
    check refuses a data member; the registry reads it.

    Home: `ddflow.adapters.forge` (compat re-export `ddflow.infra.forge`).
    """

    name: str

    def find(self, head: str) -> Any:
        """The request for `head`, or None; a `PRInfo`."""

    def create(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool,
        labels: list[str],
        reviewers: list[str],
    ) -> Any:
        """Open the request; a `PRInfo`."""

    def view(self, number: int) -> Any:
        """The request's current state; a `PRInfo`."""

    def merge(self, number: int, *, strategy: str, head_sha: str, auto: bool) -> str:
        """Merge (or enqueue) the request; the forge's answer."""

    def set_base(self, number: int, base: str) -> None:
        """Change the request's base branch."""

    def threads(self, number: int) -> list[Any]:
        """The review threads; each a `Thread`."""

    def reply(self, number: int, thread_id: str, body: str) -> None:
        """Reply to a review thread."""

    def resolve(self, number: int, thread_id: str) -> None:
        """Mark a review thread resolved."""
