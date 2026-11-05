"""Stamping `wave:N` by default (`[waves].auto_stamp`): the guard around ONE command.

It wraps EVERY command, reads included, so the work it adds to a read is a stat of the
caller's own appends; the queue is folded only when the command appended events.
"""

from __future__ import annotations

import contextlib
import contextvars
from pathlib import Path

from ..config import Config
from ..infra import paths as PATHS
from ..infra.log import APPEND_HOOKS
from ..services import identity as ID
from . import waves

#: Re-entrancy guard: the restamp's own tag events must not trigger another restamp. A
#: ContextVar, not a module flag, so two commands in flight in one process (an MCP server)
#: do not see each other's restamp and skip their own (Bf03dc5a256 review finding).
_STAMPING = contextvars.ContextVar("ddflow_waves_auto_stamping", default=False)


#: Which command's scope the current context is in: the append hook counts a write only
#: when it runs in the scope that installed it, so a second command in another thread of
#: one process (an MCP server) under the same agent name is not counted as this one's.
_SCOPE = contextvars.ContextVar("ddflow_waves_auto_scope", default=None)


@contextlib.contextmanager
def _own_appends(root, who):
    """Counts the events THIS process appends for the caller while the block runs.

    Counted by the append hook, not by shard size: a shard is keyed by agent name, so a
    second PROCESS using the same name grows it too, and that is not this command's write
    (Bc5d4aafb98, rule r-read-verbs-read-only)."""
    seen = [0]
    token = object()

    def hook(wlog, ev, _start, _end):
        if _SCOPE.get() is token and wlog.root == root and ev.agent == who:
            seen[0] += 1

    APPEND_HOOKS.append(hook)
    reset = _SCOPE.set(token)
    try:
        yield seen
    finally:
        _SCOPE.reset(reset)
        with contextlib.suppress(ValueError):
            APPEND_HOOKS.remove(hook)


@contextlib.contextmanager
def keep_stamped(repo: Path, cfg, log, agent: str = ""):
    """Around a command: when it appended events and the knob is on, restamp the items
    whose wave changed (only those: an unchanged graph writes no event). "Appended" means
    appended by this process for the caller (the append hook), so a read verb never
    restamps because someone else wrote. Never raises."""
    try:
        on = (
            not _STAMPING.get()
            and bool(cfg.waves.auto_stamp)
            # Running the worktree's ddflow (code under test) against ITS OWN primary
            # would write the feature's own events into the live shared log (Bf4eaec64a6).
            and not PATHS.running_from_linked_worktree_against(repo)
        )
        # the identity derives lazily and may fail: resolved HERE, inside the guard
        counter = _own_appends(log.root, log.agent_id) if on else None
    except Exception:  # stamping must never break the command it follows
        on, counter = False, None
    if not on:
        yield
        return
    with counter as appended:
        yield
    try:
        if appended[0]:
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
