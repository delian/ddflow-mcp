"""The shipped fake of the `ManagedFile` port: an in-memory JSON file, no disk."""

from __future__ import annotations

from typing import Any

from ..managed_files import OWNER_KEY, ManagedFile, _digest, _dump, _owned_keys, _parse
from . import fake


@fake
class FakeManagedFile(ManagedFile):
    """Merges one in-memory JSON document; the ownership marker and the "never touch an
    unmanaged key" contract are the same as `JsonManagedFile`, so it is a faithful double."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = dict(files or {})

    def merge(self, path: str, *, owned: dict[str, Any], version: str) -> None:
        data = _parse(self.files.get(path, ""))
        for key, value_ in owned.items():
            data[key] = value_
        own = (set(_owned_keys(data)) | owned.keys()) & set(data)
        data[OWNER_KEY] = {"owner": version, "keys": {k: _digest(data[k]) for k in own}}
        self.files[path] = _dump(data)

    def remove(self, path: str, *, keys: frozenset[str]) -> None:
        data = _parse(self.files.get(path, ""))
        own = _owned_keys(data)
        for key in keys & own:
            data.pop(key, None)
        left = (own - keys) & set(data)
        if left:
            data[OWNER_KEY]["keys"] = {k: _digest(data[k]) for k in left}
        else:
            data.pop(OWNER_KEY, None)
        self.files[path] = _dump(data)

    def owned(self, path: str) -> dict[str, str]:
        data = _parse(self.files.get(path, ""))
        marker = data.get(OWNER_KEY)
        keys = (marker or {}).get("keys", {}) if isinstance(marker, dict) else {}
        hashes = keys if isinstance(keys, dict) else {}
        if not hashes:  # a legacy name-only marker: drift is unknown, so every name is current
            return dict.fromkeys(_owned_keys(data), "current")
        return {
            k: ("current" if hashes.get(k) == _digest(data.get(k)) else "edited")
            for k in _owned_keys(data)
        }

    def text(self, path: str, key: str) -> Any:
        return _parse(self.files.get(path, "")).get(key)
