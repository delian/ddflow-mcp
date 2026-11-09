"""The `ManagedFile` port and its JSON adapter: files shared with other tools, written by
ONE adapter family.

Settings and config that other tools also write (`settings.json`, `.mcp.json`,
`opencode.json`, `hooks.toml`, `AGENTS.md`/`README` regions) must be merged, never
clobbered: this port owns ONLY the keys ddflow was asked to write and leaves every other
byte of the file alone. The contract, per format:

* **merge** -- idempotently set the given ddflow-owned keys, recording the ownership of
  each under a reserved marker, and write atomically (with a `.ddflow-bak` beside the file
  before the first write). Re-applying the same keys changes nothing.
* **remove** -- drop the owned keys and the marker once it is empty; an unmanaged key is
  never touched.
* **owned** -- the ddflow-owned keys and their drifted/current state (a person's edit).

One format adapter per shape (`JSON` here; `jsonc`, `toml` (tomlkit), `yaml` and the
markdown-region grammar are the rest of the family, ADP-managed-files). JSON, JSONC and
YAML are handled ONLY here (D-adapters-answers-2). The content SHAPE stays in the
descriptor engines (`mcp_shape`, `emit`); this port only merges and marks.

Ownership marker (decision D-managedfiles-1): a reserved top-level key `_ddflow` whose
value is `{"owner": "<writer version>", "keys": [<owned key>, ...]}`. Every owned key is
listed there; a key not listed is the user's and is left byte-for-byte.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .digest import content_digest
from .ports import port

#: The reserved top-level key that records which keys ddflow owns (never a user's key).
OWNER_KEY = "_ddflow"


@port
@runtime_checkable
class ManagedFile(Protocol):
    """Merge one file of a shared format, owning only ddflow's keys.

    Home: `ddflow.adapters.managed_files`. The default merges JSON; the other formats are
    adapters of the same family.
    """

    def merge(self, path: str, *, owned: dict[str, Any], version: str) -> Any:
        """Idempotently set `owned` (ddflow-owned keys) in `path`; the write result."""

    def remove(self, path: str, *, keys: frozenset[str]) -> Any:
        """Drop the given owned keys (and the marker once empty) from `path`; never touches
        an unmanaged key."""

    def owned(self, path: str) -> dict[str, str]:
        """The ddflow-owned keys in `path`, each `"current"` or `"edited"` (drifted)."""


def _parse(text: str) -> dict[str, Any]:
    return json.loads(text) if text.strip() else {}


def _read(path: str) -> dict[str, Any]:
    p = Path(path)
    return _parse(p.read_text("utf-8")) if p.is_file() else {}


def _owned_keys(data: dict[str, Any]) -> set[str]:
    marker = data.get(OWNER_KEY)
    if isinstance(marker, dict) and isinstance(marker.get("keys"), (list, dict)):
        return {k for k in marker["keys"] if isinstance(k, str)}
    return set()


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _digest(value: Any) -> str:
    return content_digest(json.dumps(value, sort_keys=True))


def _backup(path: str) -> None:
    """A one-time `.ddflow-bak` beside the file, before the first write (D-adapters-answers-2)."""
    p = Path(path)
    bak = p.with_name(p.name + ".ddflow-bak")
    if p.is_file() and not bak.is_file():
        bak.write_text(p.read_text("utf-8"))


class JsonManagedFile:
    """The default `ManagedFile`: merges JSON (and JSONC by a tolerant reader, later), owning
    only the keys it was given, atomically. Each owned key carries a digest under the marker,
    so a person's edit is reported as `"edited"`. Plain JSON has no comments, so the write
    re-serialises; a `.ddflow-bak` sits beside the file before the first write."""

    def merge(self, path: str, *, owned: dict[str, Any], version: str) -> Any:
        from .fsio import atomic_write

        data = _read(path)
        for key, value_ in owned.items():
            data[key] = value_
        own = set(_owned_keys(data)) | owned.keys()
        data[OWNER_KEY] = {"owner": version, "keys": {k: _digest(data[k]) for k in own}}
        _backup(path)
        atomic_write(path, _dump(data))

    def remove(self, path: str, *, keys: frozenset[str]) -> Any:
        from .fsio import atomic_write

        data = _read(path)
        own = _owned_keys(data)
        for key in keys & own:  # only the owned keys: an unmanaged key is never touched
            data.pop(key, None)
        left = own - keys
        if left:
            data[OWNER_KEY]["keys"] = {k: _digest(data[k]) for k in left}
        else:
            data.pop(OWNER_KEY, None)
        atomic_write(path, _dump(data))

    def owned(self, path: str) -> dict[str, str]:
        data = _read(path)
        marker = data.get(OWNER_KEY)
        hashes = (marker or {}).get("keys", {}) if isinstance(marker, dict) else {}
        return {
            k: ("current" if hashes.get(k) == _digest(data.get(k)) else "edited")
            for k in _owned_keys(data)
        }
