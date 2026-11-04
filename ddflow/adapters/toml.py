"""The `TomlIo` port and its default adapter: the ONE module that reads TOML.

Home of the `toml` family's raw I/O. This step carries the READ side (`tomllib`); the write
side (`dumps`, `document` over `tomlkit`) and the `toml-home` import contract with its
shrink-only baseline follow in ADP-toml-contract, because they edit shared guard files
(.importlinter, tests/architecture_guards_support.py) that ADP-fs holds.

Why two ports: `Toml` (`ddflow.adapters.tomlcfg`, implemented today in `ddflow.infra.tomlcfg`)
is the policy layer -- layered overlay of config paths, unknown-key rules, key upsert/remove
on text -- with a loose, overlay-shaped signature. `TomlIo` is the primitive under it: parse
and render, nothing else. They are one stack, not two paths: `infra.tomlcfg` and ~35 other
modules still import `tomllib` themselves only until they delegate to `TomlIo`; that move for
`infra.tomlcfg` is the follow-up task ADP-toml-unify, after which no second reader or writer
exists there.

Small on purpose: callers of ADP-managed-files and ADP-config-store depend on these calls.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .ports import port


class TomlError(tomllib.TOMLDecodeError):
    """What a parse failure raises, from the adapter and the fake alike. A
    `tomllib.TOMLDecodeError` (so a caller that catches that keeps working) built from a
    message alone."""

    def __init__(self, msg: str, source: BaseException | None = None) -> None:
        ValueError.__init__(self, msg)  # tomllib's own signature wants doc and pos
        # the position of the error `source` (a tomllib one) reported, or the start of the text
        self.msg = msg
        self.args = (msg, getattr(source, "doc", ""), getattr(source, "pos", 0))  # as tomllib's
        self.doc = getattr(source, "doc", "")
        self.pos = getattr(source, "pos", 0)
        # Python 3.14 gives the line and column; earlier ones only say "(at line 2, column 5)"
        at = re.search(r"at line (\d+), column (\d+)", str(source or ""))
        self.lineno = getattr(source, "lineno", int(at[1]) if at else 1)
        self.colno = getattr(source, "colno", int(at[2]) if at else 1)

    def __str__(self) -> str:
        return self.msg

    def __reduce__(self) -> tuple[Any, ...]:  # `args` is tomllib's (msg, doc, pos)
        return (_rebuild_error, (self.msg, self.doc, self.pos, self.lineno, self.colno))


def _rebuild_error(msg: str, doc: str, pos: Any, lineno: int, colno: int) -> TomlError:
    """Unpickle a `TomlError` with everything it carried (`args` is derived from these)."""
    err = TomlError(msg)
    err.doc, err.pos, err.lineno, err.colno = doc, pos, lineno, colno
    err.args = (msg, doc, pos)
    return err


@port
@runtime_checkable
class TomlIo(Protocol):
    """Read TOML into plain data.

    Home: `ddflow.adapters.toml`. Single adapter; `tomllib` is imported there alone, and the
    `toml-home` contract (ADP-toml-contract) lets the other importers only go down.
    """

    def loads(self, text: str) -> dict[str, Any]:
        """The table `text` holds; raises `TomlError` when it is not TOML."""

    def read(self, path: str | Path) -> dict[str, Any]:
        """The table the file at `path` holds; `OSError` when unreadable, `TomlError` when bad."""


class TomlLibIo:
    """The default `TomlIo`: `tomllib`."""

    def loads(self, text: str) -> dict[str, Any]:
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise TomlError(str(exc), exc) from exc  # keeps the position tomllib found

    def read(self, path: str | Path) -> dict[str, Any]:
        with open(path, "rb") as handle:
            raw = handle.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TomlError(f"{path}: not UTF-8: {exc}") from exc
        return self.loads(text)
