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
        table = root
        for number, raw in enumerate(text.splitlines(), 1):
            line = raw.split("#", 1)[0].strip() if raw.lstrip()[:1] == "#" else raw.strip()
            if not line:
                continue
            if m := _HEADER.fullmatch(line):
                table = root
                for part in m.group(1).split("."):
                    table = table.setdefault(part, {})
                    if not isinstance(table, dict):
                        raise TomlError(f"line {number}: {part!r} is not a table")
            elif m := _PAIR.fullmatch(line):
                if m.group(1) in table:
                    raise TomlError(f"line {number}: duplicate key {m.group(1)!r}")
                try:
                    table[m.group(1)] = json.loads(m.group(2))
                except ValueError as exc:
                    raise TomlError(f"line {number}: {exc}") from exc
            else:
                raise TomlError(f"line {number}: not TOML this fake knows: {line!r}")
        return root

    def read(self, path: str | Path) -> dict[str, Any]:
        try:
            return self.loads(self.files[str(path)])
        except KeyError:
            raise FileNotFoundError(str(path)) from None
