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


#: Finding kinds, in a report's reading order. The counts are the reason the lint exists,
#: so every kind gets its HEADLINE line even when the details are capped below.
_LINT_KINDS = (
    "overlap",
    "no_globs",
    "duplicate",
    "dx_priority",
    "stale_needs",
    "body_ref",
    "stale_ref",
)

#: Detail rows shown per kind; `--json` carries every finding (a whole-queue run counts
#: them in thousands -- a person reads the headline and the first rows, never a list).
_LINT_SHOWN = 60


def _lint_text(out, a) -> str:
    d = out.data
    if "findings" not in d:
        return out.reason
    by: dict[str, list[dict]] = {k: [] for k in _LINT_KINDS}
    for f in d["findings"]:
        by[f["kind"]].append(f)
    lines = [out.reason]
    if d.get("note"):
        lines.append(f"note  {d['note']}")
    for kind in _LINT_KINDS:
        rows = by[kind]
        if not rows:
            continue
        lines.append(f"{kind} ({len(rows)}):")
        for f in rows[:_LINT_SHOWN]:
            fix = f"  -> fix: {f['fix']}" if f.get("fix") else ""
            lines.append(f"  {' '.join(f['ids'])}: {f['detail']}{fix}")
        if len(rows) > _LINT_SHOWN:
            lines.append(f"  ... and {len(rows) - _LINT_SHOWN} more (--json holds them all)")
    lines += [f"applied  {s}" for s in (d.get("fixed") or {}).get("applied", [])]
    lines += [f"refused  {s}" for s in (d.get("fixed") or {}).get("skipped", [])]
    return "\n".join(lines)


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
        payload=(
            "source",
            "plan",
            "hash",
            "items",
            "dry_run",
            "would_file",
            "filed",
            "waves",
            "hint",
            "findings",
        ),
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
    Command(
        path=("plan", "lint"),
        summary="lint the live queue: overlaps without an ordering edge, missing globs, stale needs, duplicates, DX/fix at default priority (--fix applies the high-confidence ones)",
        tool="",
        reason=(
            "CLI-only by design, like `plan check`: it reads the queue and its output is "
            "a report to read -- not a tool an agent calls mid-task."
        ),
        call=lambda repo, a, agent: _api().plan_lint(
            repo,
            since=a.get("since") or "",
            phase=a.get("phase") or "",
            fix=bool(a.get("fix")),
            agent=agent,
        ),
        payload=("clean", "items", "findings", "fixed", "note"),
        render=_lint_text,
        cli=_SHOWN,
        params=(
            Param("since", help="Only live work filed on or after this ISO date (default: the whole open queue)."),
            Param("phase", help="Only live work under this phase id (default: the whole open queue)."),
            Param(
                "fix",
                type="boolean",
                help="Apply the high-confidence fixes: rule-forced ordering edges, stale-needs removals, priority bands.",
            ),
        ),
    ),
)
