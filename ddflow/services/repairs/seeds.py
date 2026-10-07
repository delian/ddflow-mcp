"""The seed repairs: the damage this week's fixes stopped, mended in logs written before them.

Each is registered in `REGISTRY` (`services/repairs/__init__.py`) and exercised by
`tests/test_repairs.py` on a crafted log carrying that damage: detect finds it, repair
appends corrective events, a second run finds nothing, and every original line is
byte-identical.
"""

from __future__ import annotations

from ...core import digest as D
from ...core.events import is_older
from ...core.model import DONE
from ...infra import worktree as W
from ...infra.log import _parse_event, running_version
from .. import sessions as SS
from ..export import frame as F
from ..export import ops as EX
from ..export import registry as ER
from ..export import write as EW
from ..export.query import ExportError
from .base import OPERATOR, Context, Corrective, Finding, Repair, Unavailable

# -- prompts and notes recorded with no session id --------------------------------------


def _orphans_detect(ctx: Context) -> list[Finding]:
    return [
        Finding(o.id, f"{o.kind} at {o.ts[:16]} by {o.agent} has no session id")
        for o in SS.unadopted_orphans(ctx.events)
    ]


def _orphans_repair(ctx: Context, _found: list[Finding]) -> list[Corrective]:
    # The findings are every unadopted orphan of `ctx.events` (a settled one has its copy,
    # so it is adopted), which is exactly what `plan_adoptions` adopts.
    return SS.plan_adoptions(ctx.events, ctx.cfg)


ORPHANS = Repair(
    id="orphan-prompts",
    since="0.1.11",
    title="prompts and notes recorded with no session id",
    action="append a copy of each under the nearest session (`adopted_from`), as "
    "`ddflow session adopt-orphans` does",
    detect=_orphans_detect,
    repair=_orphans_repair,
)


# -- items completed with --force over unmet conditions ----------------------------------


def _forced_detect(ctx: Context) -> list[Finding]:
    last = {e.subject: e for e in ctx.events if e.kind == "item.completed"}
    out = []
    for subject, e in last.items():
        it = ctx.st.items.get(subject)
        if not e.data.get("forced") or it is None or it.state != DONE:
            continue  # completed cleanly since, reopened or removed: nothing stands on it
        over = "; ".join(str(b) for b in e.data.get("overridden") or []) or "unrecorded"
        out.append(
            Finding(
                e.id or e.compute_id(),
                f"{subject} was completed with --force over: {over} "
                f"(`ddflow verify {subject}` re-checks it)",
            )
        )
    return out


FORCED = Repair(
    id="forced-completions",
    since="0.1.9",
    title="items completed with --force over unmet conditions",
    action="report each and annotate it in the log (the repair record names the item and "
    "what was overridden); the completion itself is left standing",
    detect=_forced_detect,
    repair=lambda _ctx, _found: [],
)


# -- lines that are not events: torn appends, non-object JSON ----------------------------


def _torn_detect(ctx: Context) -> list[Finding]:
    out = []
    for shard in ctx.log.shards():
        try:
            raw = shard.read_bytes()
        except OSError as exc:
            raise Unavailable(f"could not read {shard.name}: {exc}") from exc
        lines = raw.decode("utf-8", errors="replace").split("\n")
        for n, line in enumerate(lines, 1):
            text = line.strip()
            if text and _parse_event(text) is None:
                out.append(
                    Finding(
                        f"{shard.name}:{n}:{D.content_digest(text, length=12)}",
                        f"{shard.name} line {n} is not an event (a torn append or a stray "
                        f"line, {len(text)} chars): skipped by every reader",
                    )
                )
    return out


TORN = Repair(
    id="unreadable-lines",
    since="0.1.13",
    title="event-log lines that are not events (torn appends, non-object JSON)",
    action="quarantine note: record each line's shard, number and digest; nothing is deleted",
    detect=_torn_detect,
    repair=lambda _ctx, _found: [],
    bugs=("B28b3839fe6", "B643e9c830c", "Bed68b638c2"),
)


# -- events whose id is not the hash of their body ---------------------------------------


def _mismatched_detect(ctx: Context) -> list[Finding]:
    return [
        Finding(
            e.id,
            f"{e.id} ({e.kind} {e.subject} by {e.agent}): content does not match "
            f"its address (edited after it was written?)",
        )
        for e in ctx.events
        if e.id and e.id != e.compute_id()
    ]


