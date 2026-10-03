"""`status`, `show`, `recover`, `rebuild` — the human surface for `api.reporting`.

Each renders an `Outcome`. Where the prose needs objects rather than dicts — sorting
completed tasks by `completed_at`, calling `GateStatus.render()` — it reads them from the
Outcome's `_render` bucket, which is stripped from every wire body by `Outcome.body`. The
point is that the log is folded ONCE per command.
"""

from __future__ import annotations

import json
import sys
import time

from ...api import reporting as A
from ...infra import worktree as W
from ...views.markdown import addenda_lines, cap_held
from ..context import FAIL, NOTHING, OK, Ctx


def _queue_lines(r) -> list[str]:
    """Completed, in flight, ready, blocked — the four lists a person scans for."""
    out: list[str] = []
    if r["done"]:
        out.append("Completed:")
        for t in sorted(r["done"], key=lambda x: x.completed_at)[-12:]:
            sha = f"  ({t.merged_sha[:8]})" if t.merged_sha else ""
            out.append(f"  [x] {t.id:<12} {t.title}{sha}")
    if r["running"]:
        out.append("")
        out.append("In flight:")
        for t in r["running"]:
            held = f"  — {t.lease.holder}" if t.lease else ""
            out.append(f"  [~] {t.id:<12} {t.title}{held}")
    if r["ready"]:
        out.append("")
        out.append("Ready to start:")
        out += [f"  [ ] {t.id:<12} {t.title}" for t in r["ready"][:8]]
    out += [f"  INTERRUPTED: {note}" for note in r["interrupted"]]
    if r["capped"]:
        out.append("")
        held = _first([t.id for t in r["capped"]])
        out.append(f"{cap_held(len(r['capped']), bool(r['ready']))} {r['cap']}: {held}")
    if r["blocked"]:
        out.append("")
        out.append("Blocked: " + _first([f"{b.item} ({b.reason})" for b in r["blocked"]]))
    return out


_SHOWN = 8


def _first(names: list[str]) -> str:
    """The first few of a list, and "..." when there are more."""
    return ", ".join(names[:_SHOWN]) + (" ..." if len(names) > _SHOWN else "")


def _recorded_line(r) -> list[str]:
    """What the project has accumulated BESIDE its queue.

    Shown only when non-empty: a project with no decisions does not need to be told it
    has none, and every unconditional line is one the reader learns to skip.
    """
    extras = []
    if r["decisions"]:
        extras.append(f"{r['decisions']} architectural decision(s)")
    if r["lessons"]:
        extras.append(f"{r['lessons']} lesson(s)")
    if r["open_bugs"]:
        extras.append(f"{r['open_bugs']} OPEN bug(s)")
    return ["", "Recorded: " + " · ".join(extras)] if extras else []


def _warning_lines(r) -> list[str]:
    rec, findings = r["recoverable"], r["findings"]
    if not rec and not findings:
        return ["", "Nothing looping, nothing to recover."]
    out: list[str] = []
    if rec:
        salv = [x for x in rec if x.salvageable]
        out.append("")
        out.append(
            f"⚠ {len(rec)} recoverable situation(s)"
            + (f", {len(salv)} may contain unsaved work" if salv else "")
            + " — `ddflow recover`"
        )
    if findings:
        out.append("")
        out.append(f"⚠ {len(findings)} loop finding(s) — `ddflow loops`")
    return out


def cmd_status(a, c: Ctx) -> int:
    """One answer to "what is the state of this project?".

    Written for a human asking in a chat window, which is a different question from any
    of the machine views: it wants the shape of the thing, not a table.

    Split into section helpers when it moved out of `cli.py`, whose blanket `C901`
    exemption — written for `build_parser` — had been covering this function's 16
    branches too.
    """
    # Everything: a terminal or a `--json` pipe is where the full lists are asked for. The
    # bounded answer is the MCP tool's (Bd6aa9ffde9).
    out = A.status(c.repo, agent=c.requested_agent, full=True)
    if c.json:
        print(json.dumps(out.body(), indent=2, default=str))
        return out.exit
    d = out.data
    r = d["_render"]
    lines = [
        f"# {r['repo']}",
        "",
        f"{len(r['done'])}/{r['tasks']} tasks complete across {r['phases']} phase(s); "
        f"{r['hours']:.1f} agent-hours, {d['commits']} commit(s).",
        "",
        *_queue_lines(r),
        *_recorded_line(r),
        *_warning_lines(r),
    ]
    print("\n".join(lines))
    return out.exit


