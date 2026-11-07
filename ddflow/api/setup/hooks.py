"""The commit hook: install, uninstall, status, or run the check itself.

Also the canonical-event handlers (`_HOOK_HANDLERS`) the ``ddflow hooks run`` path
dispatches to, one per harness-registered event.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import Config
from ...core import outcome as O
from ...services import claudehooks as CH
from ...services import enforce as E
from ...services import forbidden as FB
from ...services import harnessreg as HR
from ...services import hookio as HI
from ...services import hookwriters as HW
from ...services import identity as ID
from ...services import launchers as LA
from ...services import sessions as S
from ...services.swarm import lanehooks_check as CK
from .._base import _load
from .session import _check_msg, _record_compaction, _session_start


def _hook_line(name: str, armed) -> str:
    """`<name> hook: installed|NOT installed`, and how -- or why not -- when it is not
    ddflow's own hook file, where an operator would otherwise look for it."""
    line = f"{name} hook: {'installed' if armed.via else 'NOT installed'}"
    return f"{line} -- {armed.detail}" if armed.detail else line


def _hook_remedy(armed, check: str) -> str:
    """How to arm `ddflow hooks <check>`. Over a pre-commit-framework hook, `armed.remedy`
    is about the config: `hooks install` refuses a foreign hook, and an edit to the
    generated file is lost at the next `pre-commit install` (B259fd32dbd)."""
    if armed.remedy:
        return armed.remedy
    if check == "check-msg":
        return (
            'Run `ddflow hooks install`, or add `ddflow hooks check-msg "$1"` to '
            "your own commit-msg hook"
        )
    return "Run `ddflow hooks install`"


def _state_line(known: tuple[bool | None, str], missing: str) -> str:
    """One hook's status from its `claudehooks.state_spec` result: installed, UNKNOWN
    (with why), or `missing`."""
    on, why = known
    if on is None:
        return f"UNKNOWN -- {why}"
    return "installed" if on else missing


def _hook_spec(agent: str, name: str) -> CH.HookSpec | None:
    """`CH.spec(agent, name)` without its KeyError: only claude carries the
    session-start/pre-compact/pre-tool hooks, so a non-claude default harness has none."""

    for h in CH.HOOKS:
        if h.agent == agent and h.name == name:
            return h
    return None


def _prompt_hook_line(repo: Path) -> str:

    return "; ".join(
        f"{HR.title_of(h.agent)}: {_state_line(CH.state_spec(repo, h), 'not installed')}"
        for h in CH.HOOKS
        if h.name == "prompt"
    )


def _precompact_line(repo: Path) -> str:
    h = _hook_spec(HR.default_id(), "pre-compact")
    if h is None:
        return "not installed (ddflow has no PreCompact hook for this harness)"
    return _state_line(CH.state_spec(repo, h), f"not installed (`ddflow hooks install {h.flag}`)")


def _pretool_line(repo: Path) -> str:

    h = _hook_spec(HR.default_id(), "pre-tool")
    if h is None:
        return "not installed (ddflow has no PreToolUse hook for this harness)"
    return _state_line(
        CH.state_spec(repo, h),
        f"not installed (`ddflow hooks install {h.flag}` refuses pkill, git stash, git push and "
        "force flags before they run)",
    )


def _agent_hooks(repo: Path, action: str, *, claude: bool, gemini: bool) -> list[str]:
    """Install or remove every table hook of the named harnesses (`claudehooks.HOOKS`)."""

    want = {"claude": claude, "gemini": gemini}
    msgs: list[str] = []
    for h in CH.HOOKS:
        if not want.get(h.agent):
            continue
        if action != "install":
            msgs.append(CH.uninstall_spec(repo, h))
            continue
        msgs.append(CH.install_spec(repo, h))
        # Said once, after the first hook: every line points where this one does.
        if h.name == "session-start" and (note := E.redirect_note(CH.command(h))):
            msgs.append(note)
    return msgs


