"""The `ConfigStore` port and its default adapter: ddflow's own layered config read and
written through ONE adapter.

Today `config/_core.py` (the `Config` dataclass), `config_sections/_layers.py` and
`services/configwrite` each handle part of it. One port hides that split: `resolve` gives the
layered config (packaged defaults, the committed file, the machine-local file, env) with the
SOURCE of every value and the explain log; `set`/`unset` write one key in place
(comment-preserving, through the TOML adapter); `validate` checks a table before it is
written. A new config SOURCE (an org pack, a remote store) is a second adapter behind the
same port (D-adapters-answers-2: new source is an adapter, a new FORMAT is not — ddflow's
config stays TOML).

The default stays small on purpose: `resolve`/`validate`/`explain` are the in-process
`Config`; `set`/`unset` go through `services.configwrite`, the one guarded write path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .ports import port


@port
@runtime_checkable
class ConfigStore(Protocol):
    """Read and write ddflow's own layered config as one unit.

    Home: `ddflow.adapters.config_store` (compat: the call sites still import
    `ddflow.config.Config` / `services.configwrite`). Single default adapter; a new source is
    a new adapter, never a new code path in the loader or writer.
    """

    def resolve(
        self, root: Path | str | None = None, *, env: dict[str, str] | None = None
    ) -> Any:
        """The layered config at ``root``: a value holding the merged knobs (`as_dict`), the
        source of every knob (`sources`) and the explain log (`explain`)."""

    def validate(self, data: dict[str, Any], *, written: Any = None) -> None:
        """``data`` would load; raises name the first section/knob/value wrong."""

    def explain(self, resolved: Any) -> list[tuple[str, Any, str, str]]:
        """``(key, value, source, doc)`` for every knob, the `config --explain` contract."""

    def set(
        self,
        root: Path | str,
        key: str,
        value: object,
        *,
        layer: str = "file",
        person: bool = False,
        agent: str = "",
    ) -> Any:
        """Write ``key = value`` to ``layer``'s file in place (comments and layout stay); the
        `EditResult` (nothing written when anything is refused). A key REMOVAL (`unset`) is
        not in this port yet: `services.configwrite` has a set and an append edit but no
        remove edit, so deletion waits on one (ADP-config-store)."""


class LocalConfigStore:
    """The default `ConfigStore`: resolves the in-process `Config`, writes `configwrite`."""

    def resolve(self, root: Path | str | None = None, *, env: dict[str, str] | None = None):
        from ddflow.config import Config

        return Config.load(Path(root) if root is not None else None, env=env)

    def validate(self, data: dict[str, Any], *, written: Any = None) -> None:
        from ddflow.config import Config

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
        from ddflow.services import configwrite as cw

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
