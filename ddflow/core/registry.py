"""One typed `Registry[T]` for the in-tree seams.

Export kinds, ticks, search sources and item kinds each had their own dict, `register`,
`unregister`, `get`, duplicate rule, "unknown" message and (for export kinds) module
discovery -- five spellings of one idea, which is how a sixth one gets written. This is the
one: a registry of named things of one type, with

* **names** -- an item's name is its ``key(item)`` (or given when registering); listing is
  sorted by name unless ``order="registered"`` keeps the order things were added;
* **duplicates** -- registering a taken name is a `DuplicateName` error, except that the
  same definition again is a no-op when ``same_ok`` (a module imported twice), or a
  replacement when ``replace`` (a search source reloaded);
* **unknown names** -- `get` raises `UnknownName` (a `KeyError` too, so the `Mapping`
  methods work) whose message lists the names known;
* **convention discovery** -- ``package="pkg", prefix="kind_"`` imports every ``pkg/kind_*.py``
  once so each registers itself: a new kind is a new file and no two edit the same line;
* **lazy targets** -- ``lazy(name, "pkg.mod:attr")`` costs no import until the name is used;
* **a loader** -- ``loader=fn`` fills the registry once from data (TOML descriptors);
* **a listing** -- `listing()` for doctor and help: name, where it came from, whether it has
  been resolved.

It is a read-only `Mapping[str, T]` for existing callers (``name in reg``, ``reg.get(n)``,
``', '.join(reg)``); ``table`` is the underlying dict for the tests that save and restore it.
The fold's ``HANDLERS`` stay closed and are not a registry: they are not a plug-in seam.

Pure of ddflow: only `importlib` and `pkgutil`.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class RegistryError(ValueError):
    """A registry refused a name."""


class DuplicateName(RegistryError):
    """A name that is already taken, by another definition."""


class UnknownName(RegistryError, KeyError):
    """A name nothing registered; the message lists those known."""

    def __str__(self) -> str:  # KeyError would quote it
        return str(self.args[0]) if self.args else ""


@dataclass(frozen=True)
class Lazy:
    """A ``"pkg.mod:attr"`` target, resolved when the name is first used."""

    target: str

    def resolve(self) -> Any:
        module, _, attr = self.target.partition(":")
        if not module or not attr:
            raise RegistryError(f"a lazy target is 'package.module:attr'; got {self.target!r}")
        return getattr(importlib.import_module(module), attr)


class Registry(Mapping[str, T], Generic[T]):
    """Named things of one type. ``what`` is how messages call one ("export kind")."""

    def __init__(
        self,
        what: str,
        *,
        key: Callable[[T], str] | None = None,
        order: str = "sorted",
        same_ok: bool = False,
        replace: bool = False,
        package: str = "",
        prefix: str = "",
        loader: Callable[[], Iterable[T]] | None = None,
    ) -> None:
        if order not in ("sorted", "registered"):
            raise ValueError(f"order is 'sorted' or 'registered'; got {order!r}")
        if same_ok and replace:
            raise ValueError("same_ok and replace are two answers to one question")
        self.what = what
        self._key = key
        self._order = order
        self._same_ok = same_ok
        self._replace = replace
        self._package = package
        self._prefix = prefix
        self._loader = loader
        self._ready = not (package or loader)
        self._filling = False
        #: name -> item (or a `Lazy`), in registration order. Tests that save and restore
        #: a registry hold this dict; the same object lives as long as the registry.
        self.table: dict[str, Any] = {}

    # -- adding and removing ------------------------------------------------------------
    def register(self, item: T, name: str = "") -> T:
        """Add ``item`` under ``name`` (default: ``key(item)``). A taken name is an error;
        see ``same_ok`` and ``replace``."""
        if not name:
            if self._key is None:
                raise RegistryError(f"{self.what}: give a name, or build the registry with key=")
            name = self._key(item)
        have = self.table.get(name)
        if have is not None and not self._replace:
            if self._same_ok and have == item:
                return item
            suffix = " with another definition" if self._same_ok else ""
            raise DuplicateName(f"{self.what} {name!r} is already registered{suffix}")
        self.table[name] = item
        return item

    def lazy(self, name: str, target: str) -> None:
        """Register ``name`` as ``"package.module:attr"``, imported when it is first used."""
        if name in self.table and not self._replace:
            raise DuplicateName(f"{self.what} {name!r} is already registered")
        self.table[name] = Lazy(target)

    def unregister(self, name: str) -> None:
        """Remove ``name`` (a test's throwaway); an unknown name is ignored. A discovered or
        loaded registry is filled first, so what is removed does not come back on the next read
        (and a failure of that fill is raised here, as it would be by any read)."""
        if not self._filling:
            self._fill()
        self.table.pop(name, None)

    # -- reading ------------------------------------------------------------------------
    def _fill(self) -> None:
        """Import the discovery package and run the loader, once; a failure raises again on
        the next call (nothing is half-marked as done)."""
        if self._ready or self._filling:
            return
        self._filling = True
        try:
            self._fill_now()
        finally:
            self._filling = False

    def _fill_now(self) -> None:
        if self._package:
            pkg = importlib.import_module(self._package)
            for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
                if m.name.startswith(self._prefix):
                    importlib.import_module(f"{self._package}.{m.name}")
        if self._loader is not None:
            for item in list(self._loader()):  # all of them first: a failure registers none
                self.register(item)
        self._ready = True

    def get_or_raise(self, name: str) -> T:
        self._fill()
        try:
            have = self.table[name]
        except KeyError:
            known = ", ".join(self.names()) or "(none)"
            raise UnknownName(f"unknown {self.what} {name!r}. Known: {known}") from None
        if isinstance(have, Lazy):
            have = self.table[name] = have.resolve()
        return have

    def __getitem__(self, name: str) -> T:
        return self.get_or_raise(name)

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())

    def __len__(self) -> int:
        self._fill()
        return len(self.table)

    def __contains__(self, name: object) -> bool:
        self._fill()
        return name in self.table

    def names(self) -> list[str]:
        self._fill()
        return sorted(self.table) if self._order == "sorted" else list(self.table)

    def discover(self) -> None:
        """Import the discovery package now (it is done on first use anyway)."""
        self._fill()

    def values_list(self) -> list[T]:
        """Every item, in listing order (lazy targets resolved)."""
        return [self.get_or_raise(n) for n in self.names()]

    def listing(self) -> list[dict[str, Any]]:
        """For doctor and help: each name, the module it came from, and whether a lazy
        target has been resolved yet."""
        self._fill()
        rows = []
        for name in self.names():
            have = self.table[name]
            if isinstance(have, Lazy):
                rows.append({"name": name, "module": have.target.partition(":")[0], "lazy": True})
            else:
                module = getattr(have, "__module__", "") or type(have).__module__
                rows.append({"name": name, "module": module, "lazy": False})
        return rows
