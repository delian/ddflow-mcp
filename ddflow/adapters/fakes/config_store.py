"""The shipped fake of the `ConfigStore` port: one in-memory layer, no disk, no env dance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config_store import ConfigStore
from . import fake


@dataclass
class _Resolved:
    """What the fake's `resolve` returns: the merged knobs, their sources, the explain rows."""

    data: dict[str, Any]
    sources: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return self.data

    def explain(self) -> list[tuple[str, Any, str, str]]:
        return [
            (key, self._get(key), self.sources.get(key, "default"), "")
            for key in self.sources
        ]

    def _get(self, key: str) -> Any:
        node = self.data
        for part in key.split("."):
            node = node[part]
        return node


def _nest(pairs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs.items():
        node = out
        *head, leaf = key.split(".")
        for part in head:
            node = node.setdefault(part, {})
        node[leaf] = value
    return out


def _coerce(raw: str) -> Any:
    raw = raw.strip()
    if raw == "true":
        return True
    if raw == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        return raw  # the text as typed, the empty string included


@dataclass
class _Result:
    """The fake's stand-in for `configwrite.EditResult`: nothing is refused here."""

    written: tuple[str, ...] = ()
    error: str | None = None


@fake
class FakeConfigStore(ConfigStore):
    """A `ConfigStore` over one in-memory layer: ``files`` maps a dotted knob to the string a
    person typed, which `resolve` returns as the ``"file"`` source. Nothing else is layered;
    a value that is not a number is returned as the string it is, and `validate` raises for a
    key this fake does not know (the real one raises `InvalidValue`)."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = dict(files or {})

    def resolve(self, root=None, *, env: dict[str, str] | None = None) -> _Resolved:
        data = _nest({k: _coerce(v) for k, v in self.files.items()})
        return _Resolved(data, dict.fromkeys(self.files, "file"))

    def validate(self, data: dict[str, Any], *, written: Any = None) -> None:
        # the real adapter raises InvalidValue for the first wrong knob; this fake mimics the
        # one the conformance needs: an unknown section, or a value that is not a number.
        known = _nest({k: _coerce(v) for k, v in self.files.items()})
        for section, table in data.items():
            if section not in known:
                raise ValueError(f"unknown section {section!r}")
            for knob, value in table.items():
                if isinstance(value, str) and not value.lstrip("-").isdigit():
                    raise ValueError(f"bad value {section}.{knob} = {value!r}")

    def explain(self, resolved: _Resolved) -> list[tuple[str, Any, str, str]]:
        return resolved.explain()

    def set(self, root, key, value, *, layer="file", person=False, agent=""):
        self.files[key] = "true" if value is True else "false" if value is False else str(value)
        return _Result(written=(key,))
