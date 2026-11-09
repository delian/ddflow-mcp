"""The shipped fake of the `ManagedFile` port: an in-memory JSON file, no disk."""

from __future__ import annotations

from typing import Any

from ..managed_files import OWNER_KEY, ManagedFile, _dump, _owned_keys, _parse
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
        data[OWNER_KEY] = {"owner": version, "keys": sorted(set(_owned_keys(data)) | owned.keys())}
        self.files[path] = _dump(data)

    def remove(self, path: str, *, keys: frozenset[str]) -> None:
        data = _parse(self.files.get(path, ""))
        for key in keys:
            data.pop(key, None)
        left = _owned_keys(data) - keys
        if left:
            data[OWNER_KEY]["keys"] = sorted(left)
        else:
            data.pop(OWNER_KEY, None)
        self.files[path] = _dump(data)

    def owned(self, path: str) -> dict[str, str]:
        return dict.fromkeys(_owned_keys(_parse(self.files.get(path, ""))), "current")

    def text(self, path: str, key: str) -> Any:
        return _parse(self.files.get(path, "")).get(key)
