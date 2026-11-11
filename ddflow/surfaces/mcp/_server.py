"""`Server`, one MCP connection: dispatch, identity, and the offload boundary.

The `initialize`/`handle`/`_dispatch` core and the connection state; the `_m_*` method
handlers live in `_handlers._Handlers`, mixed in here.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ddflow.core import agentname as AN

from ...api.harness import HR
from ...config import Config
from ...core import outcome as O
from ...infra import harness_identity
from ...infra.log import EventLog
from .. import mcp_lifecycle as _lifecycle
from .. import mcp_next as _NEXT
from .. import mcp_protocol as protocol
from .. import projectkinds as PK
from .. import registry as _REGISTRY
from ..mcp_protocol import SUPPORTED_PROTOCOLS
from ..mcp_protocol import err as _err
from ..mcp_protocol import modern_check as _modern_check
from ..mcp_protocol import modernize as _modernize
from ..mcp_protocol import ok as _ok
from ..tools import profiles as _PROFILES
from ..tools.tiers import resolve_output_schemas, resolve_tier
from ._core import (
    _STRUCTURED_SINCE,
    AS_AGENT,
    _default_agent,
    _identity,
    _meta_agent,
    _progress_token,
    _waves_auto,
)
from ._handlers import _Handlers
from ._instructions import _instructions_parts, _upgrade_note
from ._snapfast import served as _snapshot_served


class Server(_Handlers):
    """One connection. Which, deliberately, is not the same thing as one agent.

    Identity is how every attribution in the log works -- who holds a lease, who ran a
    gate, whether the reviewer was a different agent than the author. The default is
    derived from the working tree (`EventLog.default_agent_id`), and that is right for
    the ordinary case of one agent per worktree.

    It is WRONG, silently, for the case this tool exists to support: several agents or
    subagents working the same tree at once. Each spawns its own stdio server, every
    one of them resolves the same cwd to the same identity, and their events merge into
    one indistinguishable stream. Nothing errors. `brief` then reports another agent's
    item as "what you were doing", and reviewer-independence compares an agent with
    itself and is satisfied.

    There is no signal that can distinguish them -- so identity has to be DECLARED:
    `DDFLOW_AGENT` in the environment, or `ddflow_identify` on the connection, or an
    `as_agent` argument on the individual call. Explicit beats derived, innermost wins.
    """

    def __init__(self, repo: Path, agent: str = "", *, called_from: Path | None = None) -> None:
        self.repo = Path(repo)
        PK.declare(self.repo)  # a bad project kind is reported by the `record` tool
        #: WHERE THE CALLER IS, unresolved. `self.repo` is the primary checkout -- that
        #: is what makes every worktree share one event log -- and resolving to it threw
        #: away the fact `claim` needs: whether the caller was already inside a worktree.
        #: Worktree ADOPTION was therefore unreachable from MCP, which is the surface the
        #: harnesses it was written for (Claude Code, Cursor) actually drive.
        self.called_from = Path(called_from) if called_from else self.repo
        self.protocol = SUPPORTED_PROTOCOLS[0]
        #: Old names (aliases) this connection has already been told are deprecated: said
        #: once per session (D-compat).
        self._notices = _REGISTRY.Notices()
        #: The stale-server note (`mcp_upgrade.footer`): said once, looked for once a minute;
        #: `reexec_pending` asks `serve` to replace the process once it is quiet.
        self.reexec_pending = False
        #: `kill_pending` asks `serve` to end the process once it is quiet (a hard `kill`,
        #: or a `restart`/`kill` on a remote server, which resolves to a reboot).
        self.kill_pending = False
        self._stale_said = False
        self._stale_checked_at = float("-inf")
        #: Declared identity for this connection; empty means "use the process
        #: default", which is the backward-compatible single-agent behaviour.
        self.agent = agent
        if not agent:
            # A server restarted under the same harness keeps what the agent declared
            # there, or its shell (which still reads the record) and it would split.
            self.agent = harness_identity.own(self.repo, scope=HR.session_fingerprint(os.environ))
        #: Which tools `tools/list` advertises: `[mcp].tools`, read ONCE here. Start-time
        #: only -- `listChanged` is false and there is no call that widens it.
        #: `[mcp].next_hints`, read ONCE like the tier: 0 (off) or the most hints a result carries.
        self._next_limit = _NEXT.resolve(self.repo)
        configured = resolve_tier(self.repo)
        #: `auto` waits for the client's name (the client table, `initialize`); until then, lean.
        self._tier_auto = configured == _PROFILES.AUTO
        self.tier = _PROFILES.settle(configured)
        #: `[mcp].output_schemas`, read ONCE here like the tier: declare an `outputSchema`
        #: and return `structuredContent` (D-compat-json-views).
        self.output_schemas = resolve_output_schemas(self.repo)
        #: What the client called itself at `initialize`. A LABEL, never an identity:
        #: every subagent of one harness reports the same `clientInfo.name`, so using
        #: it as an id would reproduce the exact collapse above while looking specific.
        self.client_info: dict[str, Any] = {}
        #: The session-start preamble rides on the first call only (`_first_call_note`).
        self._first_call_done = False
        #: Set when a handshake was answered from the interface snapshot (`_snapfast`): the
        #: upgrade notice it left out rides the first tool result (`_deferred_notice`).
        self._notice_deferred = False
        #: The stable text of a handshake rendered live, for `serve` to write as the snapshot.
        self.snapshot_due: str | None = None
        self._first_call_tries = 0
        #: Re-instruction cadence, per CONNECTION. `initialize` delivers the rules once and
        #: nothing re-states them afterwards; after a context compaction the model may
        #: retain none of it, and MCP has no server->client context-injection primitive. A
        #: footer on tool results is the only channel that survives, so these two counters
        #: decide how often it is allowed to speak. Per-connection because that is the
        #: lifetime of the context it is compensating for.
        self._calls_since_footer = 0
        self._last_footer_at = 0.0
        #: Writes one JSON-RPC frame to the client; `serve` sets it. Without it (a bare
        #: `handle` call) a long tool simply sends no progress.
        self.notify: Callable[[dict[str, Any]], None] | None = None
        #: The progress sender of each call in flight, by its token (`_progress`). `handle` runs
        #: one request at a time on a Server (a worker has its own), so the dict needs no lock.
        self._says: dict[Any, Callable[[str], None]] = {}

    def _structured(self, modern: bool) -> bool:
        """Whether this request is served with `outputSchema` / `structuredContent`: the knob
        is on and the client's revision has them (every modern revision does)."""
        return self.output_schemas and (modern or self.protocol >= _STRUCTURED_SINCE)

    def _progress(self, params: dict[str, Any]) -> Callable[[str], None] | None:
        """A per-line `notifications/progress` sender for this call, or None.

        A client that sent `_meta.progressToken` resets its idle timer on each one; a
        review of 2055 s was aborted at 1800 s with none (bug B206)."""
        token = _progress_token(params)
        send = self.notify
        if token is None or send is None:
            return None
        if (known := self._says.get(token)) is not None:
            return known  # one counter per call: progress must only ever increase
        count = [0]
        lock = threading.Lock()  # a heartbeat thread and the tool's own lines share this counter

        def say(line: str) -> None:
            with lock:  # count and send together: progress must reach the client in order
                count[0] += 1
                try:
                    send(
                        {
                            "jsonrpc": "2.0",
                            "method": "notifications/progress",
                            "params": {
                                "progressToken": token,
                                "progress": count[0],
                                "message": line.strip()[:500],
                            },
                        }
                    )
                except Exception:  # a closed pipe must not fail the review
                    pass

        self._says[token] = say
        return say

    def _someone_else(self, per_call: str) -> bool:
        """Does a per-call name -- `as_agent`, or a stateless request's `_meta` agent --
        name an agent OTHER than this connection's own?

        The connection's own identity is the declared one, else the derived default --
        the same answer `ddflow_identify` reports. Naming it again per call is the same
        agent; naming anyone else is a subagent riding this connection.
        """
        if not per_call:
            return False
        own = self.agent or _default_agent(self.repo)[0]
        return per_call != own

    def _caller(
        self, params: dict[str, Any], args: dict[str, Any], modern: bool
    ) -> tuple[str, str, dict[str, Any], str]:
        """(the agent this call runs as, the name the call itself gave -- "" for none --,
        the arguments without `as_agent`, why the name cannot be used -- "" when it can).

        The connection's identity, unless the call names one: on a stateless request its
        `_meta` agent (D-mcp-identity-per-call), and on any request the `as_agent`
        argument, which wins over both. Either is a PER-CALL name everywhere it counts:
        naming anyone but the connection's own identity, a claim does not adopt the tree
        the server stands in (`_someone_else`) -- a stateless caller cannot be told
        apart from a subagent riding the connection (B7c7a0d9222)."""
        if AS_AGENT in args:  # the argument first: when it is given, `_meta` is not read
            args = dict(args)
            want = args.pop(AS_AGENT)
            if not isinstance(want, str):
                return self.agent, "", args, f"{AS_AGENT} must be a string"
            want = want.strip()
            if want and not AN.is_valid(want):
                return self.agent, "", args, AN.refusal(want)
            if want:
                return want, want, args, ""
        if not modern:
            return self.agent, "", args, ""
        per_call, bad = _meta_agent(params)
        return per_call or self.agent, per_call, args, bad

    def _invoke(self, spec, args, agent, per_call, params):
        with _identity().via_mcp(), self._stamped(agent):
            result = self._invoke_marked(spec, args, agent, per_call, params)
        if spec.get("lifecycle"):
            _lifecycle.arm(self, spec["lifecycle"])
        return result

    def _stamped(self, agent):
        """Stamping `wave:N` by default (`[waves].auto_stamp`) after a call that wrote."""
        return _waves_auto().scope_for(self.repo, agent)

    def _invoke_marked(self, spec, args, agent, per_call, params):
        """Run a tool's typed `api`; the connection facts it needs are passed in."""
        # `own_identity` tools act only as the CONNECTION's identity: the per-call
        # `as_agent` is a claim the stdio transport does not authenticate, so naming a
        # different agent to read or withdraw "that agent's own" asks is refused here
        # rather than answered under a spoofable name (B6335d3482d).
        if spec.get("own_identity") and self._someone_else(per_call):
            own = self.agent or _default_agent(self.repo)[0]
            return O.refused(
                "mcp.identity",
                f"the per-call name {per_call!r} does not name this connection ({own!r}); "
                "over MCP the name is not authenticated, so reading or withdrawing asks "
                "as another agent is refused (B6335d3482d).",
            )
        # `called_from` only where the tool asks for it. `claim` is the one
        # operation whose behaviour depends on WHERE the caller is standing
        # rather than which repo it is in: an agent whose harness already put
        # it in a worktree should have that tree ADOPTED, and resolving to the
        # primary loses the only fact that says so. `main()` computed it and
        # discarded it, which is why adoption was unreachable from MCP.
        if spec.get("wants_called_from"):
            where = self.called_from
            if self._someone_else(per_call):
                # Where the connection stands is where the CONNECTION's
                # identity works, for EVERY tool that asks. A subagent sharing
                # it (Claude Code's do) is not standing in its parent's
                # harness tree: `claim` adopted that tree and branch
                # (B7c7a0d9222), and for an item claimed --no-worktree,
                # `gate_run` ran the parent's tree as the subagent's pass and
                # `merge` landed the parent's branch (B11e4c5a185). Asked from
                # the primary, the ITEM decides -- its own tree and branch,
                # else the "name the branch" answers -- as from the CLI.
                where = self.repo
            extra = {}
            if spec.get("wants_progress") and (say := self._progress(params)):
                extra["on_progress"] = say
            result = spec["api"](self.repo, args, agent, called_from=where, **extra)
        elif spec.get("wants_progress") and (say := self._progress(params)):
            result = spec["api"](self.repo, args, agent, on_progress=say)
        else:
            result = spec["api"](self.repo, args, agent)
        return result

    def _override_skew(self, reason: Any, agent: str) -> None:
        """Record the session-scoped skew override this call carries (`allow_older_version`)."""

        if not isinstance(reason, str):
            raise ValueError("allow_older_version must be a string: the reason")
        cfg = Config.load(self.repo)
        log = EventLog(self.repo, _identity().resolve(self.repo, cfg, agent).id, log_cfg=cfg.log)
        log.override_skew(reason)

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """One JSON-RPC message, in whichever era the message itself declares.

        A request carrying the modern `_meta` is checked and answered by the `2026-07-28`
        rules (`_modern_check`, `_modernize`); anything else -- `initialize` and every
        request after it -- is served exactly as before. The era is read from each
        message, never from the connection: a modern request relies on nothing an
        earlier request said.
        """
        method = msg.get("method", "")
        if method == "initialize":
            return self._dispatch(msg)
        era = _modern_check(msg)
        if era is None:
            return self._dispatch(msg)
        if "id" not in msg:
            # A notification is never answered -- not with a refusal, and not with a
            # result either (`server/discover` sent without an id would otherwise get one).
            if not isinstance(era, dict):
                self._dispatch(msg)
            return None
        if isinstance(era, dict):
            return era
        if method == "server/discover":
            reply: dict[str, Any] | None = _ok(msg.get("id"), self._discover())
        else:
            reply = self._dispatch(msg, modern=True)
        return _modernize(method, reply)

    def _handshake_text(self) -> str:
        """The instructions of `initialize` / `server/discover`: the fresh interface snapshot's
        text, else the live rendering (stable text plus the upgrade line), which is then queued
        as the next snapshot (`snapshot_due`)."""
        if (snap := _snapshot_served(self)) is not None:
            self._notice_deferred = True
            return snap["instructions"]
        stable, volatile = _instructions_parts(self.repo, self.agent, self.tier)
        self.snapshot_due = stable
        return stable + volatile

    def _deferred_notice(self) -> str:
        """The upgrade line a snapshot-served handshake left out, once, on the first tool result.
        Never fails the call."""
        if not self._notice_deferred:
            return ""
        self._notice_deferred = False
        try:
            return _upgrade_note(self.repo, self.agent)
        except Exception:
            return ""

    def _discover(self) -> dict[str, Any]:
        """`server/discover`: the versions, capabilities, identity and instructions a
        modern client would otherwise have learned from `initialize`. `resultType`, the
        identity in `_meta` and the cache hints are added by `_modernize`, like every
        modern result's."""
        return protocol.discover(self._handshake_text())

    def _dispatch(self, msg: dict[str, Any], *, modern: bool = False) -> dict[str, Any] | None:
        """Answer one JSON-RPC message: the method's own handler, else `method not found`.

        One handler per method (`_METHODS`), each taking the message and the era it was
        sent in. The era is the message's own; only `_structured` falls back on the
        protocol a legacy connection negotiated at `initialize`."""
        method = msg.get("method", "")
        handler = self._METHODS.get(method) if isinstance(method, str) else None
        if handler is None:
            return _err(msg.get("id"), -32601, f"method not found: {method}")
        return handler(self, msg, modern)
