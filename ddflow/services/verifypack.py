"""The evidence pack: what an independent verifier needs to judge a completion.

`ddflow verify --pack` prints it; `--judge` hands it to a different-family reviewer as the
review's context. Mechanical checks (`services/verify.py`) cannot tell whether the
requirement was met in spirit -- a reviewer who has the requirement, what landed and the
mechanical findings can. The requirement text is fenced as DATA (it is an agent- or
operator-written record, not an instruction), and the pack is bounded: counts, short lists
and a diff STAT, never a diff.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..config import Config
from ..core import provenance as PV
from ..core.events import Event
from ..core.model import State
from . import ledger as LG
from . import verify as V

MAX_BODY_CHARS = 3000
MAX_STAT_LINES = 40
_QUESTION = (
    "Judge ONLY from this evidence and the diff: does what landed satisfy the requirement? "
    "Report each clause of the requirement that the evidence does NOT show as met, as one "
    "finding naming the clause and what is missing. Report nothing when it is met. Do not "
    "restate the mechanical findings; question them if they are wrong."
)


def _stat(repo: Path, sha: str) -> list[str]:
    from ..infra.worktree import git

    if not sha:
        return []
    r = git(repo, "show", "--stat", "--format=", "-m", "--first-parent", sha, timeout=60)
    if not r.ok:
        return []
    lines = r.out.splitlines()
    if len(lines) <= MAX_STAT_LINES:
        return lines
    more = len(lines) - MAX_STAT_LINES
    return [*lines[:MAX_STAT_LINES], f"[... truncated: {more} more line(s) of diff stat]"]


def requirement_text(title: str, body: str) -> str:
    """The requirement the verifier reads, with an explicit marker when it was cut."""
    text = f"{title}\n\n{body}".strip()
    if len(text) <= MAX_BODY_CHARS:
        return text
    return (
        text[:MAX_BODY_CHARS]
        + f"\n[... truncated: {len(text) - MAX_BODY_CHARS} more characters of requirement]"
    )


def requirement(events: Sequence[Event], item_id: str) -> str | None:
    """The requirement AS IT STOOD AT COMPLETION (not as it reads now), or None."""
    led = LG.build(events, item_id)
    if led is None:
        return None
    r = led["requirement"]
    return requirement_text(r["title"], r["body"])


def _data(kind: str, ident: str, text: str) -> str:
    """Free text from the record, as DATA: a skip reason is as untrusted as the requirement."""
    return PV.fence(kind, ident, text, PV.Origin(PV.UNKNOWN))


def pack(repo: Path, cfg: Config, st: State, events: Sequence[Event], item_id: str) -> str | None:
    """The pack for one completed item, or None when it is not completed."""
    led = LG.build(events, item_id)
    if led is None:
        return None
    # Reconstructed landings are included (flagged), so the verifier sees what verify saw.
    from . import backfill as BF

    led = BF.apply(repo, st, led, item_id)
    rep = V.check(repo, cfg, st, events, item_id)
    it = st.items[item_id]
    d = led["done"]
    out = [
        f"# Verify the completion of {item_id}",
        "",
        f"_{PV.DATA_RULE}_",
        "",
        "## Requirement (as it stood at completion)",
        "",
        PV.fence(
            "requirement",
            item_id,
            requirement(events, item_id) or "",
            PV.Origin(PV.UNKNOWN),
            inline=False,
        ),
        "",
        "- declared globs: "
        + (
            _data("globs", item_id, ", ".join(led["requirement"]["globs"]))
            if led["requirement"]["globs"]
            else "(none)"
        ),
        f"- requirement digest `{led['requirement']['digest']}`"
        + (
            "; **the requirement was edited after completion**"
            if led["requirement_changed_after"]
            else ""
        ),
        "",
        "## What was recorded",
        "",
        f"- completed {led['completed_at'][:19]} by {_data('agent', 'completed_by', led['completed_by'])}"
        + (f" as `{led['sha'][:10]}`" if led["sha"] else " (no commit recorded)"),
        f"- forced: {'YES, overriding ' + ', '.join(led['overridden']) if led['forced'] else 'no'}",
        "- gates: "
        + (", ".join(f"{g}={v['outcome']}" for g, v in led["gates"].items()) or "(none recorded)"),
    ]
    if led["skipped"]:
        out.append(
            "- skipped gates and why: "
            + "; ".join(
                f"{g}: "
                + (
                    _data("skip-reason", g, led["gates"][g]["reason"])
                    if led["gates"][g].get("reason")
                    else "NO REASON"
                )
                for g in led["skipped"]
            )
        )
    if led["amendments"]:
        out.append(f"- amended {len(led['amendments'])} time(s) after completion")
    out += ["", "## What landed", ""]
    if d["files_known"]:
        out.append(f"- {d['files_total']} file(s) changed, {len(d['tests'])} of them tests")
        out += [f"  - {_data('path', item_id, f)}" for f in d["files"][:12]]
        out += [
            "- tests: " + ", ".join(_data("path", item_id, t) for t in d["tests"][:8])
            if d["tests"]
            else "- tests: NONE touched"
        ]
        stat = _stat(repo, led["sha"])
        if stat:
            out += ["", "```", *stat, "```"]
    else:
        out.append("- UNKNOWN: no landing could be found, so what changed is not known")
    if led.get("backfill"):
        out.append(
            f"- _reconstructed after the fact: {led['backfill']['how']}_"
        )  # ours, not record text
    out += ["", f"## Mechanical findings: {rep.verdict}", ""]
    # A claim's detail can echo record text (a declared path, a skip reason): fenced too.
    out += [f"- [{c.status}] {c.id}: {_data('finding', c.id, c.detail)}" for c in rep.claims]
    out += ["", "## Your task", "", _QUESTION, ""]
    if it.reopened:
        out.append(f"_This item was reopened {len(it.reopened)} time(s) before._")
    return "\n".join(out)
