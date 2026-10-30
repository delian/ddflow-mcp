"""Stamping `wave:N` by default (`[waves].auto_stamp`): the guard around ONE command.

It wraps EVERY command, reads included, so the work it adds to a read is a stat of the
shards (`EventLog.mark`); the queue is folded only when the command appended events.
"""

from __future__ import annotations

import contextlib
import contextvars
from pathlib import Path

from ..config import Config
from ..services import identity as ID
from . import waves

#: Re-entrancy guard: the restamp's own tag events must not trigger another restamp. A
#: ContextVar, not a module flag, so two commands in flight in one process (an MCP server)
#: do not see each other's restamp and skip their own (Bf03dc5a256 review finding).
_STAMPING = contextvars.ContextVar("ddflow_waves_auto_stamping", default=False)


@contextlib.contextmanager
def keep_stamped(repo: Path, cfg, log, agent: str = ""):
    """Around a command: when it appended events and the knob is on, restamp the items
    whose wave changed (only those: an unchanged graph writes no event). Never raises."""
    try:
        on = not _STAMPING.get() and bool(cfg.waves.auto_stamp)
        before = log.mark() if on else None
    except Exception:  # stamping must never break the command it follows
        on, before = False, None
    yield
    if not on:
        return
    try:
        if log.mark() != before:
            _token = _STAMPING.set(True)
            try:
                waves.restamp(repo, agent)
            finally:
                _STAMPING.reset(_token)
    except Exception:  # the command's own result stands
        return


def scope_for(repo: Path, agent: str = ""):
    """`keep_stamped` for a caller that holds no config or log yet (the MCP server)."""
    try:
        cfg = Config.load(repo)
        log, _who = ID.open_log(repo, cfg, agent)
    except Exception:  # a project that cannot load runs the call as before
        return contextlib.nullcontext()
    return keep_stamped(repo, cfg, log, agent)


#: Commands whose own contract is "changes nothing but what they report": a rehearsal of an
#: upgrade checks that the apply leaves items alone, so it must not stamp them (the first
#: command after it does).
NEVER = frozenset({"upgrade"})


def scope_of(ctx, command: str = ""):
    """`keep_stamped` for a CLI context (its repo, config and log), or nothing without one."""
    if command in NEVER:
        return contextlib.nullcontext()
    try:
        return keep_stamped(ctx.repo, ctx.cfg, ctx.log, ctx.requested_agent)
    except AttributeError:
        return contextlib.nullcontext()