MISMATCHED = Repair(
    id="mismatched-ids",
    since="0.1.11",
    title="events whose content does not match their id",
    action="quarantine note: record each event id; the line is left as it is",
    detect=_mismatched_detect,
    repair=lambda _ctx, _found: [],
)


# -- event shards by an agent id with no committed history -------------------------------


def unknown_author_shards(ctx: Context) -> tuple[list[str], str]:
    """Shard file names (not this agent's) absent from the default branch's committed tree,
    and that branch. Raises `Unavailable` when git cannot say."""
    shards = {p.name for p in ctx.log.shards()} - {ctx.log.shard.name}
    if not shards:
        return [], ""
    if not W.git(ctx.repo, "rev-parse", "--is-inside-work-tree").ok:
        raise Unavailable("not a git repository")
    base = W.default_branch(ctx.repo)
    if not W.git(ctx.repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").ok:
        raise Unavailable(f"no commit on {base!r}")
    known = W.git_paths(ctx.repo, "ls-tree", "-r", "--name-only", base, "--", ".ddflow/events")
    if known is None:
        raise Unavailable(f"could not list {base!r}")
    return sorted(shards - {k.rsplit("/", 1)[-1] for k in known}), base


def _authors_detect(ctx: Context) -> list[Finding]:
    new, base = unknown_author_shards(ctx)
    return [
        Finding(
            name,
            f"{name} was written by an agent id with no committed history on {base}: check "
            f"who wrote it (`git log -- .ddflow/events/{name}`) before trusting it",
        )
        for name in new
    ]


AUTHORS = Repair(
    id="unknown-author-shards",
    since="0.1.11",
    title="event shards by an agent id with no committed history",
    action="record that the operator reviewed each shard's author; nothing else changes",
    detect=_authors_detect,
    repair=lambda _ctx, _found: [],
    consent=OPERATOR,
)


# -- generated documents still carrying an older ddflow's render -------------------------


def _old_renders(ctx: Context) -> list[tuple[EX.Spec, str]]:
    """`(spec, header version)` of each selected whole-file document an older ddflow
    rendered and that regenerating would change. A hand-edited file is never listed."""
    running = running_version()
    out = []
    for doc in EX.selection(ctx.cfg):
        try:
            spec = EX.spec_for(ctx.cfg, doc, writing=True)
            if spec.mode != ER.WHOLE:
                continue
            path, _rel = EW.safe_target(ctx.repo, spec.path)
            head, _body = F.split(path.read_text("utf-8")) if path.is_file() else (None, "")
        except (ExportError, OSError, UnicodeDecodeError, ValueError):
            continue  # export --check reports a document it cannot read; not ours to judge
        if head is not None and is_older(head.version, running):
            out.append((spec, head.version))
    if not out:
        return []
    q = EX.load(ctx.repo, ctx.cfg)
    return [(s, v) for s, v in out if EX.state_of(ctx.repo, ctx.cfg, q, s)[0] == "stale"]


def _exports_detect(ctx: Context) -> list[Finding]:
    return [
        Finding(
            f"{s.doc}:{s.path}:{v}",
            f"{s.path} ({s.doc}) carries a render by ddflow {v}; regenerating it changes it",
        )
        for s, v in _old_renders(ctx)
    ]


def _exports_repair(ctx: Context, found: list[Finding]) -> list[Corrective]:
    keys = {f.key for f in found}
    stale = [s for s, v in _old_renders(ctx) if f"{s.doc}:{s.path}:{v}" in keys]
    if stale:
        q = EX.load(ctx.repo, ctx.cfg)
        for spec in stale:
            EX.write_doc(ctx.repo, ctx.cfg, q, spec)
    return []


EXPORTS = Repair(
    id="old-export-renders",
    since="0.1.11",
    title="generated documents still carrying an older ddflow's render",
    action="regenerate each, as `ddflow export <doc> --update` does (a hand-edited file is "
    "never touched)",
    detect=_exports_detect,
    repair=_exports_repair,
)


SEEDS: tuple[Repair, ...] = (ORPHANS, FORCED, TORN, MISMATCHED, AUTHORS, EXPORTS)
