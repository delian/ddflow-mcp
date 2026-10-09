"""Which documents are selected, and who selected them (D-export-selection,
D-export-agent-enable).

The selection itself is ``[export].documents`` in the config (plus ``[export.<doc>]`` for a
path or mode); enabling or disabling edits it through the same guarded write as
``config --set``. This module adds the RECORD: ``export.enabled`` / ``export.disabled`` /
``export.acknowledged`` events saying who did it and when, so that

- an agent may enable a document without approval, but never silently: the result names the
  agent and the command that stops it, ``brief`` carries a line and ``doctor`` a note until
  the operator acknowledges it (``ddflow export ack``, or ``ddflow export`` at a terminal);
- the operator may VETO with ``disable --lock``: an agent's later enable is refused (exit 3);
  only a person at a terminal enables a locked document again.

An enable creates no file: the first write is the next export run, with every hand-edit
protection of D-export. Nothing here prints; ``api.export`` wraps these into Outcomes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...config import Config
from ...core.model import State
from . import registry as R
from . import write as W
from .query import EXIT_REFUSED, EXIT_UNAVAILABLE, ExportError

#: What an agent's enable tells the operator, verbatim from the decision.
STOP = "ddflow export disable {doc}"


def stop_command(doc: str) -> str:
    return STOP.format(doc=doc)


def _is_agent(requested_agent: str, via_mcp: bool) -> str:
    """Why this call is an agent's, or "" for a person at a terminal. The MCP surface is
    always an agent; a CLI call is one under --agent, DDFLOW_AGENT or a harness's marker
    (the rule ``reviewers approve`` uses)."""
    from ..identity import is_agent

    return is_agent(requested_agent, via_mcp)


def _locked(st: State, doc: str) -> dict[str, Any] | None:
    e = st.exports.get(doc)
    return e if e and e.get("locked") and not e.get("enabled") else None


def _write_selection(
    repo: Path, pairs: list[tuple[str, object]], *, local: bool, agent: str
) -> list[tuple[str, object]]:
    from ..configwrite import SetPairs, apply_edit

    res = apply_edit(repo, SetPairs(pairs), layer="local" if local else "file", agent=agent)
    if res.error:
        raise ExportError(f"could not edit the selection: {res.error}", EXIT_UNAVAILABLE)
    return pairs


def enable(
    repo: Path,
    log: Any,
    cfg: Config,
    st: State,
    doc: str,
    *,
    path: str = "",
    mode: str = "",
    local: bool = False,
    requested_agent: str = "",
    via_mcp: bool = False,
) -> dict[str, Any]:
    """Select ``doc``. Returns what happened; raises ``ExportError`` (exit 3) when refused.

    Idempotent: enabling a selected document with the settings it already has changes
    nothing and records nothing. An agent's enable of a locked document is refused.
    """
    from . import ops

    R.get(doc)  # unknown kind: refused, listing the kinds
    why_agent = _is_agent(requested_agent, via_mcp)
    lock = _locked(st, doc)
    if lock and why_agent:
        raise ExportError(
            f"{doc} is locked by the operator ({lock.get('by') or '?'}, {str(lock.get('at', ''))[:19]}): "
            f"an agent cannot enable it. Only the operator, at a terminal, enables it again "
            f"with `ddflow export enable {doc}`",
            EXIT_REFUSED,
        )
    spec = ops.spec_for(cfg, doc, path=path, mode=mode, writing=True)  # mode/kind checks
    try:
        W.safe_target(repo, spec.path)
    except ExportError as exc:
        raise ExportError(str(exc), EXIT_REFUSED) from exc
    current = ops.spec_for(cfg, doc, writing=True)
    selected = list(ops.selection(cfg))
    unchanged = (
        doc in selected and spec.path == current.path and spec.mode == current.mode and not lock
    )
    result: dict[str, Any] = {
        "doc": doc,
        "path": spec.path,
        "mode": spec.mode,
        "local": local,
        "by": log.agent_id,
        "by_agent": bool(why_agent),
        "stop": stop_command(doc),
        "changed": not unchanged,
        "unlocked": bool(lock),
        "warnings": [],
    }
    if unchanged:
        result["message"] = f"{doc} is already enabled -> {spec.path}"
        return result
    pairs: list[tuple[str, object]] = []
    if doc not in selected:
        pairs.append(("export.documents", [*selected, doc]))
    if path and path != current.path:
        pairs.append((f"export.{doc}.path", path))
    if mode and mode != current.mode:
        pairs.append((f"export.{doc}.mode", mode))
    if pairs:
        _write_selection(repo, pairs, local=local, agent=requested_agent)
    if spec.mode == R.APPEND:  # an append target is union-merged by git (D-export (2))
        W.register_append_only(repo, spec.path)
    now = Config.load(repo)
    if doc not in ops.selection(now):
        result["warnings"].append(
            f"{doc} is not in the effective selection: another config layer sets "
            f"[export].documents (try {'without' if local else 'with'} --local)"
        )
    log.append(
        "export.enabled",
        doc,
        {
            "document": doc,
            "path": spec.path,
            "mode": spec.mode,
            "by": log.agent_id,
            "human": not why_agent,
            "local": local,
            "locked": False,
        },
    )
    msg = f"enabled {doc} -> {spec.path}"
    if why_agent:
        msg += f" (by {log.agent_id}); the operator can stop it with {stop_command(doc)}"
    if lock:
        msg += " (the operator's lock is lifted)"
    result["message"] = msg
    return result


def disable(
    repo: Path,
    log: Any,
    cfg: Config,
    st: State,
    doc: str,
    *,
    lock: bool = False,
    local: bool = False,
    requested_agent: str = "",
    via_mcp: bool = False,
) -> dict[str, Any]:
    """Stop selecting ``doc`` (the file, if any, stays). ``lock`` is the operator's veto."""
    from . import ops

    R.get(doc)
    why_agent = _is_agent(requested_agent, via_mcp)
    if lock and why_agent:
        raise ExportError(
            f"locking a document is the operator's veto, and this call is an agent's "
            f"({why_agent}). Run `ddflow export disable {doc} --lock` from your own terminal",
            EXIT_REFUSED,
        )
    selected = list(ops.selection(cfg))
    was = doc in selected
    if was:
        _write_selection(
            repo,
            [("export.documents", [d for d in selected if d != doc])],
            local=local,
            agent=requested_agent,
        )
    warnings = []
    if doc in ops.selection(Config.load(repo)):
        warnings.append(
            f"{doc} is still selected: another config layer sets [export].documents "
            f"(try {'without' if local else 'with'} --local)"
        )
    already_locked = bool((st.exports.get(doc) or {}).get("locked"))
    if was or (lock and not already_locked):
        log.append(
            "export.disabled",
            doc,
            {
                "document": doc,
                "by": log.agent_id,
                "human": not why_agent,
                "local": local,
                "locked": lock,
            },
        )
    msg = f"disabled {doc}" if was else f"{doc} is not selected"
    if lock:
        msg += "; locked: an agent cannot enable it again"
    return {
        "doc": doc,
        "changed": was or lock,
        "locked": lock or already_locked,
        "by": log.agent_id,
        "local": local,
        "warnings": warnings,
        "message": msg,
    }


