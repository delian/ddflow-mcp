"""The `TomlIo` port and its default adapter: the ONE module that reads and writes TOML.

Home of the `toml` family's raw I/O: the READ side (`tomllib`) and the WRITE side
(`dumps`, `document` over `tomlkit`), plus the `toml-home` import contract with its
shrink-only baseline (ADP-toml-contract).

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

# `tomlkit` (the write side) is imported lazily inside `dumps`/`document` (B19157c3453):
# the READ side (`tomllib`) must stay importable under `-S`, where site-packages is dropped
# and `tomlkit` is gone, or the bare-interpreter import `scripts/bump.sh` (and the MCP
# handshake) refuses to start.


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
        # tomllib calls them lineno/colno; tomlkit (the write side) calls them line/col.
        self.lineno = getattr(source, "lineno", getattr(source, "line", int(at[1]) if at else 1))
        self.colno = getattr(source, "colno", getattr(source, "col", int(at[2]) if at else 1))

    def __str__(self) -> str:
        return self.msg

    def __reduce__(self) -> tuple[Any, ...]:  # `args` is tomllib's (msg, doc, pos)
        # `type(self)` -> a subclass round-trips as itself. `args` is a BaseException slot (not
        # in `__dict__`), so it is carried to `_rebuild_error` as a plain value; `add_note` notes
        # DO live in `__dict__` (PEP 678), so `self.__dict__.items()` carries them (not `self`).
        return (_rebuild_error, (type(self), self.msg, tuple(self.__dict__.items()), self.args))


def _rebuild_error(
    cls: type[TomlError], msg: str, items: tuple[Any, ...], args: tuple[Any, ...]
) -> TomlError:
    err = cls(msg)
    err.__dict__.update(dict(items))
    err.args = args
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

    def dumps(self, data: Any) -> str:
        """``data`` (or a `document` returned value) as TOML text, comments and layout kept."""

    def document(self, text: str) -> Any:
        """``text`` as an editable document (comments and layout kept); `TomlError` when bad."""


class TomlLibIo:
    """The default `TomlIo`: `tomllib` to read, `tomlkit` to write."""

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

    def dumps(self, data: Any) -> str:
        import tomlkit  # deferred: the write side's dep is optional to import the module

        return tomlkit.dumps(data)

    def document(self, text: str) -> Any:
        import tomlkit  # deferred: see `dumps`

        try:
            return tomlkit.parse(text)
        except tomlkit.exceptions.ParseError as exc:  # one error family, as `loads` has
            raise TomlError(str(exc), exc) from exc  # keeps the position tomlkit found


#: The one default adapter callers look up instead of importing `TomlLibIo` at the call
#: site (r-swap-seam): a caller takes the port as an argument, or reads `DEFAULT` here, never
#: reaches the adapter directly; in a test pass `FakeTomlIo`.
DEFAULT: TomlIo = TomlLibIo()
