"""`ddflow review` and `ddflow reviewers ...` — the human surface for `api.review`.

The CLI passes `print` as the progress callback, so a review that takes two minutes says
what it is doing while it does it. The MCP tool collects the same lines and returns them
as the body, which is what its PROSE_TOOLS entry has always promised.

`reviewers presets`, `add` and `test` stay here: they are configuration authoring and a
live probe, neither of which has an MCP tool, and `test` deliberately runs a canned diff
rather than the project's.
"""

from __future__ import annotations

import sys

# The SUBMODULE. `from ...api import review` would bind the re-exported function; this
# form reads `ddflow.api.review` out of sys.modules and cannot be shadowed.
import ddflow.api.review as A

from ..context import FAIL, NOTHING, OK, REFUSED, Ctx


def _triage(a, c: Ctx, item: str) -> int:
    """`ddflow review triage <id> --gate G --finding N --refuted|--confirmed --probe ...`."""
    if a.refuted and a.confirmed:
        print("--refuted and --confirmed are exclusive: one verdict per finding", file=sys.stderr)
        return FAIL
    out = A.triage(
        c.repo,
        item,
        gate=a.gate or "",
        finding=a.finding or 0,
        verdict="refuted" if a.refuted else "confirmed" if a.confirmed else "",
        probe=a.probe or "",
        agent=c.requested_agent,
    )
    if out.exit != OK:
        print(out.reason, file=sys.stderr)
        return out.exit
    print(out.data["text"])
    return OK


def cmd_review(a, c: Ctx) -> int:
    ids = list(a.id or [])
    if ids[:1] == ["triage"]:  # `review triage <id>`: a verb, not an item named "triage"
        if len(ids) != 2:  # noqa: PLR2004 -- the verb and the item
            print("usage: ddflow review triage <id> --gate G --finding N ...", file=sys.stderr)
            return FAIL
        if a.chunk:
            print("--chunk re-reviews; `review triage` only records a verdict", file=sys.stderr)
            return FAIL
        return _triage(a, c, ids[1])
    # The triage flags are accepted by this parser (it is the verb's parser too) but mean
    # nothing to a review. Ignoring them started a 25-minute reviewer run the caller never
    # asked for (bug B3531d304ec), so refuse BEFORE any reviewer is contacted.
    stray = [
        flag
        for flag, given in (
            ("--finding", a.finding),
            ("--refuted", a.refuted),
            ("--confirmed", a.confirmed),
            ("--probe", a.probe),
        )
        if given is not None and given is not False  # `--finding 0`, `--probe ""` still count
    ]
    if stray:
        item = ids[0] if len(ids) == 1 else "<id>"
        print(
            f"{', '.join(stray)} record a verdict on a finding and need the `triage` verb; "
            "without it this would start a full re-review. Did you mean:\n"
            f"  ddflow review triage {item} --gate {a.gate or 'critic'} --finding N "
            '--refuted|--confirmed --probe "..."',
            file=sys.stderr,
        )
        return FAIL
    if len(ids) > 1:
        print(f"review takes one item id; got {ids}", file=sys.stderr)
        return FAIL
    out = A.review(
        c.repo,
        gate=a.gate or "critic",
        item=ids[0] if ids else "",
        intent=a.intent or "",
        context=a.context or "",
        base=a.base or "",
        commit=a.commit or "",
        branch=a.branch or "",
        called_from=c.called_from,
        # Streamed as it happens. Silence for two minutes reads as a hang, and an agent
        # watching a hung tool kills it.
        on_progress=lambda line: print(line, flush=True),
        agent=c.requested_agent,
        chunks=a.chunk or None,
        delta=bool(a.delta),
        full=bool(getattr(a, "full", False)),
        force=bool(a.force),
        reason=a.reason or "",
    )
    # A refusal (exit 3) with no reviewer behind it -- a `--chunk` re-review that
    # cannot merge -- has printed nothing else (roborev 991).
    if out.exit == FAIL or (out.exit in (NOTHING, REFUSED) and not out.data.get("reviewer")):
        print(out.reason, file=sys.stderr)
    return out.exit


def _reviewers_detect(a, c: Ctx) -> int:
    out = A.reviewers_detect(
        c.repo, write=a.write, shared=bool(getattr(a, "shared", False)), agent=c.requested_agent
    )
    if out.exit in (NOTHING, FAIL, REFUSED):
        print(out.reason, file=sys.stderr)
        return out.exit
    print(out.data["text"])
    return OK


def _reviewers_list(a, c: Ctx) -> int:
    out = A.reviewers_list(c.repo, agent=c.requested_agent)
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING
    print(out.data["text"])
    return OK


