"""The session start for an agent that has no hook to deliver it: the first tool call of an
MCP connection carries a compact preamble once.

Not every agent shows MCP initialize instructions or runs a session hook. The first call of
a new connection, whatever it is, gets the start of a session appended as a second content
block (``content[0]`` stays the answer): open session or recovery, the top of the brief,
missing companions. It is skipped when a native hook already delivers the same text for the
agent's harness, when the call is the brief itself, and when ``[session].first_call`` is off.
The session it opens is recorded with the harness name, so the start is attributed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..config import Config
from ..infra.log import EventLog
from ..services import harnessreg as HR
from ..services import hookwriters as HW
from ..services import sessions as S
from .setup import _session_start

#: Tools that already carry the start of a session in their own answer.
CARRIES_START = frozenset({"ddflow_brief"})


def harness_of(client_info: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> str:
    """The descriptor id of the connecting agent: its session variable, else its
    ``clientInfo.name`` containing a descriptor id or family. "" when it cannot be told."""
    if found := HR.detect(os.environ if environ is None else environ):
        return found
    name = str(client_info.get("name", "")).lower()
    if not name:
        return ""
    # An id in the name, else a family; only a UNIQUE match counts (a family is shared by
    # several descriptors, and a wrong guess would switch off the start for the right agent).
    for hits in (
        {h.id for h in HR.load() if h.id and h.id in name},
        {h.id for h in HR.load() if h.family and h.family in name},
    ):
        if len(hits) == 1:
            return hits.pop()
    return ""


def hook_delivers(repo: Path, harness: str) -> bool:
    """Is a native hook that injects the session start installed for ``harness``?"""
    h = HR.get(harness)
    if h is None or h.hooks is None or "session_start" not in h.hooks.inject:
        return False
    event = h.hooks.events.get("session_start")
    return any(on is True and spec.event == event for spec, on, _why in HW.state(repo, h))


def clip(text: str, max_tokens: int) -> str:
    limit = max(1, max_tokens) * 4  # about four characters a token: a ceiling, not a measure
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...(clipped; call ddflow_brief for the rest)"


def preamble(repo: Path, agent: str, tool: str, client_info: Mapping[str, Any]) -> str:
    """The text to append to the first call's reply, or "" when none is due. Never raises:
    a failing preamble must not fail the call it rides on."""
    try:
        cfg = Config.load(repo)
        if not cfg.session.first_call or tool in CARRIES_START:
            return ""
        harness = harness_of(client_info)
        if not harness and not client_info:
            return ""  # a connection that never said who it is (no initialize) gets no start
        if harness and hook_delivers(repo, harness):
            return ""
        S.resolve(
            EventLog(repo, agent, log_cfg=cfg.log), tool=harness
        )  # records the start with the harness name
        body = _session_start(repo, agent).data.get("message", "")
        return clip(body, cfg.session.first_call_max_tokens) if body else ""
    except Exception:
        return ""