def _agent_hooks_outcome(
    repo: Path, action: str, claude: bool, gemini: bool, harness: str
) -> O.Outcome:
    """`hooks install|uninstall` for the agents' own hooks: the Claude/Gemini flags and any
    harness the descriptors name, all of them when given together (the first problem
    stops the rest and is the answer)."""
    msgs: list[str] = []
    if claude or gemini:
        try:
            msgs += _agent_hooks(repo, action, claude=claude, gemini=gemini)
        except CH.NewerSettings as exc:  # a newer ddflow's entry: upgrade, not a failure
            return O.refused("hooks", str(exc), message=str(exc), installed=E.installed(repo))
        except CH.SettingsError as exc:
            return O.failed("hooks", str(exc), message=str(exc), installed=E.installed(repo))
    if harness:
        got = _harness_hooks(repo, action, harness)
        if got.exit != O.OK:
            return got
        msgs.append(got.data["message"])
    return O.ok(
        "hooks",
        message="\n".join(msgs),
        installed=E.installed(repo),
        **({"session_hook": CH.installed(repo)} if claude or gemini else {}),
    )


def _harness_hooks(repo: Path, action: str, harness: str) -> O.Outcome:
    """`hooks install|uninstall --harness <id>` for an agent the descriptor names: the writer
    its hook style selects (`services.hookwriters`); a style with no writer is refused."""
    h = HR.get(harness)
    if h is None:
        return O.failed("hooks", f"no harness {harness!r}; known: {', '.join(HR.ids())}")
    try:
        msgs = HW.apply(repo, h, action)
    except HW.Unsupported as exc:
        why = f"no hook writer for harness {harness!r} yet: {exc}"
        return O.failed("hooks", why, message=why, installed=E.installed(repo))
    except CH.NewerSettings as exc:
        return O.refused("hooks", str(exc), message=str(exc), installed=E.installed(repo))
    except CH.SettingsError as exc:
        return O.failed("hooks", str(exc), message=str(exc), installed=E.installed(repo))
    return O.ok("hooks", message="\n".join(msgs), installed=E.installed(repo))


def _prompt_core(repo: Path, agent: str, session_id: str, text: str, model: str) -> O.Outcome:
    """Record one operator prompt (redacted inside `sessions.capture_prompt`). Raises on a
    problem; both callers turn that into a quiet `skipped`."""

    cfg = Config.load(repo)
    # A short lock wait: the hook must not make the operator's turn wait on a busy log.
    log, _who = ID.open_log(repo, cfg, agent, lock_timeout_s=min(cfg.lease.acquire_timeout_s, 3.0))
    result = S.capture_prompt(
        log,
        cfg,
        session_id,
        text,
        model=model,
        tool="hook",
        # This runs in the process the harness started for the hook: its start is
        # the firing, and the imports and log read since are its own delay.
        fired_at=S.process_started_at(),
    )
    return O.ok("hooks", message="", result=result)


def _capture_prompt(repo: Path, stdin: str, agent: str) -> O.Outcome:
    """What the prompt hook does with the harness's JSON. NEVER fails the user's turn:
    every problem becomes a quiet `skipped` with the reason, and the exit is always 0.

    Redaction happens inside `sessions.capture_prompt`, before the event is built.
    """

    try:
        payload = json.loads(stdin) if stdin.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("the hook's stdin is not a JSON object")
        text = payload.get("prompt")
        if not isinstance(text, str):
            return O.ok("hooks", message="", result="skipped", why="no prompt in the hook JSON")
        sid = str(payload.get("session_id") or payload.get("conversation_id") or "")
        return _prompt_core(repo, agent, sid, text, str(payload.get("model") or ""))
    except Exception as exc:  # a hook must not break the prompt it observes
        return O.ok("hooks", message="", result="skipped", why=f"{type(exc).__name__}: {exc}")


@dataclass(frozen=True)
class _HookResult:
    """What one canonical event's handler produced."""

    context: str = ""  #: text the agent should see, shaped by its emitter ("" for none)
    note: str = ""  #: a diagnostic for stderr; never a decision
    result: str = ""  #: what the handler did, for tests and `--json`
    deny: str = ""  #: pre_tool only: refuse the tool call, showing the agent this reason


def _synth_stdin(p) -> str:
    """The payload as the existing handlers read it: the dialect-independent facts
    (`session_id`, `source`) laid over the agent's own JSON."""
    raw = dict(p.raw)
    if p.session_id:
        raw["session_id"] = p.session_id
    if p.source:
        raw["source"] = p.source
    return json.dumps(raw)


def _on_session_start(repo: Path, agent: str, p, plan, *, dry_run: bool = False) -> _HookResult:
    if "session_start" not in plan.inject:
        # This agent's SessionStart hook cannot show the model anything, so building the
        # brief (an external sync, a cadence check, the log fold) would be wasted work.
        return _HookResult(result="not injectable")
    out = _session_start(repo, agent, _synth_stdin(p), dry_run=dry_run)
    return _HookResult(context=out.data["message"], result="injected")


