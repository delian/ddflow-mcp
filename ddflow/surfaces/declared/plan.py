"""The `plan check` command, declared once. CLI-ONLY, on purpose.

`tool=""` with a `reason` is the parity exemption as a field: this is a pre-filing
convenience the coordinator runs on a document, and its output is a list to read, not
something an agent calls mid-task. Not being an MCP tool also means no tools/list bytes,
no tool schema, and no tier entry for it.
"""

from __future__ import annotations

from ..registry import CliPolicy, Command, Param
from ..tools._common import _api


def _text(out, a) -> str:
    """The findings (what to fix), then the waves (what could run together).

    A REFUSAL (a document that is not a plan) carries no findings, so its reason is the
    whole answer -- and it is printed, because the policy shows every exit.
    """
    d = out.data
    if "clean" not in d:
        return out.reason
    if d["clean"]:
        lines = [f"{d['source']}: {d['items']} item(s), no findings"]
    else:
        lines = [f"{d['source']}: {len(d['findings'])} finding(s)"]
        lines += [f"  {f['kind']}  {' '.join(f['ids'])}: {f['detail']}" for f in d["findings"]]
    lines += [f"  warning  {w['id']}: {w['detail']}" for w in d.get("warnings", [])]
    if d["waves"]:
        lines.append("waves:")
        lines += [f"  wave {w}: {iid}" for iid, w in d["waves"].items()]
    return "\n".join(lines)


def _apply_text(out, a) -> str:
    """What `plan apply` did or would do; the findings of a refused plan; else the reason."""
    d = out.data
    lines = [out.reason] if out.reason else []
    lines += [f"  {f['kind']}  {' '.join(f['ids'])}: {f['detail']}" for f in d.get("findings", [])]
    if d.get("dry_run"):
        lines.append(f"plan {d['plan']} (hash {d['hash']}): dry run, nothing filed")
        lines += [f"  would file {i} (wave {d['waves'].get(i, '-')})" for i in d["would_file"]]
        lines.append(f"to file them, PROPOSED: {d['hint']}")
    elif "filed" in d:
        lines.append(f"plan {d['plan']}: filed {len(d['filed'])} item(s), proposed")
        lines += [f"  {i}" for i in d["filed"]]
        lines.append(f"the operator confirms: ddflow plan confirm {d['plan']} --yes --reason ...")
    return "\n".join(lines)


def _confirm_text(out, a) -> str:
    d = out.data
    if "released" in d:
        return f"plan {d['plan']} confirmed: {', '.join(d['released'])} are open"
    return out.reason


#: The findings are the OUTPUT, so they are printed on EVERY exit -- the default prints
#: only a failure's reason on stderr, which would hide what the check exists to say.
_SHOWN = CliPolicy(shown=None)


_SOURCE = Param(
    "source",
    help="The plan file, or - for stdin.",
    cli_help="the plan file, or - for stdin",
    positional=True,
)

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("plan", "check"),
        summary="check a proposed plan before filing it (globs, cycles, collisions)",
        tool="",
        reason=(
            "CLI-only by design: a pre-filing convenience on a document, whose output is a "
            "list to read -- not a tool an agent calls mid-task."
        ),
        call=lambda repo, a, agent: _api().plan_check(repo, a["source"]),
        payload=("source", "clean", "findings", "waves", "items", "warnings"),
        render=_text,
        cli=_SHOWN,
        params=(_SOURCE,),
    ),
    Command(
        path=("plan", "apply"),
        summary="file a checked plan as PROPOSED tasks, in one step (dry run unless --apply)",
        tool="",
        reason=(
            "CLI-only by design: it files a document, and an agent may run only the dry "
            "run -- the operator confirms (D-sdd-plan-modes)."
        ),
        call=lambda repo, a, agent: _api().plan_apply(
            repo,
            a["source"],
            apply=bool(a.get("apply")),
            plan_hash=a.get("hash") or "",
            plan_id=a.get("plan_id") or "",
            agent=agent,
        ),
        payload=("source", "plan", "hash", "items", "dry_run", "would_file", "filed", "waves"),
        render=_apply_text,
        cli=_SHOWN,
        params=(
            _SOURCE,
            Param("apply", type="boolean", help="File the plan (default: a dry run)."),
            Param("hash", help="The hash the dry run printed; a changed plan is refused."),
            Param("plan_id", flag="--plan-id", help="The plan's label (default: from the hash)."),
        ),
    ),
    Command(
        path=("plan", "confirm"),
        summary="the operator confirms a plan: its proposed tasks become ready",
        tool="",
        reason="CLI-only by design: the operator's act (D-sdd-plan-modes), not an agent tool.",
        call=lambda repo, a, agent: _api().plan_confirm(
            repo,
            a["plan_id"],
            yes=bool(a.get("yes")),
            reason=a.get("reason") or "",
            agent=agent,
        ),
        payload=("plan", "released", "waiting"),
        render=_confirm_text,
        cli=_SHOWN,
        params=(
            Param("plan_id", help="The plan's label.", positional=True),
            Param("yes", type="boolean", help="The operator's consent."),
            Param("reason", help="Why the operator accepts the plan."),
        ),
    ),
)
