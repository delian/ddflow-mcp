"""The compatibility vocabulary every surface shares (decision D-compat).

A renamed command, flag, MCP tool, tool argument or config key keeps working under its old
name until ddflow 1.0. Two facts are checked wherever a rename is DECLARED, so a declaration
that breaks the promise cannot be written: it names the release that renamed it (`since`),
and it is not removed before 1.0 (`removed_in`).

Standard library only: `config_sections` is the bottom layer, and the command registry
(`surfaces/registry.py`) imports the same helpers instead of keeping a second copy.
"""

from __future__ import annotations

import re

#: The earliest release an old name may be removed in (D-compat 1).
MIN_REMOVED_IN = "1.0"

_VERSION = re.compile(r"\d+(\.\d+)*")


def version_tuple(version: str) -> tuple[int, ...]:
    """``"0.1.17"`` -> ``(0, 1, 17)``. ValueError for anything that is not dotted integers."""
    if not _VERSION.fullmatch(version or ""):
        raise ValueError(f"{version!r} is not a version (dotted integers, e.g. 0.1.17)")
    return tuple(int(p) for p in version.split("."))


def check_rename(what: str, since: str, removed_in: str) -> None:
    """Raise ValueError unless a rename of ``what`` names its release and keeps the old name
    until 1.0 at least. ``0.1`` and ``0.1.0`` are the same version; ``1.0`` and ``1.0.0`` too."""
    if not since:
        raise ValueError(f"{what}: a rename must say which release made it (deprecated_since)")
    version_tuple(since)
    floor = version_tuple(MIN_REMOVED_IN)
    got = version_tuple(removed_in)
    size = max(len(floor), len(got))
    pad = lambda t: t + (0,) * (size - len(t))  # noqa: E731
    if pad(got) < pad(floor):
        raise ValueError(
            f"{what}: removed_in {removed_in!r} is before {MIN_REMOVED_IN}; an old name keeps "
            f"working until ddflow {MIN_REMOVED_IN} (D-compat)"
        )
