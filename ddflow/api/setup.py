"""Adoption and configuration: companions, config, prompts, hooks, help, setup.

The last family off the argv path (B97). Everything here is operator-facing one-shot work
rather than queue policy, which is why it went last — but two of the refusals are as
load-bearing as anything in the pipeline:

* **Registering a companion that is not installed** writes a launch command that fails
  mid-task, as an agent reaches for the tool a gate told it to use. And "not installed"
  is kept distinct from "could not tell": sending the first to an operator whose probe
  merely TIMED OUT makes them install something they already have.
* **A config append is validated against the MERGED text**, not against what is already on
  disk. Validating the state you are replacing has checked nothing — an unknown section
  went in and every later command failed to load the file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import csv_list
from ..core import outcome as O
from ._base import _load


def _scan(repo: Path, *, probe: bool = True):
    """Statuses, or an Outcome explaining why not. ONE guard, used by every caller.

    The registry loader writes a careful sentence naming the file, the id and the bad
    value; letting its `ValueError` escape renders that sentence as an uncaught exception
    and makes the exit code an accident rather than a contract.
    """
    from ..services import companions as CO

    try:
        return CO.scan(repo, probe=probe)
    except ValueError as exc:
        return O.failed(
            "companions",
            f"{exc}\n  (in .ddflow/companions.toml or .ddflow/config.toml)",
            companions=[],
        )


def companions(repo: Path, *, no_probe: bool = False, agent: str = "") -> O.Outcome:
    """Which companion servers serve this project's gates, and what is missing.

    Exit 0 when every default companion is registered; exit 2 on a gap — "no data"
    reported as itself. NEVER exit 1: a missing optional server is a gap to close, not a
    failure of this command.
    """
    from ..services import companions as CO

    statuses = _scan(repo, probe=not no_probe)
    if isinstance(statuses, O.Outcome):
        return statuses
    _log, cfg, _st = _load(repo, agent)
    pipeline = list(cfg.gates.task_pipeline)
    cover = CO.gate_coverage(repo, statuses, pipeline)
    data: dict[str, Any] = {
        "companions": [
            {
                "id": st.companion.id,
                "title": st.companion.title,
                "gates": st.companion.gates,
                "state": st.state,
                "registered_in": st.registered_in,
                "registered_as": st.registered_as,
                "detail": st.detail,
                "install": st.companion.install,
                "url": st.companion.url,
                "default": st.companion.default,
                "kind": st.companion.kind,
            }
            for st in statuses
        ],
        "gate_coverage": cover,
        "uncovered_gates": [g for g, ids in cover.items() if not ids],
        "_render": {"statuses": statuses},
    }
    # "Registered" is not a state a `cli` companion can reach -- there is nothing to
    # register. Judging one by it would make this exit 2 forever the moment a cli entry
    # joined the registry, which trains the reader to ignore the exit code. For those,
    # INSTALLED is the goal state.
    gaps = [st for st in statuses if st.is_gap]
    if gaps:
        return O.nothing("companions", f"{len(gaps)} companion gap(s)", **data)
    return O.ok("companions", **data)


@dataclass
class Registration:
    """What to register, for whom. Named because five fields travel together through the
    argparse flags, the MCP input schema and `CO.register`."""

    ids: str = ""
    agents: str = ""
    force: bool = False
    dry_run: bool = False


def companions_add(repo: Path, reg: Registration | None = None, *, agent: str = "") -> O.Outcome:
    """Register companion servers with an agent's MCP config.

    Four refusals, and the distinction between the last two is the one that matters — see
    the module docstring.
    """
    from ..services import companions as CO

    reg = reg or Registration()
    statuses = _scan(repo, probe=True)
    if isinstance(statuses, O.Outcome):
        return statuses
    by_id = {st.companion.id: st for st in statuses}

    wanted = csv_list(reg.ids) or [
        st.companion.id
        for st in statuses
        if st.companion.default and st.installed and st.companion.is_mcp  # servers only
    ]
    unknown_ids = [w for w in wanted if w not in by_id]
    if unknown_ids:
        return O.failed(
            "companions.added",
            f"unknown companion(s): {', '.join(unknown_ids)}; known: {', '.join(by_id)}",
            actions=[],
            applied=False,
            written=0,
            refused=[],
        )
    not_servers = [w for w in wanted if not by_id[w].companion.is_mcp]
    if not_servers:
        return O.refused(
            "companions.added",
            "not an MCP server: "
            + "; ".join(
                f"{w} is a {by_id[w].companion.kind} tool ({by_id[w].companion.install})"
                for w in not_servers
            )
            + ". There is no MCP config entry to write. Install it and ddflow detects it; "
            "`ddflow companions` shows it either way.",
            actions=[],
            applied=False,
            written=0,
            refused=not_servers,
        )
    if not wanted:
        return O.nothing(
            "companions.added",
            "nothing to add: no default MCP companion is installed on this machine. "
            "`ddflow companions` lists them with their install commands.",
            actions=[],
            applied=False,
            written=0,
            refused=[],
        )

    # `is False` and `is None` are different refusals. Both block -- registering a launch
    # command that fails mid-task is the thing to avoid either way -- but "not installed
    # here" sent to an operator whose probe merely TIMED OUT makes them install something
    # they already have, and the install line then does nothing. Say which one it is.
    absent = [w for w in wanted if by_id[w].installed is False and not reg.force]
    untested = [w for w in wanted if by_id[w].installed is None and not reg.force]
    if absent or untested:
        parts = []
        if absent:
            parts.append(
                f"not installed here: {', '.join(absent)}. Registering one would write a "
                f"launch command that fails mid-task. Install it first "
                f"({'; '.join(by_id[w].companion.install for w in absent)}), or --force if "
                f"you are about to."
            )
        if untested:
            parts.append(
                f"could not tell whether these are installed: {', '.join(untested)} — "
                + "; ".join(by_id[w].detail for w in untested)
                + ". That is not the same as absent. Re-run, check by hand, or --force if "
                "you know it is there."
            )
        return O.refused(
            "companions.added",
            "\n".join(parts),
            actions=[],
            applied=False,
            written=0,
            refused=absent + untested,
        )

    agents = csv_list(reg.agents) or ["claude"]
    results = [
        CO.register(repo, by_id[w].companion, ag, dry_run=reg.dry_run)
        for w in wanted
        for ag in agents
    ]
    actions = [msg for _st, msg in results]
    # A REFUSAL is not a success. `register` used to return only the message, so an
    # unparseable `.mcp.json` printed "SKIPPED ... not valid JSON", reported
    # `applied: true` and exited 0 -- nothing written, surface saying otherwise.
    refused = [msg for st, msg in results if st == "refused"]
    wrote = [msg for st, msg in results if st == "written"]
    data: dict[str, Any] = {
        "actions": actions,
        "applied": bool(wrote) and not reg.dry_run,
        "written": len(wrote),
        "refused": refused,
        "dry_run": reg.dry_run,
    }
    if refused:
        return O.failed("companions.added", f"{len(refused)} refused", **data)
    return O.ok("companions.added", **data)


@dataclass
class ConfigEdit:
    """One config change, or a read. `set`+`value` OR `append_toml`, never both.

    `local` sends the change to the git-ignored `.ddflow/local/config.toml`: this
    machine's endpoints, hosts, keys' variable names and sizing. Without it the change
    goes to the committed `.ddflow/config.toml`, which every clone receives.
    """

    set: str = ""
    value: str = ""
    append_toml: str = ""
    filter: str = ""
    explain: bool = False
    local: bool = False


def _sync_attributes(repo: Path) -> list[str]:
    """Write the `merge=union` lines `[lease] append_only_globs` now asks for (D-shared-
    globs), at the moment the setting changes and beside the config it came from. Only
    the COMMITTED config's globs: a `--local` edit writes no tracked rule."""
    from ..services import shared_files as SF

    return SF.sync_attributes(repo)


