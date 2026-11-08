"""The shipped fake of the `TomlIo` port: no `tomllib`, no disk."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..toml import TomlError
from . import fake

_HEADER = re.compile(r"\[([A-Za-z0-9_.-]+)\]")
_PAIR = re.compile(r"([A-Za-z0-9_-]+)\s*=\s*(.+)")


def _strip_comment(raw: str) -> str:
    """`raw` without a trailing `# comment` (a `#` inside a double-quoted string stays)."""
    quoted = escaped = False
    for i, ch in enumerate(raw):
        if quoted and ch == "\\" and not escaped:
            escaped = True
            continue
        if ch == '"' and not escaped:
            quoted = not quoted
        elif ch == "#" and not quoted:
            return raw[:i].strip()
        escaped = False
    return raw.strip()


def _refuse(name: str) -> Any:
    """JSON's `NaN` and `Infinity` are not TOML (which spells them `nan` and `inf`)."""
    raise ValueError(f"not a TOML value: {name}")


def _is_toml_value(value: Any) -> bool:
    """A scalar or an array of them: JSON's `null` and inline objects are not in this subset."""
    if isinstance(value, list):
        return all(_is_toml_value(v) for v in value)
    return isinstance(value, str | int | float | bool)


@fake
class FakeTomlIo:
    """A `TomlIo` over a small subset: `[a.b]` tables and `key = value` where the value is
    JSON-shaped (strings in double quotes, numbers, true/false, arrays). That is the part a
    caller's test needs; anything else raises `TomlError`, as a bad file does for the real one.
    `files` maps a path (as text) to the TOML text `read` returns."""

    def __init__(self, files: Mapping[str, str] | None = None) -> None:
        self.files = {str(k): v for k, v in (files or {}).items()}

    def loads(self, text: str) -> dict[str, Any]:
        root: dict[str, Any] = {}
        table, declared = root, set()
        for number, raw in enumerate(text.splitlines(), 1):
            line = _strip_comment(raw)
            if not line:
                continue
            if m := _HEADER.fullmatch(line):
                parts = m.group(1).split(".")
                if "" in parts:
                    raise TomlError(f"line {number}: empty key in table name {m.group(1)!r}")
                if m.group(1) in declared:
                    raise TomlError(f"line {number}: cannot declare [{m.group(1)}] twice")
                declared.add(m.group(1))
                table = root
                for part in parts:
                    table = table.setdefault(part, {})
                    if not isinstance(table, dict):
                        raise TomlError(f"line {number}: {part!r} is not a table")
            elif m := _PAIR.fullmatch(line):
                if m.group(1) in table:
                    raise TomlError(f"line {number}: duplicate key {m.group(1)!r}")
                if "\\/" in m.group(2):  # JSON has this escape, TOML does not
                    raise TomlError(f"line {number}: invalid escape in {m.group(2)!r}")
                try:
                    value = json.loads(m.group(2), parse_constant=_refuse)
                except ValueError as exc:
                    raise TomlError(f"line {number}: {exc}") from exc
                if not _is_toml_value(value):
                    raise TomlError(f"line {number}: not a TOML value: {m.group(2)!r}")
                table[m.group(1)] = value
            else:
                raise TomlError(f"line {number}: not TOML this fake knows: {line!r}")
        return root

    def read(self, path: str | Path) -> dict[str, Any]:
        try:
            return self.loads(self.files[str(path)])
        except KeyError:
            raise FileNotFoundError(str(path)) from None

    def dumps(self, data: Any) -> str:
        """``data`` as TOML in the fake's subset (bare keys, tables, arrays, scalars): what
        `loads` reads back. A key or value outside that subset (a dotted or spaced key,
        `null`, a table inside an array) raises `TomlError`, never an unparsable document."""
        return "\n".join(self._lines(dict(data), ""))

    def document(self, text: str) -> Any:
        """The fake's document is its plain table: the subset has no comments to keep."""
        return self.loads(text)

    def _lines(self, table: dict[str, Any], prefix: str) -> list[str]:
        # scalars before the nested tables: once a `[header]` opens, a later scalar would join
        # that table, not this one.
        for key, value in table.items():
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
                raise TomlError(f"key {key!r} is not a bare TOML key this fake knows")
            if isinstance(value, dict):
                continue  # after the scalars
            if not _is_toml_value(value):
                raise TomlError(f"not a TOML value this fake knows: {value!r}")
        out = [f"{k} = {json.dumps(v)}" for k, v in table.items() if not isinstance(v, dict)]
        for key, value in table.items():
            if isinstance(value, dict):
                out += ["", f"[{prefix}{key}]", *self._lines(value, f"{prefix}{key}.")]
        return out