def contest_block(it) -> str:
    """`show`'s CONTESTED block: each rival definition or claim, whole.

    Whole, not summarised: the point of recording a contest is that the losing side is
    not lost, and a title alone is not enough to re-file it from.
    """
    if not (it.contested or it.lease_contest):
        return ""
    lines = [f"CONTESTED — `ddflow resolve {it.id} --keep <event-id|agent>` settles it"]
    for d in it.contested:
        lines.append(f"  definition {d['event']} by {d['agent']} (lamport {d['lamport']})")
        lines.append(f"    title {d['title']!r}")
        lines += [f"    | {ln}" for ln in (d["body"] or "").splitlines()]
    for h in it.lease_contest:
        where = h["lease"].get("worktree") or "-"
        met = " and ".join(g["holder"] for g in it.lease_clashes(h)) or "none of them"
        lines.append(f"  claim {h['event']} by {h['holder']} (worktree {where}), overlapped {met}")
    return "\n".join(lines)


def cmd_show(a, c: Ctx) -> int:
    out = A.show(c.repo, a.id, agent=c.requested_agent)
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        print(json.dumps(out.body("item"), indent=2, default=str))
        return OK
    if "bug" in out.data["_render"]:
        print(_bug_lines(out.data["_render"]["bug"]))
        return OK
    it = out.data["_render"]["item"]
    print(f"{it.id} [{it.kind}] {it.title}\n  state {it.state}")
    if it.needs:
        print(f"  needs {', '.join(it.needs)}")
    if it.globs:
        print(f"  globs {', '.join(it.globs)}")
    if it.lease:
        # The lease's OWN globs: what the conflict checks and the commit hook read, which
        # an operator could otherwise only learn from the event log (Bd8038b08a1).
        held = f" on {', '.join(it.lease.globs)}" if it.lease.globs else " on no globs"
        print(f"  lease {it.lease.holder} ({it.lease.remaining_s(time.time()):.0f}s left){held}")
    if it.worktree:
        print(f"  worktree {W.load_path(c.repo, it.worktree)} [{it.branch}]")
    if it.body:
        print(f"\n{it.body}\n")
    print(out.data["_render"]["gate_status"].render())
    extra = addenda_lines(out.data["_render"]["addenda"])
    if extra:
        print("\n".join(extra))
    contest = contest_block(it)
    if contest:
        print(contest)
    led = out.data["item"].get("ledger")
    if led:
        print(_ledger_lines(led))
    return OK


def _ledger_lines(led: dict) -> str:
    """The completion ledger, compact: what landed, what was skipped, what changed after."""
    lines = [
        "\n## Completion ledger"
        + (
            " (reconstructed from the log: no git facts were recorded)"
            if led["reconstructed"]
            else ""
        ),
        f"  completed {led['completed_at']} as {led['sha'] or '-'}; requirement {led['requirement']}"
        + ("; FORCED" if led["forced"] else ""),
        f"  files changed {led['files_total']}, tests among them {led['tests']}"
        + (f": {', '.join(led['files'])}" if led["files"] else ""),
    ]
    if led["skipped_gates"]:
        lines.append(f"  gates skipped: {', '.join(led['skipped_gates'])}")
    if led["amendments"]:
        lines.append(
            f"  amended {led['amendments']} time(s) after completion"
            + ("; the requirement text CHANGED" if led["requirement_changed_after"] else "")
        )
    return "\n".join(lines)