def _said(added: list[str]) -> str:
    if not added:
        return ""
    return "\nadded to .gitattributes (tracked -- commit it with the config):\n" + "\n".join(
        f"  {ln}" for ln in added
    )


def configure(repo: Path, edit: ConfigEdit | None = None, *, agent: str = "") -> O.Outcome:
    """Read every knob with its source, or change one.

    An append is validated against the MERGED text — see the module docstring for why
    validating what is already on disk checks nothing.
    """
    from ..services.configwrite import _append_config, _toml_literal, _write_config, config_file

    edit = edit or ConfigEdit()
    _log, cfg, _st = _load(repo, agent)
    target = str(config_file(repo, local=edit.local))

    if edit.set:
        err, _text = _write_config(repo, [(edit.set, edit.value)], local=edit.local, agent=agent)
        if err:
            return O.failed("config", err, key=edit.set, value=edit.value, rows=[], text="")
        added = [] if edit.local else _sync_attributes(repo)
        return O.ok(
            "config",
            key=edit.set,
            value=edit.value,
            literal=_toml_literal(edit.value),
            path=target,
            local=edit.local,
            rows=[],
            gitattributes_added=added,
            text=f"{edit.set} = {_toml_literal(edit.value)}  ({target})" + _said(added),
        )

    if edit.append_toml:
        # Validated BEFORE writing: an agent composing TOML gets a parse error back as a
        # readable message instead of leaving the project with a config no later command
        # can load.
        err, path = _append_config(repo, edit.append_toml, local=edit.local, agent=agent)
        if err:
            return O.failed("config", err, path="", rows=[], text="")
        added = [] if edit.local else _sync_attributes(repo)
        return O.ok(
            "config",
            path=str(path),
            appended=True,
            local=edit.local,
            rows=[],
            gitattributes_added=added,
            text=f"appended to {path}" + _said(added),
        )

    from ..views import human

    rows = [
        {"key": k, "value": v, "source": s, "doc": d}
        for k, v, s, d in cfg.explain()
        if not edit.filter or edit.filter in k
    ]
    out = O.ok("config", rows=rows, explain=edit.explain, filter=edit.filter, text="")
    out.data["text"] = human.render(out)
    return out