def _on_prompt(repo: Path, agent: str, p, plan, *, dry_run: bool = False) -> _HookResult:
    if p.prompt is None:
        return _HookResult(result="skipped")  # silent, as the prompt hook always was
    if dry_run:  # the probe records nothing: the reply is shaped, the log stays untouched
        return _HookResult(result="not recorded (dry-run)")
    try:
        out = _prompt_core(repo, agent, p.session_id, p.prompt, p.model)
    except Exception:  # a hook must not break the prompt it observes
        return _HookResult(result="skipped")
    return _HookResult(result=str(out.data.get("result", "")))


def _on_pre_compact(repo: Path, agent: str, p, plan, *, dry_run: bool = False) -> _HookResult:
    if p.malformed:
        return _HookResult(
            result="skipped",
            note="ddflow pre-compact: ValueError: the hook's stdin is not a JSON object",
        )
    if dry_run:
        # Nothing is recorded, so nothing can block: the note says a probe was computed.
        return _HookResult(result="dry-run", note="ddflow pre-compact: dry-run")
    out = _record_compaction(repo, _synth_stdin(p), agent)
    result = str(out.data.get("result", ""))
    # stdout stays empty: PreCompact output can only block, never inform (B195). Why a run
    # recorded nothing goes to stderr, which no agent treats as a decision.
    note = "" if result in ("recorded", "off") else str(out.data.get("why") or result)
    return _HookResult(result=result, note=note and f"ddflow pre-compact: {note}")


def _on_pre_tool(repo: Path, agent: str, p, plan, *, dry_run: bool = False) -> _HookResult:
    """Refuse a file write outside the agent's lease globs, or a Bash command that breaks
    `[hooks].forbidden_commands`, before it runs. Anything else (another tool, no path, no
    command, any problem) is allowed: a guard that fails closed on its own bug would stop
    every command the agent runs."""
    # The lease-glob check for a tool call that names a file path (reuses the lane check,
    # SW-lane-hooks): a write outside the globs is refused before it happens, and an agent
    # holding no live lease is stopped. Paths are judged against the repo root, where `_mine`
    # roots the lease globs.
    paths = CK.paths_of(p.raw)
    cfg = Config.load(repo)
    logged = ""  # a `log`-mode refusal: noted and allowed, never a blocker
    if paths and cfg.locks.mode != "off":
        code, denial = CK.verdict(repo, agent, paths, Path(p.cwd or Path.cwd()), root=repo)
        if code == CK.REFUSE:
            if cfg.locks.mode == "log":
                logged = denial
            else:
                return _HookResult(result="denied", deny=denial, note=denial)
    tool_input = p.raw.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if p.tool.lower() not in ("bash", "run_shell_command", "shell") or not isinstance(command, str):
        return _HookResult(result="logged", note=logged) if logged else _HookResult(result="allowed")
    refusal = FB.check(command, cfg, cwd=p.cwd or Path.cwd())
    if refusal is None:
        return _HookResult(result="logged", note=logged) if logged else _HookResult(result="allowed")
    if not dry_run:
        try:
            holds = (
                HI.emit_deny(plan, "pre_tool", "") is not None
            )  # only a refusal that holds is recorded
        except Exception:
            holds = True
        if holds:
            try:
                log, _who = ID.open_log(
                    repo, cfg, agent, lock_timeout_s=min(cfg.lease.acquire_timeout_s, 3.0)
                )
                FB.record(log, cfg, refusal, p.session_id, p.harness)
            except Exception:  # an unrecorded refusal is still a refusal
                pass
    msg = refusal.message()
    return _HookResult(result="denied", deny=msg, note=msg)


def _on_nothing(repo: Path, agent: str, p, plan, *, dry_run: bool = False) -> _HookResult:
    """pre_tool, post_tool, stop and session_end: the entry point accepts them so an agent's
    hook can be wired to every event today; the handlers (heartbeat, stop protocol, session
    end) arrive with the tasks that need them."""
    return _HookResult(result="no handler")


#: One handler per canonical event (`harnessreg.CANONICAL_EVENTS`).
_HOOK_HANDLERS = {
    "session_start": _on_session_start,
    "prompt": _on_prompt,
    "pre_compact": _on_pre_compact,
    "pre_tool": _on_pre_tool,
    "post_tool": _on_nothing,
    "stop": _on_nothing,
    "session_end": _on_nothing,
}


