"""The hook entry point's two translation halves: payload normalizers and output emitters.

Every coding agent that can run a command hook calls ddflow the same way
(``ddflow hooks run <event> --harness <id>``) and speaks its own dialect on both sides of
that call. This module is the dialect, nothing else:

* a **normalizer** reads the agent's stdin JSON into one `HookPayload` (the canonical
  event, the session id, the prompt text, the start source, the model, the working
  directory), whatever the agent calls those fields;
* an **emitter** shapes what ddflow wants the agent to see -- the session brief, say --
  into what that agent's hook contract accepts: plain stdout text, `additionalContext`
  JSON, Cursor's `additional_context`, Cline's `contextModification`, or nothing at all.

What happens BETWEEN them -- the handler for each canonical event -- lives with the
operations it calls (`api.setup`), because a service may not import the API above it.

The names a descriptor gives (`[hooks] normalizer`, `emitter`) are the keys of `NORMALIZERS`
and `EMITTERS`; `tests/test_hook_core.py` pins that every command-hook descriptor resolves.

Two properties are load-bearing:

* **Nothing here raises.** A hook that fails blocks, in some agents, the very turn it
  observes (Claude and Codex treat exit 2 as a block; Copilot's preToolUse fails closed).
  A payload that is not JSON, not an object or missing every field normalizes to an
  empty `HookPayload`, and the handler decides what an empty payload means.
* **An emitter only injects where the descriptor says the agent honours it.** Copilot CLI
  drops a prompt hook's output; Grok Build discards SessionStart's. Printing context there
  would be a silent no-op at best, so the emitter prints the agent's neutral reply instead.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import harnessreg

CANONICAL = harnessreg.CANONICAL_EVENTS


@dataclass(frozen=True)
class HookPayload:
    """One hook firing, in ddflow's words."""

    event: str  #: a canonical event
    harness: str  #: the descriptor id the call named
    session_id: str = ""
    prompt: str | None = None  #: the operator's prompt; None when the payload carried none
    source: str = ""  #: SessionStart's startup|resume|clear|compact; "" when not stated
    model: str = ""
    cwd: str = ""
    tool: str = ""  #: the tool name on a tool event
    #: the parsed stdin, for a handler that needs more
    raw: dict[str, Any] = field(default_factory=dict)
    malformed: bool = False  #: stdin was present but not a JSON object


@dataclass(frozen=True)
class FieldMap:
    """Where one dialect keeps each fact: dotted paths (`a.b.0`), first non-empty wins."""

    session: tuple[str, ...] = ("session_id",)
    prompt: tuple[str, ...] = ()
    source: tuple[str, ...] = ("source",)
    model: tuple[str, ...] = ()
    cwd: tuple[str, ...] = ("cwd",)
    tool: tuple[str, ...] = ()


#: Claude's dialect, which Codex, VS Code and Goose share. Session id falls back to
#: `conversation_id` because that is what the first prompt hook accepted (Cursor sends it).
_CLAUDE = FieldMap(
    session=("session_id", "conversation_id"),
    prompt=("prompt",),
    model=("model",),
    tool=("tool_name",),
)

NORMALIZERS: dict[str, FieldMap] = {
    "claude": _CLAUDE,
    "codex": _CLAUDE,
    "vscode": _CLAUDE,
    "goose": FieldMap(
        session=("session_id",), prompt=("prompt", "message"), model=("model",), tool=("tool_name",)
    ),
    "gemini": FieldMap(prompt=("prompt",), tool=("tool_name",)),
    # Copilot's camelCase form and its PascalCase (Claude-compatible) form both arrive here.
    "copilot": FieldMap(
        session=("sessionId", "session_id"),
        prompt=("prompt", "initialPrompt"),
        tool=("toolName", "tool_name"),
    ),
    "cursor": FieldMap(
        session=("conversation_id", "session_id"),
        prompt=("prompt",),
        source=(),
        model=("model",),
        cwd=("workspace_roots.0",),
    ),
    "antigravity": FieldMap(
        session=("conversationId",),
        source=(),
        model=("modelName",),
        cwd=("workspacePaths.0",),
        tool=("toolCall.name",),
    ),
    "cline": FieldMap(
        session=("taskId",),
        prompt=("userPromptSubmit.prompt", "prompt"),
        source=(),
        cwd=("workspaceRoots.0",),
        tool=("preToolUse.toolName",),
    ),
    "cascade": FieldMap(
        session=("trajectory_id",),
        prompt=("tool_info.user_prompt",),
        source=(),
        model=("model_name",),
        cwd=("tool_info.cwd",),
    ),
}


