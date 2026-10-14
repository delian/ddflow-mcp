"""The tree's registry read by a fresh interpreter (B72b73360bc).

A long-lived MCP server keeps the knob registry it imported at start. After an edit or an
upgrade the manifest on disk (always read fresh) announces knobs the process has never heard
of, and the release lint called them removed and refused a release for changes that do not
exist. The lint therefore double-checks a REMOVAL, the one finding a stale process can invent,
with a new interpreter: a knob or event kind that interpreter still has was never removed."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .. import config as C
from ..infra import proc as P

#: Runs in a fresh interpreter: the tree's own knobs and event kinds, as JSON.
_FRESH = (
    "import json; from ddflow.services import upgrade_manifest as U; "
    "print(json.dumps({'knobs': U.knob_defaults(), 'kinds': sorted(U.event_kinds())}))"
)

#: Seconds a fresh interpreter may take to answer.
TIMEOUT_S = 120


class FreshError(Exception):
    """The fresh interpreter could not say what the tree's registry is."""


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
