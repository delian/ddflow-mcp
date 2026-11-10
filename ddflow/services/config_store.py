"""The default `ConfigStore`: ddflow's own layered config read and written behind one port.

The `ConfigStore` port is declared in `ddflow.adapters.config_store`; this module supplies
its single default adapter (r-core-pure): the bottom `adapters` layer cannot reach up into
`ddflow.config` and `ddflow.services.configwrite`, so the adapter that does lives here, in
`services`. A new config SOURCE (an org pack, a remote store) is a second adapter behind the
same port, never the in-process split it hides.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ddflow.adapters.config_store import ConfigStore
from ddflow.config import Config
from ddflow.services import configwrite as cw

__all__ = ["ConfigStore", "LocalConfigStore", "default_store"]


class LocalConfigStore:
    """The default `ConfigStore`: resolves the in-process `Config`, writes `configwrite`."""

    def resolve(self, root: Path | str | None = None, *, env: dict[str, str] | None = None):
        return Config.load(Path(root) if root is not None else None, env=env)

    def validate(self, data: dict[str, Any], *, written: Any = None) -> None:
        Config.check(data, written=written)

    def explain(self, resolved: Any) -> list[tuple[str, Any, str, str]]:
        return resolved.explain()

    def set(
        self,
        root: Path | str,
        key: str,
        value: object,
        *,
        layer: str = "file",
        person: bool = False,
        agent: str = "",
    ):
        return cw.apply_edit(
            Path(root),
            cw.SetPairs([(key, value)]),
            layer=layer,
            person=person,
            agent=agent,
        )


def default_store() -> LocalConfigStore:
    """The one default adapter."""
    return LocalConfigStore()