def _dig(raw: Any, path: str) -> Any:
    cur = raw
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _first_text(raw: dict[str, Any], paths: tuple[str, ...]) -> str | None:
    """The first path holding a non-empty string; None when none does."""
    for p in paths:
        v = _dig(raw, p)
        if isinstance(v, str) and v:
            return v
    return None


def parse_stdin(stdin: str) -> dict[str, Any]:
    """The hook's stdin as an object; ``{}`` for empty, invalid or non-object input."""
    try:
        data = json.loads(stdin) if stdin.strip() else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_empty_object(stdin: str) -> bool:
    try:
        return json.loads(stdin) == {}
    except ValueError:
        return False


def normalize(normalizer: str, event: str, harness: str, stdin: str) -> HookPayload:
    """Read ``stdin`` in the ``normalizer`` dialect. An unknown dialect falls back to the
    Claude one, which is the most common shape; the registry test keeps that unreachable
    for a shipped descriptor."""
    raw = parse_stdin(stdin)
    malformed = bool(stdin.strip()) and not raw and not _is_empty_object(stdin)
    fm = NORMALIZERS.get(normalizer, _CLAUDE)
    prompt = _first_text(raw, fm.prompt)
    # A `prompt` that is present but not text (a malformed hook) is "no prompt", as before.
    return HookPayload(
        event=event,
        harness=harness,
        session_id=_first_text(raw, fm.session) or "",
        prompt=prompt,
        source=_first_text(raw, fm.source) or "",
        model=_first_text(raw, fm.model) or "",
        cwd=_first_text(raw, fm.cwd) or "",
        tool=_first_text(raw, fm.tool) or "",
        raw=raw,
        malformed=malformed,
    )


# --- emitters --------------------------------------------------------------------------

#: An emitter: (canonical event, the context ddflow wants shown or "", whether this agent
#: honours injection at this event) -> the exact stdout text. The exit code is always 0.
Emitter = Callable[[str, str, bool], str]

_SESSION_EVENT_NAMES = {"session_start": "SessionStart", "prompt": "UserPromptSubmit"}


def _plain(event: str, text: str, inject: bool) -> str:
    """Stdout IS the context (Claude Code, Codex): print it, or nothing."""
    return text if inject and text else ""


def _json_context(event: str, text: str, inject: bool) -> str:
    """`hookSpecificOutput.additionalContext` (Gemini, VS Code): JSON only, `{}` when silent."""
    if not (inject and text):
        return "{}"
    name = _SESSION_EVENT_NAMES.get(event, "SessionStart")
    return json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}})


def _copilot(event: str, text: str, inject: bool) -> str:
    return json.dumps({"additionalContext": text}) if inject and text else ""


def _cursor(event: str, text: str, inject: bool) -> str:
    return json.dumps({"additional_context": text}) if inject and text else "{}"


def _cline(event: str, text: str, inject: bool) -> str:
    return json.dumps({"contextModification": text}) if inject and text else "{}"


def _antigravity(event: str, text: str, inject: bool) -> str:
    # PreInvocation's `injectSteps` entry shape is NOT VERIFIED (docs/RESEARCH.md R-hx), so
    # this emitter stays silent until a recorded fixture shows it: `{}` is a no-op reply.
    return "{}"


def _silent(event: str, text: str, inject: bool) -> str:
    """Agents whose hook output cannot reach the model (Windsurf Cascade) or is unverified."""
    return ""


EMITTERS: dict[str, Emitter] = {
    "claude": _plain,
    "codex": _plain,
    "gemini": _json_context,
    "vscode": _json_context,
    "copilot": _copilot,
    "cursor": _cursor,
    "cline": _cline,
    "antigravity": _antigravity,
    "cascade": _silent,
    "goose": _silent,
}


@dataclass(frozen=True)
class Plan:
    """What `hooks run` needs to know about a harness before it does anything."""

    harness: str
    normalizer: str
    emitter: str
    inject: tuple[str, ...]


def plan_for(harness: str) -> Plan | None:
    """The normalizer, emitter and injection events for ``harness``; None when ddflow has no
    command-hook descriptor for it (unknown id, no hooks section, plugin or no hooks)."""
    h = harnessreg.get(harness)
    if h is None or h.hooks is None or h.hooks.style not in harnessreg.COMMAND_HOOK_STYLES:
        return None
    return Plan(harness, h.hooks.normalizer, h.hooks.emitter, h.hooks.inject)


def emit(plan: Plan, event: str, text: str) -> str:
    """The stdout for ``event`` under ``plan``: ``text`` shaped for the agent, or its neutral reply."""
    fn = EMITTERS.get(plan.emitter, _silent)
    return fn(event, text, event in plan.inject)
