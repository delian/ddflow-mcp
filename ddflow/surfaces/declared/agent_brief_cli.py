"""How `brief --for-agent` reads with `--json`: the launch brief whole and by section."""

from __future__ import annotations

from typing import Any

from ...core import outcome as O
from ..tools._common import _api
from . import lifecycle_cli as L


def call(repo, a, agent, called_from=None, shell_cwd=None):
    """`brief`: the launch brief of ``--for-agent`` for the item, else the session pack.

    The item is `--item` or the positional id. Flags that belong to the other form are refused
    rather than ignored: a brief that quietly is not about the item asked for is worse than none.
    """
    item = a.get("target") or a.get("item") or ""
    if not a.get("for_agent"):
        if a.get("note"):
            return O.failed("brief", "--note belongs to --for-agent", text="")
        return _api().brief(
            repo,
            item=item,
            phase=a.get("phase", "") or "",
            check_recovery=bool(a.get("check_recovery", _api().DEFAULT_CHECK_RECOVERY)),
            called_from=called_from,
            shell_cwd=shell_cwd,
            agent=agent,
        )
    if not item:
        return O.failed(
            "brief", "--for-agent needs the item: `brief --for-agent NAME ITEM`", text=""
        )
    if a.get("phase"):
        return O.failed("brief", "--phase belongs to the session pack, not --for-agent", text="")
    return _api().agent_brief(
        repo, item=item, for_agent=a["for_agent"], note=a.get("note") or "", agent=agent
    )


def brief_body(out, a) -> Any:
    """The `--json` body of `brief`: the session pack's, or the launch brief's sections."""
    if out.exit == L.FAIL or "sections" not in out.data:
        return L.brief_body(out, a)
    return out.body(("brief", "item", "for_agent", "sections", "over", "approx_tokens"))
