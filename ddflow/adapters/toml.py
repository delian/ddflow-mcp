"""The `TomlIo` port and its default adapter: the ONE module that reads and writes TOML.

Home of the `toml` family's raw I/O: `tomllib` (reading) and `tomlkit` (writing, with comments
and layout kept) are imported here and nowhere else; the `toml-home` import contract (task ADP-toml-contract) lets the
count of other importers only go down (tests/guard_baselines/importlinter-toml-home.toml).

Why two ports: `Toml` (`ddflow.adapters.tomlcfg`, implemented today in `ddflow.infra.tomlcfg`)
is the policy layer -- layered overlay of config paths, unknown-key rules, key upsert/remove
on text -- and has a loose, overlay-shaped signature. `TomlIo` is the primitive under it: parse
and render, nothing else. They are one stack, not two paths: `infra.tomlcfg` still imports
`tomllib` and `tomlkit` itself (it is on the `toml-home` baseline) only until it delegates to
`TomlIo`; that move is the follow-up task ADP-toml-unify, after which no second reader or
writer exists.

Small on purpose: callers of ADP-managed-files and ADP-config-store depend on these four calls.
`tomlkit` is imported on first write (38 ms; most commands never edit a config).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .ports import port

class TomlError(tomllib.TOMLDecodeError):
    """What a parse failure raises, from the adapter and the fake alike. A `tomllib.TOMLDecodeError`
    (so a caller that catches that keeps working) built from a message alone."""

    def __init__(self, msg: str) -> None:
        ValueError.__init__(self, msg)  # tomllib's own signature wants doc and pos; we have neither


@port
@runtime_checkable
class TomlIo(Protocol):
    """Read TOML into plain data; write data or an edited document back as text.

    Home: `ddflow.adapters.toml`. Single adapter; `tomllib` and `tomlkit` are imported there
    alone, and the `toml-home` contract (ADP-toml-contract) lets the other importers only go down in number.
    """

    def loads(self, text: str) -> dict[str, Any]:
        """The table `text` holds; raises `TomlError` when it is not TOML."""

    def read(self, path: str | Path) -> dict[str, Any]:
        """The table the file at `path` holds; `OSError` when unreadable, `TomlError` when bad."""

    def dumps(self, data: Any) -> str:
        """TOML text for a mapping or for a document from `document`."""

    def document(self, text: str) -> Any:
        """An editable document: `dumps` of it gives `text` back, comments and layout kept."""


class TomlLibIo:
    """The default `TomlIo`: `tomllib` for reading, `tomlkit` for writing."""

    def loads(self, text: str) -> dict[str, Any]:
        try:
            return tomllib.loads(text)
        except TomlError:
            raise
        except tomllib.TOMLDecodeError as exc:
            raise TomlError(str(exc)) from exc

    def read(self, path: str | Path) -> dict[str, Any]:
        with open(path, "rb") as handle:
            return self.loads(handle.read().decode("utf-8"))

    def dumps(self, data: Any) -> str:
        import tomlkit

        return tomlkit.dumps(data)

    def document(self, text: str) -> Any:
        import tomlkit

        try:
            return tomlkit.parse(text)
        except tomlkit.exceptions.ParseError as exc:  # one error family, as `loads` has
            raise TomlError(str(exc)) from exc


def default_io() -> TomlLibIo:
    """The one default adapter."""
    return TomlLibIo()
