"""How the flow verbs (`pr`, `version`, `promote`, `flow`) read on the command line: the
human text and the stderr notes. The operations are `api.flow`; the executor
(`surfaces/cliexec.py`) prints what these return, and `--json` is the body of the Outcome.
It imports nothing that loads the api (tests/test_declared_commands.py)."""

from __future__ import annotations

from collections.abc import Iterator

from ...core.flow import config_wins
from ...core.outcome import NOTHING, OK

# -- pr --------------------------------------------------------------------------------


def pr_status_text(out, a) -> str:
    d = out.data
    if not d["rows"] and not d["releases"] and not d["back_merges"]:
        return "no pull requests recorded"
    lines = []
    for r in d["rows"]:
        queue = (
            f"  [merge queue{' #' + str(r['queue_position']) if r.get('queue_position') else ''}]"
            if r.get("queue")
            else ""
        )
        lines.append(
            f"  {r['id']:<14} {r['state']:<8} #{r['number']} {r['pr_state']:<7} "
            f"review={r['review'] or '-'} checks={r['checks'] or '-'} "
            f"-> {r['base']}  (as of {r['synced_at'] or 'never'})" + queue
        )
    lines += [
        f"  release {r['version']}: {r.get('url', '')} awaiting merge into {r.get('base', '')}"
        for r in d["releases"]
    ]
    lines += [
        f"  back-merge {r['item']} -> {r['into']}: {r['state']} {r.get('url', '')}"
        for r in d["back_merges"]
    ]
    return "\n".join(lines)


def pr_sync_notes(out, a, ctx) -> Iterator[str]:
    for u in out.data["unavailable"]:
        yield f"UNAVAILABLE: {u}"
    for r in out.data["refused"]:
        yield f"REFUSED: {r}"
    if out.exit != OK:
        yield out.reason


def pr_sync_text(out, a) -> str | None:
    lines = [
        f"  {ch['item']}: {ch['what']} {ch['detail']} {ch['url']}".rstrip()
        for ch in out.data["changes"]
    ]
    lines += [
        f"  {w['id']}: waiting (review={w['review'] or '-'}, checks={w['checks'] or '-'}) {w['url']}"
        + (f"  -- {w['note']}" if w.get("note") else "")
        for w in out.data["waiting"]
    ]
    return "\n".join(lines) if lines else None


def pr_threads_text(out, a) -> str:
    d = out.data
    lines = []
    if d["replied"]:
        lines.append(f"replied on {a['thread']}")
    if d["resolved"]:
        lines.append(f"resolved {a['thread']}")
    if not d["threads"]:
        return "\n".join([*lines, f"{a['id']} #{d['number']}: no review threads"])
    for t in d["threads"]:
        mark = "resolved" if t["resolved"] else "OPEN    "
        where = f"{t['path']}:{t['line']}" if t["path"] else "(general)"
        more = f" (+{t['replies']})" if t["replies"] else ""
        lines.append(f"  {t['id']}  {mark}  {where}  {t['author']}: {t['body'][:100]!r}{more}")
    lines.append(f"{d['unresolved']} open of {len(d['threads'])}")
    return "\n".join(lines)


# -- version ---------------------------------------------------------------------------


def version_show_text(out, a) -> str:
    d = out.data
    head = f"on {d['ref']}: current {d['current'] or '(none)'} ({d['current_tag'] or 'no tag'})"
    if out.exit != OK:
        return f"{head}\n{out.reason}"
    reasons = "".join(f"\n  {r}" for r in d["reasons"][:12])
    return (
        f"{head}\nnext: {d['next']}  ({d['bump']}, {d['commits']} commit(s), "
        f"{len(d['items'])} item(s)){reasons}\n\n{d['notes']}"
    )


def version_lint_notes(out, a, ctx) -> Iterator[str]:
    if out.exit == OK and out.data["warning"]:
        yield out.data["warning"]


