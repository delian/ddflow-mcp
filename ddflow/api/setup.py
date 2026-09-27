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
    """One config change, or a read. `set`+`value` OR `append_toml`, never both."""

    set: str = ""
    value: str = ""
    append_toml: str = ""
    filter: str = ""
    explain: bool = False


def configure(repo: Path, edit: ConfigEdit | None = None, *, agent: str = "") -> O.Outcome:
    """Read every knob with its source, or change one.

    An append is validated against the MERGED text — see the module docstring for why
    validating what is already on disk checks nothing.
    """
    import tomllib

    from ..config import Config
    from ..infra import tomlcfg as TC
    from ..services.configwrite import _toml_literal, _write_config

    edit = edit or ConfigEdit()
    _log, cfg, _st = _load(repo, agent)

    if edit.set:
        err, _text = _write_config(repo, [(edit.set, edit.value)])
        if err:
            return O.failed("config", err, key=edit.set, value=edit.value, rows=[], text="")
        return O.ok(
            "config",
            key=edit.set,
            value=edit.value,
            literal=_toml_literal(edit.value),
            path=str(repo / ".ddflow" / "config.toml"),
            rows=[],
            text=f"{edit.set} = {_toml_literal(edit.value)}",
        )

    if edit.append_toml:
        # Validated BEFORE writing: an agent composing TOML gets a parse error back as a
        # readable message instead of leaving the project with a config no later command
        # can load.
        try:
            tomllib.loads(edit.append_toml)
        except tomllib.TOMLDecodeError as exc:
            return O.failed("config", f"not valid TOML: {exc}", path="", rows=[], text="")
        path = repo / ".ddflow" / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        prev = path.read_text("utf-8") if path.exists() else ""
        merged = prev.rstrip() + "\n\n" + edit.append_toml.strip() + "\n"
        try:
            Config.check(tomllib.loads(merged))
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            return O.failed(
                "config",
                f"appending this would break the config: {exc}",
                path="",
                rows=[],
                text="",
            )
        TC.atomic_write(path, merged)
        return O.ok("config", path=str(path), appended=True, rows=[], text=f"appended to {path}")

    from ..views import human

    rows = [
        {"key": k, "value": v, "source": s, "doc": d}
        for k, v, s, d in cfg.explain()
        if not edit.filter or edit.filter in k
    ]
    out = O.ok("config", rows=rows, explain=edit.explain, filter=edit.filter, text="")
    out.data["text"] = human.render(out)
    return out


def hooks(repo: Path, *, action: str = "status", force: bool = False, agent: str = "") -> O.Outcome:
    """The commit hook: install, uninstall, status, or run the check itself."""
    from ..services import enforce as E

    _log, cfg, _st = _load(repo, agent)
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
        if code == 0:
            vcode, vmsg = E.check_views(repo, cfg)
            msg = "\n".join(x for x in (msg, vmsg) if x)
            code = vcode
        if code == 0 and cfg.enforce.require_item_trailer:
            tcode, tmsg = E.check_item_trailer(repo)
            msg = "\n".join(x for x in (msg, tmsg) if x)
            code = tcode
        data = {
            "message": msg,
            "installed": E.installed(repo),
            "policy": cfg.enforce.commit_without_lease,
        }
        if code == 0:
            return O.ok("hooks", **data)
        return O.Outcome(kind="hooks", data=data, exit=code, reason=msg)

    on = E.installed(repo)
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
        note = (
            "\n\nNOTE: the policy is 'block' but NO HOOK IS INSTALLED, so nothing enforces "
            "it. Run `ddflow hooks install`."
        )
    message = (
        f"pre-commit hook: {'installed' if on else 'NOT installed'}\n"
        f"policy [enforce].commit_without_lease = {mode!r}{note}"
    )
    data = {"installed": on, "policy": mode, "message": message}
    if on or mode == "off":
        return O.ok("hooks", **data)
    return O.nothing("hooks", message, **data)


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

    if action == "list":
        rows = [
            {
                "name": t.name,
                "kind": t.kind,
                "source": t.source,
                "path": str(t.path),
                "chars": len(t.text),
            }
            for t in P.list_all(repo, overrides)
        ]
        return O.ok("prompts", rows=rows, action=action, text="", written=[], skipped=[])
    if action == "show":
        try:
            return O.ok(
                "prompts",
                rows=[],
                action=action,
                text=P.resolve_any(name, repo, overrides).text,
                written=[],
                skipped=[],
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


@dataclass
class Adoption:
    """What `setup` writes, and for whom."""

    agents: str = ""
    docs: str = "docs/ddflow"
    launch: str = ""
    image: str = ""


def setup(repo: Path, plan: Adoption | None = None, *, agent: str = "") -> O.Outcome:
    """Adopt ddflow into a project: drivers, the agent rules sections, the hook.

    Names the companion gap at adoption time. A project that adopts ddflow and stops has a
    `standards` gate with nothing behind it and a `rules` gate reading no memory — and
    because an agent gate passes on an assertion, that gap is invisible in exactly the way
    this design exists to prevent. Detection only; nothing is installed, because fetching
    and running code on someone's machine is not a thing a work-queue tool gets to do.
    """
    from ..services.adopt import AGENT_TARGETS, adopt

    plan = plan or Adoption()
    _log, cfg, _st = _load(repo, agent)
    agents = csv_list(plan.agents) or list(AGENT_TARGETS)
    try:
        actions = adopt(
            repo,
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
    from ..views import human

    out = O.ok(
        "setup",
        actions=actions,
        agents=agents,
        companions_ready=ready,
        companions_absent=absent,
        text="",
    )
    out.data["text"] = human.render(out)
    return out
