"""Renamed-knob migration (D-compat)."""

from __future__ import annotations

import sys
from typing import Any

#: Old keys already warned about in this process (`_migrate_renamed`).
_WARNED_RENAMED: set[str] = set()


def _migrate_renamed(data: dict[str, Any], source: str) -> dict[str, Any]:
    """``data`` with every renamed knob (`RENAMED`, D-compat) under its CURRENT key.

    The old key is still read, with one warning on stderr per process; when both are set the
    new key wins, as the more recent spelling. ``source="check"`` (a write being validated)
    stays quiet: the write path migrates the file itself (`services/configwrite`).

    ``RENAMED``, ``Config`` and ``_WARNED_RENAMED`` are read from `ddflow.config` at call
    time (not imported here), so a monkeypatch of ``ddflow.config._WARNED_RENAMED`` takes
    effect: the package re-exports this function's home, and a fresh set there is the reset
    a test performs.
    """
    _config = sys.modules[__package__]  # ddflow.config, read at call time (monkeypatch, D-compat)

    hits = [
        old
        for old in _config.RENAMED
        if isinstance(sec := data.get(old.partition(".")[0]), dict) and old.partition(".")[2] in sec
    ]
    if not hits:
        return data
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in data.items()}
    for old in hits:
        new, since, removed_in = _config.RENAMED[old]
        osec, _, oknob = old.partition(".")
        nsec, _, nknob = new.partition(".")
        value = out[osec].pop(oknob)
        if not out[osec] and osec not in _config.Config.__dataclass_fields__:
            del out[osec]  # the old section held nothing else: do not call it unknown
        target = out.setdefault(nsec, {})
        if isinstance(target, dict):
            target.setdefault(nknob, value)
        if source != "check" and old not in _config._WARNED_RENAMED:
            _config._WARNED_RENAMED.add(old)
            print(
                f"ddflow: warning: config key '{old}' is deprecated since {since}; use "
                f"'{new}' (the old key is read until {removed_in}; the next config write "
                "moves it).",
                file=sys.stderr,
            )
    return out
