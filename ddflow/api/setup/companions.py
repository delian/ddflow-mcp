"""Companion registry: scan, verify and register MCP servers.

The registry is operator-facing one-shot work. Registering a companion that is not
installed writes a launch command that fails mid-task, and "not installed" is kept
distinct from "could not tell" so an operator whose probe merely timed out is not sent to
install something they already have.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import csv_list
from ...core import outcome as O
from ...services import companions as CO
from ...services import harnessreg as HR
from ...services.configwrite import bare_id_problem
from .._base import _load


def _scan(repo: Path, *, probe: bool = True):
    """Statuses, or an Outcome explaining why not. ONE guard, used by every caller.

    The registry loader writes a careful sentence naming the file, the id and the bad
    value; letting its `ValueError` escape renders that sentence as an uncaught exception
    and makes the exit code an accident rather than a contract.
    """

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

    statuses = _scan(repo, probe=not no_probe)
    if isinstance(statuses, O.Outcome):
        return statuses
    _log, cfg, _st = _load(repo, agent)
    cover = CO.gate_coverage(repo, statuses, CO.coverage_gates(cfg))
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
        "uncovered_gates": CO.gate_gaps(cover, statuses),
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


def _verify_skipped(statuses: list[Any], chosen_ids: set[str], wanted: list[str]) -> list[dict]:
    """Why each companion a verify run did not launch was left alone."""
    return [
        {
            "id": st.companion.id,
            "reason": "not an MCP server entry: nothing to launch"
            if not st.companion.is_mcp
            else (
                "install state unknown (the probe could not tell); "
                if st.installed is None
                else "not registered with an agent and not detected as installed; "
            )
            + "name it with --id to launch it anyway",
        }
        for st in statuses
        if st.companion.id not in chosen_ids and (st.companion.is_mcp or st.companion.id in wanted)
    ]


def _verify_outcome(rows: list[dict], skipped: list[dict]) -> O.Outcome:
    """Exit code for a verify run: a fact beats a doubt beats nothing launched."""
    data: dict[str, Any] = {"verified": rows, "skipped": skipped}
    bad = [r["id"] for r in rows if r["state"] == "not_mcp"]
    if bad:
        return O.failed("companions.verified", f"not an MCP server: {', '.join(bad)}", **data)
    if not rows:
        return O.nothing("companions.verified", "no MCP companion to launch", **data)
    unknown = [r["id"] for r in rows if r["state"] == "unknown"]
    if unknown:
        return O.nothing("companions.verified", f"could not tell: {', '.join(unknown)}", **data)
    return O.ok("companions.verified", **data)


def companions_verify(repo: Path, ids: str = "", *, agent: str = "") -> O.Outcome:
    """Launch each MCP companion and require an answer to a JSON-RPC `initialize`.

    OPT-IN and never on the scan path: it spawns the servers' launch commands, and
    `ddflow companions` is what the MCP handshake calls. Without `ids` it launches those
    that are registered in an agent's config or detected as installed (an `npx` entry
    that is merely in the registry would be DOWNLOADED to be tested); with `ids` it
    launches exactly those, installed or not, because the point of asking is to find out.

    Exit 0 when every launched server answered; 1 when one is not an MCP server (a
    FACT: absent, exited, or answered something that is not a JSON-RPC response); 2 when
    nothing could be launched or one could not be told (no answer in time).
    """

    statuses = _scan(repo, probe=True)
    if isinstance(statuses, O.Outcome):
        return statuses
    by_id = {st.companion.id: st for st in statuses}
    wanted = csv_list(ids)
    unknown_ids = [w for w in wanted if w not in by_id]
    if unknown_ids:
        return O.failed(
            "companions.verified",
            f"unknown companion id(s): {', '.join(unknown_ids)}",
            verified=[],
            skipped=[],
        )
    mcp = [st for st in statuses if st.companion.is_mcp]
    chosen = (
        [st for st in mcp if st.companion.id in wanted]
        if wanted
        else [st for st in mcp if st.registered_in or st.installed]
    )
    chosen_ids = {st.companion.id for st in chosen}
    skipped = _verify_skipped(statuses, chosen_ids, wanted)
    results = CO.verify(repo, sorted(chosen_ids))
    state_of = {True: "speaks_mcp", False: "not_mcp", None: "unknown"}
    rows = [
        {
            "id": v.companion.id,
            "state": state_of[v.speaks_mcp],
            "detail": v.detail,
            "server": v.server,
            "elapsed_s": v.elapsed_s,
            "command": " ".join([v.companion.command, *v.companion.args]),
        }
        for v in results
    ]
    return _verify_outcome(rows, skipped)


@dataclass
class Registration:
    """What to register, for whom. Named because five fields travel together through the
    argparse flags, the MCP input schema and `CO.register`."""

    ids: str = ""
    agents: str = ""
    force: bool = False
    dry_run: bool = False


def _add_stopped(make: Any, reason: str, refused: list[str] | None = None) -> O.Outcome:
    """The `companions.added` outcome for a request that wrote nothing."""
    return make(
        "companions.added", reason, actions=[], applied=False, written=0, refused=refused or []
    )


def _add_install_state(wanted: list[str], by_id: dict[str, Any], force: bool) -> O.Outcome | None:
    """`is False` and `is None` are different refusals. Both block -- registering a launch
    command that fails mid-task is the thing to avoid either way -- but "not installed
    here" sent to an operator whose probe merely TIMED OUT makes them install something
    they already have, and the install line then does nothing. Say which one it is."""
    absent = [w for w in wanted if by_id[w].installed is False and not force]
    untested = [w for w in wanted if by_id[w].installed is None and not force]
    if not (absent or untested):
        return None
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
    return _add_stopped(O.refused, "\n".join(parts), absent + untested)


def _add_refusal(statuses: list[Any], reg: Registration) -> tuple[list[str], O.Outcome | None]:
    """The ids to register, or the outcome that says why none can be."""
    by_id = {st.companion.id: st for st in statuses}
    wanted = csv_list(reg.ids) or [
        st.companion.id
        for st in statuses
        if st.companion.default and st.installed and st.companion.is_mcp  # servers only
    ]
    unknown_ids = [w for w in wanted if w not in by_id]
    if unknown_ids:
        return wanted, _add_stopped(
            O.failed,
            f"unknown companion(s): {', '.join(unknown_ids)}; known: {', '.join(by_id)}",
        )
    # The id is the server's key in an agent's config: `a.b` wrote [mcp_servers.a.b], a
    # nested table, not a server (D-plain-keys, B7a1ed66cb9).
    if bad := [
        p
        for w in wanted
        if (p := bare_id_problem(w, "a companion id", "the agent's [mcp_servers.<id>] entry"))
    ]:
        return wanted, _add_stopped(
            O.refused, "; ".join(bad), [w for w in wanted if bare_id_problem(w, "", "")]
        )
    not_servers = [w for w in wanted if not by_id[w].companion.is_mcp]
    if not_servers:
        return wanted, _add_stopped(
            O.refused,
            "not an MCP server: "
            + "; ".join(
                f"{w} is a {by_id[w].companion.kind} tool ({by_id[w].companion.install})"
                for w in not_servers
            )
            + ". There is no MCP config entry to write. Install it and ddflow detects it; "
            "`ddflow companions` shows it either way.",
            not_servers,
        )
    if not wanted:
        return wanted, _add_stopped(
            O.nothing,
            "nothing to add: no default MCP companion is installed on this machine. "
            "`ddflow companions` lists them with their install commands.",
        )
    return wanted, _add_install_state(wanted, by_id, reg.force)


def companions_add(repo: Path, reg: Registration | None = None, *, agent: str = "") -> O.Outcome:
    """Register companion servers with an agent's MCP config.

    Four refusals, and the distinction between the last two is the one that matters — see
    the module docstring.
    """

    reg = reg or Registration()
    statuses = _scan(repo, probe=True)
    if isinstance(statuses, O.Outcome):
        return statuses
    wanted, stopped = _add_refusal(statuses, reg)
    if stopped is not None:
        return stopped
    by_id = {st.companion.id: st for st in statuses}
    agents = csv_list(reg.agents) or [HR.default_id()]
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