_BUDGET_KNOBS = ("review.max_rounds", "review.on_exceed", "review.delta_default")


def _budget_keys(edit: ConfigEdit) -> list[str]:
    """The `review.*` knobs this edit sets, parsed (not substring-matched): quoting,
    spacing and a commented-out header do not change what TOML says."""
    import tomllib

    def norm(k: str) -> str:
        return ".".join(seg.strip().strip("\"'") for seg in k.split("."))

    # `configure` applies `set` and returns; an `append_toml` beside it is never written
    # (the probe is in tests/test_review_budget.py), so only the one that IS applied counts.
    if edit.set:
        return [k for k in _BUDGET_KNOBS if norm(edit.set) == k]
    try:
        table = tomllib.loads(edit.append_toml).get("review")
    except tomllib.TOMLDecodeError:
        return []
    if not isinstance(table, dict):
        return []
    return [f"review.{k}" for k in table if f"review.{k}" in _BUDGET_KNOBS]


def report_budget_change(
    repo: Path, edit: ConfigEdit, out: O.Outcome, *, agent: str = ""
) -> O.Outcome:
    """Tell the operator when an AGENT (the MCP surface) changed the review budget.

    The cap is the operator's: `ddflow_configure` accepts `review.max_rounds` and
    `review.on_exceed` on either layer, so an operator without a shell can tune it, but
    an agent raising or disabling it to get more review rounds must not do so silently.
    The change is returned as a NOTE and recorded as a session note the operator reads
    in `session show` and the brief; a per-item exception is `ddflow review --force
    --reason`, which is recorded on the gate instead.
    """
    if out.exit != O.OK:
        return out
    touched = _budget_keys(edit)
    if not touched:
        return out
    from ..config import ReviewConfig

    default = ReviewConfig()
    layer = "machine-local (.ddflow/local)" if edit.local else "shared (.ddflow/config.toml)"
    revert = "; ".join(
        f"`ddflow config --set {k} {str(getattr(default, k.split('.')[1])).lower()}"
        f"{' --local' if edit.local else ''}`"
        for k in touched
    )
    note = (
        f"NOTE FOR THE OPERATOR: the review budget was changed over MCP "
        f"({', '.join(touched)}; {layer} layer). Shipped default: {default.max_rounds} full "
        f"rounds per gate, on_exceed = {default.on_exceed!r}, delta_default = "
        f"{str(default.delta_default).lower()}. If you did not ask for this, "
        f"revert it with {revert}. Recorded as a session note."
    )
    from .knowledge import session_note

    session_note(repo, "", note, agent=agent)
    out.data["text"] = (out.data.get("text") or "") + "\n" + note
    return out


