"""How `brief --for-agent` reads with `--json`: the launch brief whole and by section."""

from __future__ import annotations

from typing import Any

from . import lifecycle_cli as L


def brief_body(out, a) -> Any:
    """The `--json` body of `brief`: the session pack's, or the launch brief's sections."""
    if out.exit == L.FAIL or "sections" not in out.data:
        return L.brief_body(out, a)
    return out.body(("brief", "item", "for_agent", "sections", "over", "approx_tokens"))
