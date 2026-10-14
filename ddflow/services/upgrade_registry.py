"""The running code's registry, read fresh when this process is out of date (B72b73360bc).

A long-lived MCP server keeps the knob registry it imported at start. After an edit or an
upgrade the manifest on disk (always read fresh) announces knobs the process has never heard
of, and the release lint called them removed and refused a release for changes that do not
exist. A process whose package source is newer than its own start reads the knob defaults and
event kinds from a new interpreter instead; a process that is current pays nothing."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .. import config as C

#: When this process loaded the registry. A source file written after it means the process
#: runs older code than the tree.
_STARTED = time.time()

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
    """Is any source file of the running package newer than this process's import?"""
    root = Path(C.__file__).resolve().parent
    try:
        return any(p.stat().st_mtime > _STARTED for p in root.rglob("*.py"))
    except OSError:
        return False


def fresh() -> tuple[dict[str, Any], set[str]]:
    """The knob defaults and event kinds of the tree on disk, read by a new interpreter."""
    root = Path(C.__file__).resolve().parent.parent
    try:
        done = subprocess.run(
            [sys.executable, "-c", _FRESH],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
        data = json.loads(done.stdout) if done.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise FreshError(f"could not read the running code from a fresh process: {exc}") from exc
    if not isinstance(data, dict):
        raise FreshError(
            f"could not read the running code from a fresh process: {done.stderr[-300:]}"
        )
    return data["knobs"], set(data["kinds"])
