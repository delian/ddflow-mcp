"""The shipped fake of the `Toml` (config overlay) port: over an in-memory table, no files."""

from __future__ import annotations

from typing import Any

from ..tomlcfg import Toml
from . import fake


@fake
class FakeToml(Toml):
    """`overlay_table`/`move_keys` answer the given tables; `upsert`/`remove` are a no-op that
    records the edit (the config overlay's text editing is the real `infra.tomlcfg`)."""

    def __init__(self, overlay: dict[str, Any] | None = None) -> None:
        self.overlay = dict(overlay or {})
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def overlay_table(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("overlay_table", args))
        return dict(self.overlay)

    def upsert(self, text: str, dotted: str, literal_text: str) -> str:
        self.calls.append(("upsert", (dotted, literal_text)))
        return text

    def remove(self, text: str, dotted: str) -> Any:
        self.calls.append(("remove", (dotted,)))
        return text

    def move_keys(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("move_keys", args))
        return {}