def _run_hook(
    repo: Path, event: str, harness: str, agent: str, stdin: str, *, dry_run: bool = False
) -> O.Outcome:
    """`ddflow hooks run <event> --harness <id>`: normalize the agent's JSON, run the event's
    handler, shape the reply. ALWAYS exit 0 and never raise: a failing hook blocks, in some
    agents, the very turn it observes. Every problem becomes a `note` for stderr.

    `dry_run` computes and shapes the reply the real run would, omitting its
    write-producing sections, and writes no event: probing the hook must not append to
    the committed event log.
    """
    handler = _HOOK_HANDLERS.get(event)
    if handler is None:
        return O.ok(
            "hooks",
            message="",
            stdout="",
            result="skipped",
            note=f"unknown event {event!r}",
            dry_run=dry_run,
        )
    try:
        plan = HI.plan_for(harness)
        if plan is None:
            return O.ok(
                "hooks",
                message="",
                stdout="",
                result="skipped",
                note=f"no command-hook descriptor for harness {harness!r}",
                dry_run=dry_run,
            )
        res = handler(
            repo, agent, HI.normalize(plan.normalizer, event, harness, stdin), plan, dry_run=dry_run
        )
        out = HI.emit(plan, event, res.context)
        if res.deny:
            denied = HI.emit_deny(plan, event, res.deny)
            if denied is None:
                res = _HookResult(
                    result="not enforceable",
                    note=f"{harness} has no deny reply for ddflow to use; not refused: {res.deny}",
                )
            else:
                out = denied
    except Exception as exc:  # a hook must never stand between the operator and their agent
        return O.ok(
            "hooks",
            message="",
            stdout="",
            result="skipped",
            note=f"{type(exc).__name__}: {exc}",
            dry_run=dry_run,
        )
    return O.ok("hooks", message=out, stdout=out, result=res.result, note=res.note, dry_run=dry_run)


def _hooks_status(repo: Path, cfg) -> O.Outcome:
    """What is installed, and whether the installed hook and the policy agree."""

    commit_hook = E.armed(repo, "pre-commit")
    on = bool(commit_hook.via)
    mode = cfg.enforce.commit_without_lease
    # The two halves must AGREE or neither enforces anything, and each mismatch reads
    # differently: a hook with a `warn` policy reports and allows, while a `block` policy
    # with no hook is a rule nothing applies.
    note = ""
    if on and mode == "warn":
        note = (
            "\n\nNOTE: the hook is installed but the policy is 'warn', so it reports and "
            "allows. Set it to 'block' to refuse."
        )
    elif not on and mode == "block":
        # Over a pre-commit-framework hook a hook IS installed; what is missing is ddflow's
        # check in it, and "no hook" would send the operator to reinstall the framework.
        missing = (
            "NO HOOK RUNS ddflow's check (the pre-commit framework's hook does not)"
            if commit_hook.framework
            else "NO HOOK IS INSTALLED"
        )
        note = (
            f"\n\nNOTE: the policy is 'block' but {missing}, so nothing enforces "
            f"it. {_hook_remedy(commit_hook, 'check-commit')}."
        )
    start = _hook_spec(HR.default_id(), "session-start")
    if start is None:
        known = (False, "")
        session_line = "not installed (ddflow has no SessionStart hook for this harness)"
    else:
        known = CH.state_spec(repo, start)
        session_line = _state_line(
            known,
            f"not installed (`ddflow hooks install {start.flag}` puts the brief in every session)",
        )
    session = known[0]
    msg_armed = E.armed(repo, "commit-msg")
    msg_hook = bool(msg_armed.via)
    trailer_line = _hook_line("commit-msg", msg_armed)
    if cfg.enforce.require_item_trailer and not msg_hook:
        # The rule lives in the commit-msg hook now; a required trailer with no hook
        # to check it is a rule nothing applies (roborev 827).
        trailer_line += (
            " -- but [enforce].require_item_trailer is ON, so NOTHING checks it. "
            f"{_hook_remedy(msg_armed, 'check-msg')}."
        )
    # The SessionStart/PreCompact/PreToolUse hook specs come from the DEFAULT harness
    # (`CH.spec(HR.default_id(), ...)`), so their label must follow the same source rather
    # than hard-coding one harness (bug B-setup-hooks-status-claude-label).
    hr_title = HR.title_of(HR.default_id())
    message = (
        f"{_hook_line('pre-commit', commit_hook)}\n"
        f"policy [enforce].commit_without_lease = {mode!r}{note}\n"
        f"{trailer_line}\n"
        f"{hr_title} SessionStart hook: {session_line}\n"
        f"{hr_title} PreCompact hook: {_precompact_line(repo)}\n"
        f"{hr_title} PreToolUse hook: {_pretool_line(repo)}\n"
        f"prompt capture hook: {_prompt_hook_line(repo)}"
    )

    dangling = LA.findings(repo)
    if dangling:
        message += "\nDANGLING LAUNCHER: " + "\nDANGLING LAUNCHER: ".join(
            d.render() for d in dangling
        )
    data = {
        "installed": on,
        "policy": mode,
        "session_hook": session,
        "trailer_hook": msg_hook,
        # How each is armed: "ddflow", "pre-commit", "pre-commit legacy", or "".
        "armed_via": {"pre-commit": commit_hook.via, "commit-msg": msg_armed.via},
        "dangling": [d.render() for d in dangling],
        "message": message,
    }
    if on or mode == "off":
        return O.ok("hooks", **data)
    return O.nothing("hooks", message, **data)


