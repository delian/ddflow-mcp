"""`ddflow harness list | show | doctor`: what each agent gets, and what is broken.

A read-only view over the descriptors (``services/harnessreg``) and what is on disk. It
answers three questions an operator otherwise answers by reading TOML and settings files:

* ``list``   -- every agent, whether this project was adopted for it, and its best rung;
* ``show``   -- for one agent, per canonical event the rung of the fallback ladder in
  effect, and per feature (commands, skills, subagents) what it supports;
* ``doctor`` -- drift between the descriptor and the repo: an unregistered MCP server, a
  missing hook with the command that installs it, a slash-command file that is gone.

The ladder (docs/ddflow/RESEARCH.md R-hx) is: native hook > plugin > MCP first call > MCP
instructions > instruction text. A fact the descriptor leaves out is NOT VERIFIED and is
reported as ``nv``, never as an absence (see ``harnessreg``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..services import adopt as AD
from ..services import claudehooks as CH
from ..services import harnessreg as HR
from ..services.harness import mcp_registered
from ..services.harnessreg import Harness

NATIVE = "native hook"
PLUGIN = "plugin"
FIRST_CALL = "mcp first call"
MCP_TEXT = "mcp instructions"
TEXT = "instruction text"
NONE = "none"
NV = "nv"

#: Best first; `list` reports the best rung any event reaches.
LADDER = (NATIVE, PLUGIN, FIRST_CALL, MCP_TEXT, TEXT, NONE)


def event_level(h: Harness, event: str) -> str:
    """The rung of the fallback ladder that delivers ``event`` to this agent."""
    hk = h.hooks
    if hk is not None and hk.style in HR.COMMAND_HOOK_STYLES and event in hk.events:
        return NATIVE
    if h.plugin is not None and not h.plugin.none:
        return PLUGIN
    if hk is None and h.plugin is None:
        return NV
    if h.mcp.shape != "none":
        return FIRST_CALL if event == "session_start" else MCP_TEXT
    if h.instructions is not None and h.instructions.reads:
        return TEXT
    return NONE


def _feature(sec: Any) -> str:
    if sec is None:
        return NV
    return "no" if sec.none else f"yes ({sec.dir})" if sec.dir else "yes"


def levels(h: Harness) -> dict[str, str]:
    return {e: event_level(h, e) for e in HR.CANONICAL_EVENTS}


def best_level(h: Harness) -> str:
    got = [v for v in levels(h).values() if v in LADDER]
    return min(got, key=LADDER.index) if got else NV


def _row(h: Harness, adopted: set[str]) -> dict[str, Any]:
    return {
        "id": h.id,
        "family": h.family,
        "adopted": h.id in adopted,
        "mcp": h.mcp.shape,
        "hooks": h.hooks.style if h.hooks else NV,
        "best": best_level(h),
    }


def harness_list(repo: Path) -> O.Outcome:
    adopted = set(AD.adopted_agents(repo))
    rows = [_row(h, adopted) for h in HR.load()]
    lines = [
        f"{r['id']:<12} {'adopted' if r['adopted'] else '-':<8} mcp={r['mcp']:<12} "
        f"hooks={r['hooks']:<11} best={r['best']}"
        for r in rows
    ]
    return O.ok("harness_list", harnesses=rows, message="\n".join(lines))


def harness_show(repo: Path, which: str) -> O.Outcome:
    h = HR.get(which)
    if h is None:
        return O.failed("harness_show", f"no harness {which!r}; known: {', '.join(HR.ids())}")
    lv = levels(h)
    feats = {
        "commands": _feature(h.commands),
        "skills": _feature(h.skills),
        "subagents": _feature(h.subagents),
    }
    lines = [f"{h.id} ({h.family}), adopted: {h.id in AD.adopted_agents(repo)}"]
    lines += [f"  {e:<13} {lv[e]}" for e in HR.CANONICAL_EVENTS]
    lines += [f"  {k:<13} {v}" for k, v in feats.items()]
    if h.verified.unverified:
        lines.append("  not verified: " + ", ".join(h.verified.unverified))
    return O.ok("harness_show", id=h.id, levels=lv, features=feats, message="\n".join(lines))


def _hook_findings(repo: Path, h: Harness) -> list[dict[str, str]]:
    if h.hooks is None or h.hooks.style not in HR.COMMAND_HOOK_STYLES:
        return []
    rows = [s for s in CH.HOOKS if s.agent == h.id]
    if not rows:
        return [
            _finding(
                h.id,
                "hooks",
                f"{h.hooks.style} hooks are described but ddflow has no writer for them yet",
                "",
            )
        ]
    out = []
    for spec in rows:
        on, why = CH.state_spec(repo, spec)
        if on is None:
            out.append(_finding(h.id, "hooks", f"{spec.file} cannot be read: {why}", ""))
        elif not on:
            out.append(
                _finding(
                    h.id,
                    "hooks",
                    f"the {spec.name} hook is not in {spec.file}",
                    f"ddflow hooks install --harness {h.id}",
                )
            )
    return out


def _finding(hid: str, area: str, what: str, remedy: str) -> dict[str, str]:
    return {"harness": hid, "area": area, "what": what, "remedy": remedy}


def _findings(repo: Path, h: Harness) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    registered = mcp_registered(repo, h) if h.mcp.path else True
    if registered is None:
        out.append(_finding(h.id, "mcp", f"{h.mcp.path} cannot be read", ""))
    elif not registered:
        out.append(
            _finding(
                h.id,
                "mcp",
                f"no ddflow server in {h.mcp.path}",
                f"ddflow adopt --agents {h.id}",
            )
        )
    for rel in h.commands.files if h.commands else {}:
        if not (Path(repo) / rel).is_file():
            out.append(
                _finding(h.id, "commands", f"{rel} is missing", f"ddflow adopt --agents {h.id}")
            )
    return out + _hook_findings(repo, h)


def harness_doctor(repo: Path, which: str = "") -> O.Outcome:
    """Drift for ``which``, or for every agent this project was adopted for."""
    if which:
        h = HR.get(which)
        if h is None:
            return O.failed("harness_doctor", f"no harness {which!r}; known: {', '.join(HR.ids())}")
        targets = [h]
    else:
        adopted = set(AD.adopted_agents(repo))
        targets = [x for x in HR.load() if x.id in adopted]
    found = [f for h in targets for f in _findings(repo, h)]
    if not found:
        msg = f"{len(targets)} harness(es) checked: nothing is broken"
        return O.ok("harness_doctor", findings=[], message=msg)
    lines = [
        f"{f['harness']}: {f['what']}" + (f"  -> {f['remedy']}" if f["remedy"] else "")
        for f in found
    ]
    return O.Outcome(
        kind="harness_doctor",
        data={"findings": found, "message": "\n".join(lines)},
        exit=O.FAIL,
        reason=f"{len(found)} finding(s)",
    )


def harness_view(repo: Path, *, what: str = "list", id: str = "") -> O.Outcome:
    """The three views behind one entry point (the CLI verb and the MCP action)."""
    if what == "list":
        return harness_list(repo)
    if what == "show":
        return harness_show(repo, id) if id else O.failed("harness", "show needs a harness id")
    if what == "doctor":
        return harness_doctor(repo, id)
    return O.failed("harness", f"unknown view {what!r}: list, show or doctor")