def version_lint_text(out, a) -> str:
    d = out.data
    return (
        f"release manifest lint ({d['policy']}): "
        f"{len(d['unmanifested'])} unmanifested, {len(d['waived'])} waived"
    )


def version_cut_notes(out, a, ctx) -> Iterator[str]:
    if out.exit != OK:
        yield out.reason
    elif out.data["warning"]:
        yield f"WARNING: {out.data['warning']}"


def version_cut_text(out, a) -> str | None:
    lines = [f"  {step}" for step in out.data["steps"]]
    if out.exit == OK:
        lines.append(f"{out.data['tag']}{' (dry run)' if out.data['dry_run'] else ''}")
    return "\n".join(lines) if lines else None


# -- flow ------------------------------------------------------------------------------


def _who(row) -> str:
    src = row["source"]
    rec = row["recorded"]
    if config_wins(src):
        return f"set in the {src} config"
    if src.startswith("log:"):
        by = rec.get("agent") or rec.get("user") or "?"
        how = "DEFAULT (nobody chose)" if rec.get("by") == "default" else "chosen"
        why = f" — {rec['reason']}" if rec.get("reason") and rec.get("by") != "default" else ""
        return f"{how} by {by} at {rec.get('at', '?')}{why}"
    return "UNDECIDED — the default applies at first use"


def flow_show_notes(out, a, ctx) -> Iterator[str]:
    for p in out.data["problems"]:
        yield f"PROBLEM: {p}"


def flow_show_text(out, a) -> str:
    lines = []
    if len(out.data["lines"]) > 1:
        lines.append("release lines (oldest first):")
        lines += [
            f"  {ln['line']:<10} {ln['branch'] or '(follows the model)'}"
            for ln in out.data["lines"]
        ]
        lines.append("")
    for row in out.data["choices"]:
        mark = " " if row["relevant"] else "·"
        lines.append(f"{mark} {row['knob']:<22} {row['value']:<14} {_who(row)}")
        if row.get("overridden"):
            lines.append(f"      {row['overridden']}")
    if out.data["pending"]:
        lines.append(
            f"\nundecided: {', '.join(out.data['pending'])}. Choose with "
            f"`ddflow flow choose <knob> <value> --reason ...`."
        )
    return "\n".join(lines)


def flow_choose_notes(out, a, ctx) -> Iterator[str]:
    if out.exit == OK and out.data["note"]:
        yield out.data["note"]


def flow_choose_text(out, a) -> str:
    return f"{out.data['knob']} = {out.data['value']} recorded"


# -- promote ---------------------------------------------------------------------------


def promote_add_text(out, a) -> str:
    if out.exit == NOTHING:
        return out.reason
    d = out.data
    return (
        f"{d['id']}: promote {d['from']} to {d['env']} ({d['ahead']} commit(s)). "
        f"Claim it like any task; it lands on {d['env']} after the promotion gates."
    )


def promote_deployed_text(out, a) -> str:
    return f"{out.data['env']}: live is now {out.data['sha'][:12]}"


def promote_status_text(out, a) -> str:
    if out.exit != OK:
        return out.reason
    lines = []
    for r in out.data["rows"]:
        if not r["exists"]:
            lines.append(f"  {r['env']:<16} MISSING — create it: git branch {r['env']} {r['from']}")
            continue
        behind = "up to date" if r["behind"] == 0 else f"{r['behind']} commit(s) behind {r['from']}"
        extra = f"; open: {', '.join(r['open'])}" if r["open"] else ""
        auto = " [auto]" if r["auto"] else ""
        if r["deployed"]:
            n = r["undeployed"]
            note = f" ({n} undeployed)" if n > 0 else " (undeployed count unknown)" if n < 0 else ""
            live = f"  live {r['deployed']}{note}"
        else:
            live = ""
        lines.append(f"  {r['env']:<16} {r['head']}  {behind}{extra}{auto}{live}")
    return "\n".join(lines)