def _installed_hooks(E: Any, repo: Path, msg: str) -> O.Outcome:
    """The outcome of `E.install`'s message (``E`` is `services.enforce`): a refusal over
    a hook a NEWER ddflow wrote is exit 3 (upgrade), any other REFUSED is a failure, the
    rest succeeded."""
    installed = E.installed(repo)
    if E.NEWER_HINT in msg:  # the commit-msg hook's too: install() relabels it NOT INSTALLED
        return O.refused("hooks", msg, message=msg, installed=installed)
    if msg.startswith("REFUSED"):
        return O.failed("hooks", msg, message=msg, installed=installed)
    return O.ok("hooks", message=msg, installed=installed)


def hooks(  # noqa: PLR0913 -- one keyword per hooks mode; the CLI and MCP map 1:1
    repo: Path,
    *,
    action: str = "status",
    force: bool = False,
    claude: bool = False,
    msg_file: str = "",
    agent: str = "",
    gemini: bool = False,
    stdin: str = "",
    event: str = "",
    harness: str = "",
    dry_run: bool = False,
) -> O.Outcome:
    """The commit hook: install, uninstall, status, or run the check itself.

    `claude=True` installs or removes the Claude Code SessionStart and UserPromptSubmit
    hooks instead (`services.claudehooks`); `gemini=True` the Gemini CLI BeforeAgent
    one. `session-start` is what the first runs; `prompt` records the operator's prompt
    from the hook's JSON on `stdin`. `run`'s `dry_run` computes and shapes the reply
    and writes no event.
    """

    if action == "run":
        return _run_hook(repo, event, harness, agent, stdin, dry_run=dry_run)
    if action == "session-start":
        return _session_start(repo, agent, stdin)
    if action == "pre-compact":
        return _record_compaction(repo, stdin, agent)
    if action == "prompt":
        return _capture_prompt(repo, stdin, agent)
    if action == "check-msg":
        # The config alone, not `_load`: this runs on EVERY commit, and folding the whole
        # log up front cost ~120 ms on a 5k-event log whether or not a trailer needed the
        # queue. `queue_ids` reads it only when a trailer names an item, from the index
        # when that is current. Nothing here needs the resolved identity.

        return _check_msg(repo, Config.load(repo), msg_file)
    _log, cfg, _st = _load(repo, agent)
    if action in ("install", "uninstall") and (claude or gemini or harness):
        return _agent_hooks_outcome(repo, action, claude, gemini, harness)
    if action == "install":
        return _installed_hooks(E, repo, E.install(repo, force=force))
    if action == "uninstall":
        msg = E.uninstall(repo)
        return O.ok("hooks", message=msg, installed=E.installed(repo))
    if action == "check-commit":
        code, msg = E.check_commit(repo, cfg)
        for check in (E.check_views, E.check_docs, E.check_drift):
            if code != 0:
                break
            ccode, cmsg = check(repo, cfg)
            msg = "\n".join(x for x in (msg, cmsg) if x)
            code = ccode
        data = {
            "message": msg,
            "installed": E.installed(repo),
            "policy": cfg.enforce.commit_without_lease,
        }
        if code == 0:
            return O.ok("hooks", **data)
        return O.Outcome(kind="hooks", data=data, exit=code, reason=msg)

    return _hooks_status(repo, cfg)