def _bug_lines(b: dict) -> str:
    """`show`'s answer for a bug id: what it is, where it was found, what fixes it, and
    how it was closed."""
    found = f"found {b['found_at']}" + (f" on {b['item']}" if b["item"] else "")
    lines = [f"{b['id']} [bug] {b['state']}"]
    if b.get("title"):
        lines.append(f"  title {b['title']}")
    marks = [f"{k} {b[k]}" for k in ("severity", "scope") if b.get(k) and b[k] != "project"]
    if marks:
        lines.append(f"  {'; '.join(marks)}")
    lines.append(f"  {found}")
    if b.get("upstream_sent_at"):
        where = b.get("upstream_url") or b.get("upstream_delivery") or "upstream"
        lines.append(f"  reported upstream {b['upstream_sent_at']}: {where}")
    if b["fixing"]:
        lines.append(f"  fix task(s): {', '.join(b['fixing'])}")
    if b["mentioned_by"]:
        lines.append(f"  mentioned by: {', '.join(b['mentioned_by'])}")
    if b["fixed_at"]:
        tests = [t for t in b["regression_tests"] or [b["regression_test"]] if t]
        lines.append(
            f"  closed {b['fixed_at']} as fixed" + ("; regression test(s):" if tests else "")
        )
        lines += [f"    {t}" for t in tests]
    if b["invalid_at"]:
        # A fix wins over an invalid closure (`Bug.resolution`): shown as what it now is.
        was = "earlier closed" if b["fixed_at"] else "closed"
        tail = " -- superseded by the fix" if b["fixed_at"] else ""
        lines.append(f"  {was} {b['invalid_at']} as invalid: {b['invalid_reason']}{tail}")
        if b["evidence"]:
            lines.append(f"    evidence: {b['evidence']}")
    if b["lesson"]:
        lines.append(f"  lesson {b['lesson']}")
    lines += ["", b["summary"]]
    lines += addenda_lines(b)
    return "\n".join(lines)


def cmd_recover(a, c: Ctx) -> int:
    out = A.recover(c.repo, item=a.item or "", apply=a.apply, agent=c.requested_agent)
    if c.json:
        print(json.dumps(out.body("found"), indent=2, default=str))
        return out.exit
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING
    found = out.data["_render"]["found"]
    salv = out.data["_render"]["salvageable"]
    print(f"{len(found)} recoverable situation(s); {len(salv)} may contain work:\n")
    for r in found:
        flag = "!! " if r.salvageable else "   "
        print(f"{flag}{r.item}  [{r.kind}]  was: {r.holder}")
        if r.worktree:
            print(f"     worktree {r.worktree}")
        print(f"     {r.advice}\n")
    if salv:
        print("Worktrees marked !! are NOT touched automatically. Inspect, salvage, then release.")
    return OK


def cmd_rebuild(a, c: Ctx) -> int:
    out = A.rebuild(c.repo, agent=c.requested_agent)
    d = out.data
    c.out(
        f"rebuilt index from {d['events']} events in {d['seconds']:.2f}s "
        f"({d['items']} items, {d['lessons']} lessons)",
        out.body(("events", "items")),
    )
    return out.exit


def cmd_doctor(a, c: Ctx) -> int:
    """Everything wrong with this project, and everything worth knowing.

    The prose comes from `views/human.py`, not from here: the MCP tool returns the same
    report as its body, so a renderer in this module would mean one surface reaching into
    the other. `--json` gets the lists.
    """
    out = A.doctor(c.repo, agent=c.requested_agent)
    if c.json:
        print(json.dumps(out.body(("problems", "notes", "events", "items")), indent=2))
        return out.exit
    print(out.data["text"])
    return out.exit


def cmd_board(a, c: Ctx) -> int:
    out = A.board(c.repo, phase=a.phase or "", agent=c.requested_agent)
    if out.exit == FAIL:  # an unknown --phase (Bc2acd426f4): said as `next` says it
        from .lifecycle import _next_without_plan

        return _next_without_plan(out, c)
    c.out(out.data["text"], out.body())
    return OK


def cmd_render(a, c: Ctx) -> int:
    out = A.render(
        c.repo, show=getattr(a, "show", "") or "", out_dir=a.out or "", agent=c.requested_agent
    )
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if out.data["show"]:
        print(out.data["text"])
        return OK
    c.out("\n".join(out.data["files"]), out.body(("files",)))
    return OK


def cmd_replay(a, c: Ctx) -> int:
    out = A.replay(c.repo, out_dir=a.out or "", verify=a.verify, agent=c.requested_agent)
    for p in out.data["problems"]:
        print(f"  ! {p}", file=sys.stderr)
    if out.data["problems"]:
        print(f"{len(out.data['problems'])} recorded commit(s) no longer resolve.", file=sys.stderr)
    if out.data["files"]:
        c.out(out.data["text"], out.body(("files",)))
        return OK
    print(out.data["text"])
    return OK
