"""What the MCP engine (`surfaces/mcp.py`) reads from the domain layers.

The engine is a JSON-RPC router and must not know where a companion registry, a prompt
template or the obligations footer comes from: it asks here. Two kinds of thing live in
this module and neither decides anything:

* one-line forwards (`prompt_commands`, `render_prompt`, `obligation_footer`, ...);
* the handshake's READS: each `fill_*` adds the facts one block of `mcp_instructions.md`
  renders from into the dict the engine seeded with a default for every variable. They
  write into that dict as they go, so a failure part-way keeps what the block had already
  found -- the engine decides what a failure costs (nothing: a handshake that cannot
  answer is worse than one with a missing section).

Services are reached as modules (`AD.rules_status`), not by name: a test that patches the
service must reach the call, and a name bound at import would not see it.

Imported by the engine on first use, never at module level: this module reaches the
template engine (jinja2), and `scripts/bump.sh` imports `ddflow.surfaces.mcp` under an
interpreter that has none.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import progress as PR
from ..core.model import State, fold
from ..infra.log import EventLog
from ..services import adopt as AD
from ..services import companions as CO
from ..services import gates as GA
from ..services import importer as IM
from ..services import leases as L
from ..services import obligations as OB
from ..services import prompts as P
from ..services import review as RV
from ..services.macros import MacroError  # noqa: F401  (re-exported for the engine)
from . import identity
from .lifecycle import plan_for

TemplateError = P.TemplateError


def prompt_commands(repo: Path) -> dict[str, tuple[str, str, list[str]]]:
    """Every workflow the `prompts/list` method offers: shipped and operator-defined."""
    return P.all_commands(repo)


def prompt_not_loaded_note(repo: Path) -> str:
    return P.not_loaded_note(repo)


def render_prompt(name: str, repo: Path, args: dict[str, Any]) -> str:
    """The one rendering `ddflow prompts get` and the `ddflow_prompts` tool share."""
    return P.render_command(name, repo, args)


def render_instructions(repo: Path, overrides: dict[str, str], variables: dict[str, Any]) -> str:
    """`mcp_instructions` resolved against the project's overrides and rendered."""
    return P.render(P.resolve("mcp_instructions", repo, overrides), **variables).strip()


def instructions_override(repo: Path) -> str:
    """The `[prompts].mcp_instructions` path, read by NAME: the dead-knob ratchet greps for
    the knob being read, and a bulk dict pass is how a knob gets renamed in config and
    silently stops working."""
    return Config.load(repo).prompts.mcp_instructions


def obligation_footer(repo: Path, cfg: Config) -> str:
    """The re-instruction footer text for the current log, or ""."""
    state = fold(EventLog(repo, log_cfg=cfg.log).read_all(), strict=False)
    return OB.footer(state, cfg, repo=repo, limit=cfg.reinstruct.max_items)


def importable_count(repo: Path) -> int:
    """How many journal files the CONFIGURED sources hold: a project that moved its journal
    to a path the defaults do not know was told "nothing to import" about its whole
    history. `glob` on a handful of known paths, no parsing."""
    return len(IM._files(repo, IM.all_source_globs(Config.load(repo))))


def fill_rules_drift(v: dict[str, Any], repo: Path, cfg: Config, agent: str) -> None:
    """Whether the project's rules surface is present, stripped, drifted or gone: two
    `read_text` calls, and the one fact that decides if the agent has any rules at all."""
    v["rules_drift"] = [
        {"path": r.path, "state": r.state, "detail": r.render()}
        for r in AD.rules_status(repo)
        if r.needs_attention
    ]


def fill_unit_test_todo(v: dict[str, Any], repo: Path, cfg: Config, agent: str) -> None:
    ut = GA.load_gates(repo, cfg).get("unit_tests")
    if not ut or not ut.command or "set [gate.unit_tests]" in ut.command:
        v["setup_todo"].append(
            "No test command is configured. Set it with `ddflow_configure`: "
            '`[gate.unit_tests]` / `command = "<your test command>"`. Until then '
            "the unit_tests gate reports UNAVAILABLE and cannot pass."
        )


def fill_reviewer_todo(v: dict[str, Any], repo: Path, cfg: Config, agent: str) -> None:
    if not RV.load_reviewers(repo):
        v["setup_todo"].append(
            "No cross-family reviewer is configured, so the `critic` gate cannot "
            "run and `ddflow_complete` will refuse. Call "
            "`ddflow_reviewers_detect` with write=true — it finds a local model "
            "server if one is running and records it in the git-ignored "
            ".ddflow/local/reviewers.toml, never the committed config."
        )


def _companion_row(st: Any) -> dict[str, Any]:
    return {
        "id": st.companion.id,
        "title": st.companion.title,
        "gates": list(st.companion.gates),
        # Pre-joined, because `trim_blocks` eats the newline after a block tag:
        # a nested `{% for %}` closed at the end of a content line takes that
        # line's newline with it, and every bullet lands on one line. Both
        # renderers agree on that, so it is the template's shape to avoid, not
        # an engine difference to work around.
        "gates_text": ", ".join(st.companion.gates) or "—",
        "state": st.state,
        "install": st.companion.install,
        "url": st.companion.url,
        "default": st.companion.default,
        # The CLI JSON payload carries this; omitting it here meant the
        # template could not tell a server from a command-line tool even if it
        # wanted to -- the same CLI/MCP divergence, inside the fix for it.
        "kind": st.companion.kind,
        "usable": st.usable,
        "is_gap": st.is_gap,
        "is_unknown": st.is_unknown,
        "advice": st.advice,
    }