def _worktree_drift(repo: Path, here: Path) -> str:
    """A warning when `here` is a worktree behind the base branch, else "".

    INFORMS, never refuses: this runs at session start, where the only useful thing is
    to say so. The source project's `check_worktree_sync.py --hook` did the same job; a
    rulebook changed on the base branch since this tree forked is the case that bit it.
    Only a renderer: the drift itself is `enforce.drift`, the one computation the commit
    gate (`enforce.check_drift`, which BLOCKS) reads too -- two copies of "behind" and
    "which rules" would disagree about the same tree.
    """
    from ..infra import worktree as W
    from ..services import enforce as E

    # Outside any git tree there is no branch to be behind, and nothing to say.
    if not W.git(here, "rev-parse", "--show-toplevel").ok:
        return ""
    d = E.drift(repo, here)
    if d.behind is None:
        return f"Could not tell whether this checkout is behind `{d.base}`: {d.detail}"
    if d.behind == 0:
        return ""
    msg = f"This worktree is {d.behind} commit(s) behind `{d.base}`: `git merge {d.base}` before starting."
    if d.rules:
        msg += f" The rules changed there: {', '.join(d.rules)} -- re-read them after merging."
    return msg


def _check_msg(repo: Path, cfg, msg_file: str) -> O.Outcome:
    """The commit-msg hook's check: the trailer, read from the message being committed.

    `repo` is the primary checkout, resolved from wherever git runs the hook exactly as
    for `check-commit`, so a commit in a linked worktree is checked against the one
    queue every worktree shares.
    """
    from ..infra import proc as P
    from ..services import enforce as E

    data: dict[str, Any] = {"message": "", "installed": True, "policy": ""}
    forbidden = list(cfg.enforce.forbidden_trailers)
    if not cfg.enforce.require_item_trailer and not forbidden:
        return O.ok("hooks", **data)
    path = Path(msg_file)
    if not msg_file or not path.is_file():
        # git always passes the file; no file means we were not called by git, and
        # inventing a failure from missing input is the vacuous-FAIL mirror.
        return O.ok("hooks", **data)
    text = path.read_text("utf-8", errors="replace")
    # Before the merge exemption, on purpose: a merge message is written by whoever
    # concludes the merge, which is exactly who adds an attribution line.
    code, msg = E.check_forbidden_trailers(text, forbidden)
    if code:
        data["message"] = msg
        return O.Outcome(kind="hooks", data=data, exit=code, reason=msg)
    if not cfg.enforce.require_item_trailer:
        return O.ok("hooks", **data)
    # A merge really in progress: MERGE_HEAD resolves AND is not already contained in
    # HEAD. A stale or planted MERGE_HEAD pointing at HEAD exempted every ordinary
    # commit from the trailer rule (rubber-duck).
    head = P.run(
        ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    merging = head.returncode == 0 and (
        P.run(
            ["git", "merge-base", "--is-ancestor", head.stdout.strip(), "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        ).returncode
        != 0
    )
    code, msg = E.check_item_trailer(
        text,
        list(cfg.enforce.item_trailer_keys) or ["Item"],
        ids=lambda: E.queue_ids(repo, cfg),
        waivers=dict(cfg.enforce.trailer_waivers),
        merging=merging,
    )
    data["message"] = msg
    if code == 0:
        return O.ok("hooks", **data)
    return O.Outcome(kind="hooks", data=data, exit=code, reason=msg)


def _session_start(repo: Path, agent: str) -> O.Outcome:
    """What the Claude Code SessionStart hook prints. ALWAYS exit 0.

    A hook that fails at session start blocks nothing useful and teaches the operator to
    remove it. Every failure becomes one line of text saying what to run instead.
    """
    from .lifecycle import brief

    parts = ["# ddflow session start", ""]
    try:
        drift = _worktree_drift(repo, Path.cwd())
        if drift:
            parts += [f"**{drift}**", ""]
    except Exception as exc:
        parts += [f"_(worktree drift check failed: {exc})_", ""]
    try:
        # Work waiting on a sibling repository becomes ready the moment its dependency
        # is observed done, and session start is when an agent decides what to take.
        from .operations import external_sync

        ext = external_sync(repo, agent=agent)
        changed = [o for o in ext.data.get("observed", []) if o["changed"]]
        if changed:
            parts += [
                "Observed in sibling repositories: "
                + ", ".join(f"{o['dep']} is {o['state']}" for o in changed),
                "",
            ]
        if ext.exit == O.FAIL:
            parts += [f"_(external dependencies not observed: {ext.reason})_", ""]
    except Exception as exc:
        parts += [f"_(external sync failed: {exc})_", ""]
    try:
        from .operations import cadence

        due = cadence(repo, agent=agent)
        if due.exit == O.FAIL:
            parts += [f"_(cadence check failed: {due.reason})_", ""]
        elif due.exit == O.OK and due.data.get("due"):
            parts += [
                "Periodic passes DUE: "
                + ", ".join(f"{d['cadence']} (last: {d['since']})" for d in due.data["due"])
                + ". Record each with `ddflow cadence --ran <name>` when done.",
                "",
            ]
    except Exception as exc:
        parts += [f"_(cadence check failed: {exc})_", ""]
    try:
        out = brief(repo, check_recovery=True, agent=agent)
        parts.append(out.data.get("text", "") or out.reason)
    except Exception as exc:
        parts.append(f"ddflow could not build the brief ({exc}). Run `ddflow doctor`.")
    parts += [
        "",
        "_Use the ddflow MCP tools for the queue. A subagent passes `as_agent` on every "
        "call; `ddflow_memory_add` records a fact about this machine for the next session._",
    ]
    return O.ok("hooks", message="\n".join(parts), installed=True, policy="")


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


def _prompt_hook_line(repo: Path) -> str:
    from ..services import claudehooks as CH

    parts = []
    for name, kw in (("Claude Code", _prompt_kw(CH, False)), ("Gemini CLI", _prompt_kw(CH, True))):
        on, why = CH.state(repo, **kw)
        parts.append(
            f"{name}: "
            + ("installed" if on else f"UNKNOWN -- {why}" if on is None else "not installed")
        )
    return "; ".join(parts)


def _prompt_kw(CH, gemini: bool) -> dict[str, Any]:
    return {
        "event": CH.GEMINI_PROMPT_EVENT if gemini else CH.PROMPT_EVENT,
        "marker": CH.PROMPT_MARKER,
        "rel": CH.GEMINI_SETTINGS if gemini else ".claude/settings.json",
    }


def _agent_hooks(repo: Path, action: str, *, claude: bool, gemini: bool) -> list[str]:
    """Install or remove the harness hooks: Claude's SessionStart + prompt, Gemini's prompt."""
    from ..services import claudehooks as CH
    from ..services import enforce as E

    msgs: list[str] = []
    if claude:
        if action == "install":
            line = E.command_line(CH.MARKER)
            msgs.append(CH.install(repo, line))
            if note := E.redirect_note(line):
                msgs.append(note)
        else:
            msgs.append(CH.uninstall(repo))
    for want, kw in ((claude, _prompt_kw(CH, False)), (gemini, _prompt_kw(CH, True))):
        if not want:
            continue
        if action == "install":
            cmd = (
                E.command_line(CH.PROMPT_MARKER)
                + (" --gemini" if kw["rel"] == CH.GEMINI_SETTINGS else "")
                + " || true"
            )
            msgs.append(CH.install(repo, cmd, matcher=None, **kw))
        else:
            msgs.append(CH.uninstall(repo, **kw))
    return msgs


def _capture_prompt(repo: Path, stdin: str, agent: str) -> O.Outcome:
    """What the prompt hook does with the harness's JSON. NEVER fails the user's turn:
    every problem becomes a quiet `skipped` with the reason, and the exit is always 0.

    Redaction happens inside `sessions.capture_prompt`, before the event is built.
    """
    import json

    from ..config import Config
    from ..infra.log import EventLog, resolve_agent_id
    from ..services import sessions as S

    try:
        payload = json.loads(stdin) if stdin.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("the hook's stdin is not a JSON object")
        text = payload.get("prompt")
        if not isinstance(text, str):
            return O.ok("hooks", message="", result="skipped", why="no prompt in the hook JSON")
        cfg = Config.load(repo)
        resolved, _layer = resolve_agent_id(repo, cfg, agent)
        # A short lock wait: the hook must not make the operator's turn wait on a busy log.
        log = EventLog(
            repo, resolved, lock_timeout_s=min(cfg.lease.acquire_timeout_s, 3.0), log_cfg=cfg.log
        )
        sid = str(payload.get("session_id") or payload.get("conversation_id") or "")
        result = S.capture_prompt(
            log, cfg, sid, text, model=str(payload.get("model") or ""), tool="hook"
        )
        return O.ok("hooks", message="", result=result)
    except Exception as exc:  # a hook must not break the prompt it observes
        return O.ok("hooks", message="", result="skipped", why=f"{type(exc).__name__}: {exc}")


def _hooks_status(repo: Path, cfg) -> O.Outcome:
    """What is installed, and whether the installed hook and the policy agree."""
    from ..services import claudehooks as CH
    from ..services import enforce as E

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
    session, unreadable = CH.state(repo)
    if session is None:
        session_line = f"UNKNOWN -- {unreadable}"
    elif session:
        session_line = "installed"
    else:
        session_line = (
            "not installed (`ddflow hooks install --claude` puts the brief in every session)"
        )
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
    message = (
        f"{_hook_line('pre-commit', commit_hook)}\n"
        f"policy [enforce].commit_without_lease = {mode!r}{note}\n"
        f"{trailer_line}\n"
        f"Claude Code SessionStart hook: {session_line}\n"
        f"prompt capture hook: {_prompt_hook_line(repo)}"
    )
    data = {
        "installed": on,
        "policy": mode,
        "session_hook": session,
        "trailer_hook": msg_hook,
        # How each is armed: "ddflow", "pre-commit", "pre-commit legacy", or "".
        "armed_via": {"pre-commit": commit_hook.via, "commit-msg": msg_armed.via},
        "message": message,
    }
    if on or mode == "off":
        return O.ok("hooks", **data)
    return O.nothing("hooks", message, **data)


def hooks(
    repo: Path,
    *,
    action: str = "status",
    force: bool = False,
    claude: bool = False,
    msg_file: str = "",
    agent: str = "",
    gemini: bool = False,
    stdin: str = "",
) -> O.Outcome:
    """The commit hook: install, uninstall, status, or run the check itself.

    `claude=True` installs or removes the Claude Code SessionStart and UserPromptSubmit
    hooks instead (`services.claudehooks`); `gemini=True` the Gemini CLI BeforeAgent
    one. `session-start` is what the first runs; `prompt` records the operator's prompt
    from the hook's JSON on `stdin`.
    """
    from ..services import claudehooks as CH
    from ..services import enforce as E

    if action == "session-start":
        return _session_start(repo, agent)
    if action == "prompt":
        return _capture_prompt(repo, stdin, agent)
    if action == "check-msg":
        # The config alone, not `_load`: this runs on EVERY commit, and folding the whole
        # log up front cost ~120 ms on a 5k-event log whether or not a trailer needed the
        # queue. `queue_ids` reads it only when a trailer names an item, from the index
        # when that is current. Nothing here needs the resolved identity.
        from ..config import Config

        return _check_msg(repo, Config.load(repo), msg_file)
    _log, cfg, _st = _load(repo, agent)
    if (claude or gemini) and action in ("install", "uninstall"):
        try:
            msgs = _agent_hooks(repo, action, claude=claude, gemini=gemini)
        except CH.SettingsError as exc:
            return O.failed("hooks", str(exc), message=str(exc), installed=E.installed(repo))
        return O.ok(
            "hooks",
            message="\n".join(msgs),
            installed=E.installed(repo),
            session_hook=CH.installed(repo),
        )
    if action == "install":
        msg = E.install(repo, force=force)
        if msg.startswith("REFUSED"):
            return O.failed("hooks", msg, message=msg, installed=E.installed(repo))
        return O.ok("hooks", message=msg, installed=E.installed(repo))
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


def prompts(
    repo: Path,
    *,
    action: str = "list",
    name: str = "",
    force: bool = False,
    agent: str = "",
) -> O.Outcome:
    """The prompt library: list it, read one, or eject a copy to edit."""
    from ..services import prompts as P

    _log, cfg, _st = _load(repo, agent)
    overrides = P.overrides_from(cfg)

    from ..services.macros import macro_problems

    # Every configured macro that is NOT loaded, and why -- on the surface an operator
    # reads, not only in doctor (B-macro-clash-silent).
    problems = macro_problems(repo)
    if action == "list":
        rows = [
            {
                "name": t.name,
                "kind": t.kind,
                "source": t.source,
                # "" for an inline `[[macro]]`, which has no file: `str(None)` printed "None".
                "path": str(t.path) if t.path else "",
                "chars": len(t.text),
            }
            for t in P.list_all(repo, overrides)
        ]
        return O.ok(
            "prompts",
            rows=rows,
            action=action,
            text="",
            written=[],
            skipped=[],
            problems=problems,
        )
    if action == "show":
        try:
            return O.ok(
                "prompts",
                rows=[],
                action=action,
                text=P.resolve_any(name, repo, overrides).text,
                written=[],
                skipped=[],
                problems=[p for p in problems if f"macro {name!r}" in p],
            )
        except P.TemplateError as exc:
            return O.failed(
                "prompts", str(exc), rows=[], action=action, text="", written=[], skipped=[]
            )
    if action == "eject":
        # With no name, everything -- BOTH registries, plus any `[[macro]]`. Writing only
        # the templates would mean the documented way to edit a workflow command does not
        # exist.
        names = [name] if name else [*P.TEMPLATE_NAMES, *P.all_commands(repo)]
        out = repo / ".ddflow" / "prompts"
        (out / "commands").mkdir(parents=True, exist_ok=True)
        written, skipped = [], []
        for n in names:
            try:
                tmpl = P.resolve_any(n, None, {})  # the SHIPPED default, not the override
            except P.TemplateError:
                # A MACRO has no shipped default — its body IS the operator's already, so
                # there is nothing to eject and nothing to warn about.
                try:
                    tmpl = P.resolve_any(n, repo, overrides)
                except P.TemplateError as exc:
                    return O.failed("prompts", str(exc), rows=[], action=action, text="")
            # A command must land in `prompts/commands/`, which is where `resolve_command`
            # looks. Writing it beside the templates produces a file the operator edits and
            # the tool never reads -- an override that silently does nothing, which is worse
            # than refusing to eject it.
            dst = (out / "commands" / f"{n}.md") if tmpl.kind == "command" else (out / f"{n}.md")
            if dst.exists() and not force:
                skipped.append(str(dst))
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(tmpl.text, "utf-8")
            written.append(str(dst))
        return O.ok(
            "prompts",
            rows=[{"path": w} for w in written],
            action=action,
            written=written,
            skipped=skipped,
            text="",
        )
    return O.failed(
        "prompts",
        f"unknown action {action!r}; known: list, show, eject",
        rows=[],
        action=action,
        text="",
        written=[],
        skipped=[],
    )


def help_topic(
    repo: Path, *, topic: str = "", tools: dict | None = None, agent: str = ""
) -> O.Outcome:
    """What this is, what it can do, and what the workflow is.

    `tools` is handed IN by the surface: `services/` sits below `surfaces/`, so the help
    renderer may not reach up for the tool registry, and the generated capability inventory
    must come from the LIVE table rather than a hand-kept second copy.
    """
    from ..services import help as H
    from ..services.prompts import TemplateError

    try:
        text = H.render_topic(topic, repo) if topic else H.render_index(repo, tools=tools or {})
    except TemplateError as exc:
        return O.failed("help", str(exc), topic=topic or "index", text="", topics=list(H.TOPICS))
    return O.ok("help", topic=topic or "index", text=text, topics=list(H.TOPICS))


def init_project(repo: Path, *, agent: str = "", called_from: Path | None = None) -> O.Outcome:
    """`ddflow init`: create `.ddflow/` and the repository files that keep its log safe.

    The same `services.adopt.init_files` that `setup` runs as its first step, so `init`,
    `adopt` and `ddflow_setup` cannot disagree about what an initialised project is.
    """
    from ..services.adopt import init_files

    del agent  # identity is not needed to create files; accepted for surface symmetry
    tree = files_tree(repo, called_from)  # committed files: the caller's tree, as `setup`
    actions = init_files(tree)
    d = tree / ".ddflow"
    return O.ok("init", actions=actions, root=str(d), config=str(d / "config.toml"), tree=str(tree))


@dataclass
class Adoption:
    """What `setup` writes, and for whom."""

    agents: str = ""
    docs: str = "docs/ddflow"
    launch: str = ""
    image: str = ""
    #: Rewrite only the driver docs and the rules blocks (`services.adopt.refresh_docs`).
    refresh_docs: bool = False


def files_tree(repo: Path, called_from: Path | None) -> Path:
    """Where `adopt` writes the files meant to be COMMITTED: the caller's own tree.

    `repo` is the primary checkout (`repo_root`), which is right for the shared event log
    and the hooks, and wrong for files that reach the project through the caller's
    branch: from a linked worktree they dirtied the primary and left that branch with
    nothing (bug Bfeb62112d9). A `called_from` outside this repository is not a tree of it.
    Not `W.current`: that answers None for a DETACHED linked worktree (nothing to adopt
    for a claim), which is still the caller's checkout here, and it does not ask whether
    the tree belongs to `repo` at all.
    """
    from ..infra import worktree as W

    if called_from is None:
        return Path(repo)
    top = W.git(called_from, "rev-parse", "--show-toplevel")
    if not top.ok or not top.out:
        return Path(repo)
    try:
        same = W.repo_root(called_from).resolve() == Path(repo).resolve()
    except W.GitError:
        same = False
    return Path(top.out).resolve() if same else Path(repo)


def setup(
    repo: Path,
    plan: Adoption | None = None,
    *,
    agent: str = "",
    called_from: Path | None = None,
) -> O.Outcome:
    """Adopt ddflow into a project: drivers, the agent rules sections, the hook.

    The files are written into the tree `called_from` stands in (`files_tree`): a linked
    worktree's own checkout, committed through its branch, never the shared primary.
    Without `called_from` they go to `repo`.

    Names the companion gap at adoption time. A project that adopts ddflow and stops has a
    `standards` gate with nothing behind it and a `rules` gate reading no memory — and
    because an agent gate passes on an assertion, that gap is invisible in exactly the way
    this design exists to prevent. Detection only; nothing is installed, because fetching
    and running code on someone's machine is not a thing a work-queue tool gets to do.
    """
    from ..services.adopt import AGENT_TARGETS, adopt, adopted_agents, refresh_docs

    plan = plan or Adoption()
    _log, cfg, _st = _load(repo, agent)
    agents = csv_list(plan.agents) or list(AGENT_TARGETS)
    tree = files_tree(repo, called_from)
    if plan.refresh_docs:
        # Documents only: no init files, hooks, MCP launches or command files, and no
        # companion scan. Into the caller's tree like every committed file.
        try:
            actions = refresh_docs(tree, docs_dir=plan.docs)
        except ValueError as exc:
            return O.failed("setup", str(exc), actions=[], agents=[], text="")
        out = O.ok(
            "setup",
            actions=actions,
            agents=adopted_agents(tree, docs_dir=plan.docs),
            tree=str(tree),
            refresh_docs=True,
            companions_ready=[],
            companions_absent=[],
            text="",
        )
        from ..views import human as _human

        out.data["text"] = _human.render(out)
        return out
    try:
        actions = adopt(
            tree,
            agents,
            docs_dir=plan.docs,
            install_hooks=cfg.enforce.install_hooks_on_setup,
            launch=plan.launch,
            image=plan.image,
        )
    except ValueError as exc:
        return O.failed("setup", str(exc), actions=[], agents=agents, text="")

    statuses = _scan(repo)
    if isinstance(statuses, O.Outcome):
        return statuses
    ready, absent = [], []
    for st in statuses:
        if not st.is_gap:
            continue
        (ready if st.state == "installed" else absent).append(st.companion.id)
    from ..services.adopt import Refused
    from ..views import human

    data = {
        "actions": actions,
        "agents": agents,
        "tree": str(tree),
        "companions_ready": ready,
        "companions_absent": absent,
        "text": "",
    }
    refused = [a for a in actions if isinstance(a, Refused)]
    if refused:
        # Everything else was still written, so the actions are reported in full; but a
        # step the operator must finish by hand is not "adopted", and the exit code is
        # what a script or an agent reads.
        out = O.failed("setup", "; ".join(refused), **data)
    else:
        out = O.ok("setup", **data)
    out.data["text"] = human.render(out)
    return out
