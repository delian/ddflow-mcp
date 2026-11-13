"""The `Toml` port: the one TOML overlay loader and comment-preserving write-back.

Home of the port, and (as the family moves in, ADP-migrate-homes) of its default adapter.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .ports import port


@port
@runtime_checkable
class Toml(Protocol):
    """The one TOML overlay loader and the one write-back that keeps comments and layout.

    Home: `ddflow.adapters.tomlcfg` (compat re-export `ddflow.infra.tomlcfg`). Single
    adapter; the raw `tomllib`/`tomlkit` I/O it builds on is `ddflow.adapters.toml.TomlIo`
    (the one place those modules are imported).
    """

    def overlay_table(self, *args: Any, **kwargs: Any) -> Any:
        """A TOML table overlaid from config path, `.ddflow/`, then shipped defaults."""

    def upsert(self, text: str, dotted: str, literal_text: str) -> str:
        """Set `dotted` to `literal_text` in `text`, preserving comments and layout."""

    def remove(self, text: str, dotted: str) -> Any:
        """Drop `dotted` from `text`; ``(new_text, changed)``."""

    def move_keys(self, *args: Any, **kwargs: Any) -> Any:
        """Move keys within `text`; ``(new_text, changed)``."""