def _reviewers_presets(_a, _c: Ctx) -> int:
    from ...services import review as R

    for name, spec in sorted(R.PRESETS.items()):
        where = spec.get("base_url") or spec.get("command", "")
        print(f"  {name:<12} {spec['kind']:<9} {where}")
    print("\nAdd one with:  ddflow reviewers add --preset <name> [--model M]")
    return OK


def _reviewers_add(a, c: Ctx) -> int:
    """Append a `[[reviewer]]` block (`api.review.reviewers_add`)."""
    from ...config import csv_list
    from ...services import reviewer_trust as RT

    # A command reviewer only from a person (decision D-reviewer-trust): nothing about
    # this invocation may say it is an agent's.
    why = RT.agent_marker(c.requested_agent)
    out = A.reviewers_add(
        c.repo,
        preset=a.preset or "",
        name=a.name or "",
        model=a.model or "",
        base_url=a.base_url or "",
        gates=csv_list(a.gates) if a.gates else (),
        no_launch=bool(a.no_launch),
        shared=bool(getattr(a, "shared", False)),
        person=not why,
        agent=c.cfg.agent.id,
    )
    if out.exit != OK:
        print(out.reason, file=sys.stderr)
        if out.data.get("reviewer_refused") and why:
            print(f"  (this command runs as an agent: {why}; a person runs it)", file=sys.stderr)
        return out.exit
    d = out.data
    c.out(out.data["text"], {"name": d["name"], "path": d["path"], "shared": d["shared"]})
    return OK


def _reviewers_test(a, c: Ctx) -> int:
    """A live probe against a CANNED diff.

    Deliberately not the project's diff: this answers "does this endpoint answer at all",
    and running it over real work would make a connectivity check look like a review.
    """
    from ...services import review as R

    targets = [r for r in R.load_reviewers(c.repo) if not a.name or r.name == a.name]
    if not targets:
        print(f"no reviewer named {a.name!r}; `ddflow reviewers list`", file=sys.stderr)
        return FAIL
    worst = OK
    for r in targets:
        res = R.review(
            r,
            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
            "@@ -1,3 +1,3 @@\n def f(items):\n-    return sum(items) / len(items)\n"
            "+    return sum(items) / len(items) if items else 0\n",
            intent="guard the empty-list case in the mean helper",
        )
        print(
            f"  {r.name}: {res.label} ({res.coverage()}, {res.elapsed_s:.1f}s)"
            + (f" — {res.reason}" if res.reason else "")
        )
        for f in res.findings[:3]:
            print(f"      [{f.severity}] {f.title[:90]}")
        worst = max(worst, 0 if res.status == R.REVIEWED else res.status)
    return worst


def _reviewers_approve(a, c: Ctx) -> int:
    """A PERSON vouches for a tool-written reviewer entry (decision D-reviewer-trust).

    CLI only and refused under an agent identity, like `ddflow approve`: an agent that
    could approve the reviewer it wrote would make the record decorative. With no name,
    lists the entries waiting for approval.
    """
    from ...api._base import _load
    from ...services import reviewer_trust as RT

    if not getattr(a, "name", ""):
        _log, _cfg, st = _load(c.repo, c.requested_agent)
        rows = RT.pending(c.repo, st)
        if not rows:
            c.out("No tool-written reviewer is waiting for approval.", {"pending": []})
            return NOTHING
        lines = [
            f"  {r['name']:<24} {r['kind']:<8} written by {r['agent'] or '?'} at {r['at'][:19]}"
            for r in rows
        ]
        c.out(
            "Written by a tool, not counted until approved:\n"
            + "\n".join(lines)
            + "\n\nCheck each entry, then: ddflow reviewers approve <name>",
            {"pending": rows},
        )
        return OK
    try:
        line = RT.approve(c.repo, a.name, requested_agent=c.requested_agent, note=a.note or "")
    except RT.ReviewerRefused as exc:
        print(str(exc), file=sys.stderr)
        return REFUSED
    c.out(line, {"name": a.name, "approved": True, "line": line})
    return OK


def cmd_reviewers(a, c: Ctx) -> int:
    """A dispatcher. Each subcommand is its own function — they share nothing but the
    config file they read, and the chain of `if` arms was 18 branches."""
    return {
        "detect": _reviewers_detect,
        "list": _reviewers_list,
        "presets": _reviewers_presets,
        "add": _reviewers_add,
        "test": _reviewers_test,
        "approve": _reviewers_approve,
    }.get(a.reviewers_cmd or "list", _reviewers_list)(a, c)
