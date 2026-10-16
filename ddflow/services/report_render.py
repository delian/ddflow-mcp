"""The completion report as text: each section of the data `services.report` collected becomes
the lines the template (`agent_report.md`) lays out. A section with nothing to say reads
``none``, never empty: a report that omits a heading hides whether it was checked."""

from __future__ import annotations

from typing import Any

from . import agent_brief as AB
from . import prompts as P

#: Test files listed before "and N more".
MAX_TEST_FILES = 12


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"- {x}" for x in lines) if lines else "none"


def _gate_line(g: dict[str, Any]) -> str:
    who = g["model"] or g["by"]
    line = f"{g['gate']}: {g['outcome']}"
    if g["model"] or g["reviewer"]:
        line += f" — reviewer {g['model'] or g['reviewer']} (family {g['family'] or 'unknown'})"
    elif who and g["outcome"] != "passed":
        line += f" — by {who}"
    if g["rounds"]:
        line += f", {g['rounds']} round(s)"
    t = g.get("triage")
    if t:
        line += (
            f", {t['findings']} finding(s): {t['refuted']} refuted, {t['confirmed']} confirmed, "
            f"{t['untriaged']} without a verdict"
        )
    if g.get("covered_by"):
        line += f" ({g['covered_by']})"
    if g["reason"]:
        line += f" — {g['reason']}"
    if g.get("earlier_tree"):
        line += f" [ran on an earlier tree: {g['earlier_tree']}]"
    return line


def _tests(d: dict[str, Any]) -> str:
    lines = []
    for b in d["regression"]["bugs"]:
        status = b["verified"] or "no verification recorded"
        tests = ", ".join(b["tests"]) or "no test named"
        extra = f" ({b['reason']})" if b["reason"] else ""
        lines.append(f"{b['bug']}: {tests} — complete verified it: {status}{extra}")
    files = d["regression"]["test_files"]
    if files:
        shown = ", ".join(files[:MAX_TEST_FILES])
        more = f" and {len(files) - MAX_TEST_FILES} more" if len(files) > MAX_TEST_FILES else ""
        lines.append(f"test files in the change: {shown}{more}")
    return _bullets(lines)


def variables(d: dict[str, Any]) -> dict[str, str]:
    """The template variables of a collected report, all strings."""
    ld, it = d["landing"], d["item"]
    more = ld["commits_total"] - len(ld["commits"])
    commits = ", ".join(ld["commits"]) + (f" (+{more} more)" if more > 0 and ld["commits"] else "")
    landing = (
        f"merged {ld['merge_sha'][:12]} into {ld['base'] or 'the base'}"
        if ld["merge_sha"]
        else "not merged"
    )
    if ld["branch_head"]:
        landing += f"; branch {ld['branch']} head {ld['branch_head'][:12]}"
    if commits:
        landing += f"; commits {commits}"
    if ld["completed_at"]:
        landing += f"; completed {ld['completed_at']} by {ld['completed_by'] or 'unknown'}"
    hand = [f"{g['gate']}: {g['note'] or 'no evidence text'}" for g in d["gates"] if g["by_hand"]]
    f = d["filed"]
    filed = [
        f"{b['id']} (bug{', open' if b['open'] else ', closed'}): {b['title']}" for b in f["bugs"]
    ]
    filed += [
        f"{t['id']} (task{', open' if t['open'] else ', closed'}): {t['title']}" for t in f["tasks"]
    ]
    return {
        "item": it["id"],
        "title": it["title"],
        "agent": it["agent"] or "unknown",
        "state": it["state"],
        "landing": landing,
        "tests": _tests(d),
        "gates": _bullets([_gate_line(g) for g in d["gates"]]),
        "by_hand": _bullets(hand),
        "overrides": _bullets(d["overrides"]),
        "filed": _bullets(filed),
        "follow_ups": _bullets(d["follow_ups"]),
        "not_done": _bullets(d["not_done"]),
    }


def render(tmpl, d: dict[str, Any]) -> str:
    """The report: the template's sections in order, each rendered; none is dropped."""
    v = variables(d)
    parts = [P.render(body, **v).strip() for _name, body in AB.split(tmpl.text)]
    return "\n\n".join(p for p in parts if p) + "\n"