def fill_companions(v: dict[str, Any], repo: Path, cfg: Config, agent: str) -> None:
    """The companion registry as the template renders it, bucketed by what the agent would
    have to DO about each.

    NOT a silent catch. A malformed registry must not stop the handshake, but swallowing
    it deleted the whole companions and gate-gap section, so "nobody could look" rendered
    as "no gaps" -- the unavailable-as-success class, inside the report whose purpose is
    to expose it. The failure becomes a `setup_todo` line.
    """
    try:
        # `probe=False`: detection shells out, and the handshake is the one call an
        # agent waits on before it can do anything at all. Registration state is read
        # from config files and is free; whether the binary exists can wait for
        # `ddflow_companions`, which is what the instruction tells it to call.
        statuses = CO.scan(repo, probe=False)
        v["companions"] = [_companion_row(st) for st in statuses]
        # Split by what the AGENT would have to DO about each, because the two need
        # different permission from the operator: wiring up a server that is already
        # on the machine is a config edit, while installing one runs an install
        # command. `is_gap`, not `state != "registered"`: an installed `cli` companion
        # can never be "registered". Bucketed by ADVICE, not by state: with
        # `probe=False` an mcp companion is definitely not registered and its install
        # state is unknown, which is neither "one command away" nor "go install it".
        by = {
            a: [c for c in v["companions"] if c["advice"] == a and c["default"]]
            for a in ("register", "install", "check")
        }
        v["unregistered_companions"] = by["register"]
        v["uninstalled_companions"] = by["install"]
        v["unchecked_companions"] = by["check"]
        v["missing_companions"] = by["register"] + by["install"]
        # ONE list for the template, because the proposal an agent makes is the same in
        # all three cases -- tell the operator, give them the command, let them decide.
        # Only the CLAIM about install state differs, and that is what `state_word`
        # carries.
        by_id = {c["id"]: c for c in v["companions"]}
        v["actionable_companions"] = [
            {**by_id[st.companion.id], "state_word": word}
            for st, word in CO.actionable(statuses, grouped=True)
        ]
        cover = CO.gate_coverage(repo, statuses, CO.coverage_gates(cfg))
        v["gate_gaps"] = CO.gate_gaps(cover, statuses)
    except Exception as exc:
        v["setup_todo"].append(
            f"The companion registry could not be read, so this handshake says nothing "
            f"about which gates have a tool behind them: {exc}. Fix "
            f".ddflow/companions.toml (or `ddflow companions`, which prints the same "
            f"error) — until then, treat every gate as unserved rather than served."
        )


def _recoverable(log: EventLog, cfg: Config, repo: Path) -> int:
    """Trees that may hold work: one per tree (path compared after `normpath`), not per
    item, and never a tree measured clean (B904edd649c). One that could not be measured
    (None) counts: `recover` says to treat it as containing work until someone has looked.
    A record with no tree is not a tree and is not counted."""
    return len(
        {
            os.path.normpath(r.worktree)
            for r in L.scan(log, cfg, repo)
            if r.worktree and r.salvageable is not False
        }
    )


def fill_queue(v: dict[str, Any], repo: Path, cfg: Config, agent: str) -> None:
    """The counts the template opens with: what is ready, running, blocked, looping."""
    # `identity.resolve`, not `cfg.agent.id or ""` -- the latter falls to the
    # tree-derived default and reads neither DDFLOW_AGENT nor a declared name. The
    # identity here decides which items `plan()` counts as "already mine", so with
    # DDFLOW_AGENT set the handshake reported the connection's OWN claimed work as
    # someone else's, at the one moment the agent is told what to do next.
    log = EventLog(repo, identity.resolve(repo, cfg, agent).id, log_cfg=cfg.log)
    events = log.read_all()
    st: State = fold(events, strict=False)
    # The ready set as `next` offers it (the waiters' reservation hold and the
    # parallelism limit), not the bare scheduler's: the handshake is where an agent is
    # told what to do next, so it must not promise an item `next` withholds.
    p = plan_for(repo, log, cfg, st, purpose="view", agent=log.agent_id, events=events)
    v["ready"], v["running"] = len(p.ready), len(p.running)
    v["queue_is_empty"] = not st.items
    # `rescan=False`: the queue-only half, which costs nothing because `st` is already
    # folded. The source re-scan is ~0.65 s on a real corpus -- not something to spend at
    # every session start. The handshake TELLS the agent to run the full check, and only
    # when the cheap half has already found something to act on.
    iv = IM.verify_import(repo, st, rescan=False)
    v["imported_total"] = iv.total
    v["imported_no_globs"] = len(iv.no_globs)
    v["imported_shipped_drift"] = len(iv.shipped_drift)
    v["blocked"] = len(p.blocked)
    v["open_bugs"] = sum(1 for b in st.bugs.values() if b.open)
    v["loops"] = len(PR.detect(events, st, cfg))
    v["recoverable"] = _recoverable(log, cfg, repo)
