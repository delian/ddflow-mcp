"""Test support for the hook-line launchers (services.launchers)."""

from __future__ import annotations

from ddflow.services import launchers as LA


def recorded_paths(command: str) -> list[str]:
    """The files a hook line needs, in the order it records them."""
    return list(dict.fromkeys(p for p, _x in LA._needs(command)))