# -- who enabled what, and the acknowledgement -------------------------------------------


def unacknowledged(st: State, cfg: Config) -> list[dict[str, Any]]:
    """Selected documents an AGENT enabled that the operator has not acknowledged yet."""
    out = []
    for doc in cfg.export.documents:
        e = st.exports.get(doc)
        if e and e.get("enabled") and not e.get("human") and not e.get("acked"):
            out.append({"doc": doc, "by": e.get("by", ""), "at": e.get("at", "")})
    return sorted(out, key=lambda r: (r["at"], r["doc"]))


def acknowledge(
    log: Any, st: State, cfg: Config, *, requested_agent: str = "", via_mcp: bool = False
) -> list[str]:
    """Record that a person has seen every agent-enabled document. Returns their names.

    Refused under an agent identity: an agent that could acknowledge its own enable would
    remove the one line that tells the operator."""
    if why := _is_agent(requested_agent, via_mcp):
        raise ExportError(
            f"acknowledging is the operator's: this call is an agent's ({why}). Run "
            "`ddflow export ack` from your own terminal",
            EXIT_REFUSED,
        )
    docs = [r["doc"] for r in unacknowledged(st, cfg)]
    if docs:
        import getpass

        try:
            user = getpass.getuser()
        except Exception:
            user = ""
        log.append(
            "export.acknowledged",
            "export",
            {"documents": docs, "user": user, "by": log.agent_id, "human": True},
        )
    return docs


def annotate(rows: list[dict[str, Any]], st: State) -> None:
    """Add who enabled each row's document and when, and the operator's lock, to the listing."""
    for row in rows:
        e = st.exports.get(row["doc"]) or {}
        on = bool(e.get("enabled")) and bool(row.get("selected"))
        row["enabled_by"] = e.get("by", "") if on else ""
        row["enabled_at"] = e.get("at", "") if on else ""
        row["by_agent"] = bool(on and not e.get("human"))
        row["acknowledged"] = bool(e.get("acked")) if on else True
        row["locked"] = bool(e.get("locked")) and not e.get("enabled")


def brief_line(st: State, cfg: Config) -> str:
    """One line for ``brief`` while an agent-enabled document is unacknowledged, else ""."""
    rows = unacknowledged(st, cfg)
    if not rows:
        return ""
    who = ", ".join(f"{r['doc']} (by {r['by'] or '?'})" for r in rows)
    first = rows[0]["doc"]
    return (
        f"Export: an agent enabled {who}; not yet acknowledged. The operator acknowledges with "
        f"`ddflow export` at a terminal or `ddflow export ack`, and stops it with "
        f"`{stop_command(first)}`.\n"
    )


def doctor_notes(repo: Path, cfg: Config, st: State) -> list[str]:
    """Notes for ``doctor``: agent-enabled documents not yet acknowledged, and ejected
    templates older than the shipped default."""
    from . import templates as T

    notes = [
        f"export: {r['doc']} was enabled by {r['by'] or '?'} at {str(r['at'])[:19]} and is not "
        f"acknowledged (`ddflow export ack`; stop it with `{stop_command(r['doc'])}`)"
        for r in unacknowledged(st, cfg)
    ]
    notes += T.drift_notes(repo)
    return notes
