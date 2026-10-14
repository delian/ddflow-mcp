"""The running code's registry, read fresh when this process is out of date (B72b73360bc).

A long-lived MCP server keeps the knob registry it imported at start. After an edit or an
upgrade the manifest on disk (always read fresh) announces knobs the process has never heard
of, and the release lint called them removed and refused a release for changes that do not
exist. A process whose package source differs from what it loaded reads the knob defaults and
event kinds from a new interpreter instead; a process that is current pays nothing."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .. import config as C
from ..infra import proc as P


def _scan() -> dict[str, tuple[int, int]]:
    """Every source file of the running package: its size and exact modification time."""
    root = Path(C.__file__).resolve().parent
    return {
        str(p.relative_to(root)): (st.st_size, st.st_mtime_ns)
        for p in root.rglob("*.py")
        for st in (p.stat(),)
    }


#: The package as it was when this process loaded the registry (the MCP server imports it at
#: start through api.flow). Any difference later (an edit, an upgrade, a file added or removed)
#: means the process runs older code than the tree. Size AND exact mtime, not "newer than the
#: process": a wheel or `tar -x` keeps the old build time, so "newer" misses the upgrade.
try:
    _SNAPSHOT: dict[str, tuple[int, int]] | None = _scan()
except OSError:
    _SNAPSHOT = None

#: Runs in a fresh interpreter: the tree's own knobs and event kinds, as JSON.
_FRESH = (
    "import json; from ddflow.services import upgrade_manifest as U; "
    "print(json.dumps({'knobs': U.knob_defaults(), 'kinds': sorted(U.event_kinds())}))"
)

#: Seconds a fresh interpreter may take to answer.
TIMEOUT_S = 120


class FreshError(Exception):
    """The fresh interpreter could not say what the tree's registry is."""


def is_stale() -> bool:
    """Has the package on disk changed since this process loaded it? A tree that cannot be
    read, or no snapshot at import, is not known to be current: ask a fresh process."""
    if _SNAPSHOT is None:
        return True
    try:
        return _scan() != _SNAPSHOT
    except OSError:
        return True


def fresh() -> tuple[dict[str, Any], set[str]]:
    """The knob defaults and event kinds of the tree on disk, read by a new interpreter."""
    root = Path(C.__file__).resolve().parent.parent
    done = P.capture([sys.executable, "-c", _FRESH], cwd=root, timeout=TIMEOUT_S)
    if done.error is not None:
        raise FreshError(f"could not read the running code from a fresh process: {done.error}")
    try:
        data = json.loads(done.stdout) if done.returncode == 0 else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise FreshError(
            f"could not read the running code from a fresh process: {done.stderr[-300:]}"
        )
    return data["knobs"], set(data["kinds"])
