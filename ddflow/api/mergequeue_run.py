"""`queue run`: the integrator of the merge queue.

One integrator at a time (a lock the kernel releases with the process) takes the entries in
the order they were added. For each it merges the base into the item's branch, regenerates
the pin files by their recipes (never by hand), runs the ci gate once on that merged result
and lands the branch with the ordinary `merge`. An entry that cannot be landed is PARKED with
the reason and the next one is taken: a failure never blocks the line.

Nothing here is a way round a gate. An entry whose other gates lack an outcome is parked
without being run; the gates the integrator produces itself are recorded like any other
(`ci` by `ci run`, `unit_tests` by `gate run`), a failure included.
"""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..infra import git as G
from ..infra import worktree as W
from ..services import flakes as FL
from ..services import flow as FS
from ..services import gates as GS
from ..services import mergequeue as MQ
from ..services import pins as PN
from ..services import pins_text as PT
from ._base import _load
from .cirun import run as ci_run
from .gates import run as run_gate
from .lifecycle import merge as land
from .mergequeue_add import _unit_tests, refusal


def _ident(tree: Path) -> list[str]:
    """git's `-c` identity for a commit made here when the checkout has none of its own."""
    if G.run(tree, "config", "user.email").out.strip():
        return []
    return ["-c", "user.name=ddflow", "-c", "user.email=ddflow@localhost"]


def _is_pin(path: str, files: list[str]) -> bool:
    return any(path == f.rstrip("/") or path.startswith(f.rstrip("/") + "/") for f in files)


def _merge_pin_text(tree: Path, path: str) -> bool:
    """Resolve a conflicted pin file the way the git merge driver does (`services.pins_text`):
    prose of both sides merged, our side of the generated regions, then stage it. False when
    prose both sides edited conflicts, or git cannot (a delete against an edit)."""
    blobs = []
    for stage in (2, 1, 3):  # ours, base, theirs
        blob = G.run(tree, "show", f":{stage}:{path}", binary=True)
        if not blob.ok and stage != 1:  # no base: both sides added the file
            return False
        blobs.append(blob.out_bytes or b"")
    result, conflicted = PT.merge_pin_text(*blobs)
    if conflicted:
        return False
    (Path(tree) / path).write_bytes(result)
    return G.run(tree, "add", "--", path).ok


def _bring_in_base(repo: Path, cfg, wt: W.Worktree) -> str:
    """Merge the base into the item's branch, in its own tree. "" on success, else why not
    (the tree is then as it was). A conflict in a pin file alone is not a conflict: the
    file is re-recorded from its recipe next, so our side is kept and regeneration decides."""
    if W.behind(wt) == 0:
        return ""
    ident = _ident(wt.path)
    r = G.run(wt.path, *ident, "merge", "--no-edit", wt.base)
    if r.ok:
        return ""
    conflicts = G.unmerged(wt.path) or []
    pins = PN.pin_files(wt.path, cfg)
    if conflicts and all(_is_pin(c, pins) for c in conflicts):
        if all(_merge_pin_text(wt.path, c) for c in conflicts):
            if G.run(wt.path, *ident, "commit", "--no-edit").ok:
                return ""
    G.run(wt.path, "merge", "--abort")
    named = ", ".join(conflicts[:10]) or (r.err or r.out)[-300:]
    return f"merging {wt.base} into {wt.branch} conflicts in: {named}"


def _regenerate_pins(repo: Path, cfg, wt: W.Worktree) -> str:
    """Re-record the stale pin files of the merged tree and commit them. "" or why not."""
    files = PN.pin_files(wt.path, cfg)
    if not files:
        return ""
    rep = PN.regenerate(wt.path, cfg)
    if rep.problem:
        return f"pins: {rep.problem}"
    if rep.failed:
        return "pins: " + "; ".join(f"{p.name}: {p.error}" for p in rep.failed)
    if rep.unavailable:  # not re-recorded, and not known to be current: not a merge to build on
        return "pins unavailable: " + "; ".join(
            f"{p.name}: {p.unavailable}" for p in rep.unavailable
        )
    names = [p.name for p in rep.pins if p.regenerated]
    if not names:
        return ""
    G.run(wt.path, "add", "--", *files)
    if G.run(wt.path, "diff", "--cached", "--quiet").ok:
        return ""
    msg = "regenerate pins: " + ", ".join(names)
    made = G.run(wt.path, *_ident(wt.path), "commit", "-m", msg)
    return "" if made.ok else f"could not commit the regenerated pins: {made.err or made.out}"


def _ci_detail(repo: Path, e: MQ.Entry, out: O.Outcome, log: Path) -> dict:
    """What a parked entry keeps of a failed ci: the failing hooks and tests by name, the
    last lines of the output, and the log file with all of it."""
    d = out.data
    names = [c["id"] for c in d.get("checks", []) if not c.get("ok")]
    names += [t for t in d.get("failed_tests", []) if t not in names]
    tail = "\n".join((d.get("output_tail") or "").rstrip().splitlines()[-FL.TAIL_LINES :])
    return {
        "failed": names,
        "tail": tail,
        "log": str(log.relative_to(repo)) if log.exists() else "",
    }


def _run_checks(
    repo: Path, cfg, it, e: MQ.Entry, detail: dict, *, unit_tests: bool, timeout_s: int, agent: str
) -> str:
    """The checks the integrator runs once on the merged result: ci, and unit_tests when
    asked. "" when they pass, else why the entry is parked (``detail`` then holds what failed)."""
    if "ci" in GS.pipeline_for(it, cfg):
        log = MQ.log_path(repo, e)
        out = ci_run(repo, ref=it.id, agent=agent, timeout_s=timeout_s, log_to=log, full=True)
        if out.exit != O.OK:
            detail.update(_ci_detail(repo, e, out, log))
            return f"ci {out.data.get('status', 'did not pass')}: {out.reason}"
    if unit_tests and "unit_tests" in GS.pipeline_for(it, cfg):
        out = run_gate(repo, it.id, "unit_tests", agent=agent)
        if out.exit != O.OK or out.data.get("outcome") not in ("", "passed", None):
            return f"unit_tests {out.data.get('outcome', 'did not pass')}: {out.reason}"
    return ""


def _land(repo: Path, e: MQ.Entry, detail: dict, *, unit_tests: bool, timeout_s: int,
          agent: str, where):  # fmt: skip
    """(state, reason, sha) of one entry: the whole of its integration. ``detail`` receives
    what failed (see `_ci_detail`) when it is parked by a check."""
    _log, cfg, st = _load(repo, agent)
    it = st.items.get(e.item)
    if why := refusal(repo, cfg, st, it, unit_tests=unit_tests):
        return MQ.PARKED, why, ""
    wt = W.Worktree(
        item=it.id, path=W.load_path(repo, it.worktree), branch=it.branch,
        base=FS.target(repo, cfg, it, st),
    )  # fmt: skip
    if dirty := W.dirty(wt):
        return MQ.PARKED, f"{len(dirty)} uncommitted file(s) in {wt.path}: {dirty[0]}", ""
    for step in (_bring_in_base, _regenerate_pins):
        if why := step(repo, cfg, wt):
            return MQ.PARKED, why, ""
    if why := _run_checks(
        repo, cfg, it, e, detail, unit_tests=unit_tests, timeout_s=timeout_s, agent=agent
    ):
        return MQ.PARKED, why, ""
    out = land(
        repo, it.id, message=f"merge {it.id}: {it.title}", agent=agent,
        called_from=where[0], shell_cwd=where[1],
    )  # fmt: skip
    if out.exit != O.OK:
        return (
            MQ.PARKED,
            f"merge {out.data.get('id', it.id)} {O.EXIT_NAMES[out.exit]}: {out.reason}",
            "",
        )
    return MQ.MERGED, "", str(out.data.get("sha", ""))


def _one(repo: Path, e: MQ.Entry, **kw) -> dict | None:
    """Integrate one entry; None when it was removed from the line after it was picked."""
    if (e := MQ.take(repo, e)) is None:
        return None
    detail: dict = {}
    try:
        state, reason, sha = _land(repo, e, detail, **kw)
    except Exception as exc:  # one entry's crash parks it; the line goes on
        state, reason, sha = MQ.PARKED, f"the integrator failed on it: {exc!r}", ""
    MQ.finish(repo, e, state, reason=reason, sha=sha, detail=detail)
    return {
        "item": e.item,
        "state": state,
        "reason": reason,
        "sha": sha,
        "took_s": round(e.duration_s, 1),
        "failed": e.failed,
        "tail": e.tail,
        "log": e.log,
    }


def queue_run(
    repo: Path,
    *,
    max_batch: int | None = None,
    unit_tests: bool | None = None,
    timeout_s: int | None = None,
    agent: str = "",
    called_from: Path | None = None,
    shell_cwd: Path | None = None,
) -> O.Outcome:
    """Integrate up to ``max_batch`` queued entries, in order. Exit 0 when each landed, 1
    when any was parked, 2 when there was nothing to do or another integrator holds the lock."""
    log, cfg, _st = _load(repo, agent)
    mq = cfg.merge_queue
    batch = mq.max_batch if max_batch is None else max_batch
    limit = mq.timeout_s if timeout_s is None else timeout_s
    if batch < 1 or limit < 0:
        return O.refused("queue.run", "max_batch must be at least 1 and timeout_s at least 0")
    run_tests = _unit_tests(cfg, unit_tests)
    with MQ.integrator(repo, agent=log.agent_id) as got:
        if not got:
            who = MQ.holder(repo) or {}
            return O.nothing(
                "queue.run",
                f"another integrator is running the queue (pid {who.get('pid', '?')}, "
                f"{who.get('agent', '?')}): it lands the entries in order, so there is nothing "
                f"to do here. `ddflow queue status` shows the line.",
                locked=True, holder=who, processed=[],
            )  # fmt: skip
        MQ.requeue_orphans(repo)
        done: list[dict] = []
        while len(done) < batch and (e := MQ.head(repo)) is not None:
            row = _one(repo, e, unit_tests=run_tests, timeout_s=limit, agent=agent,
                       where=(called_from, shell_cwd))  # fmt: skip
            if row is not None:
                done.append(row)
        if not done:
            return O.nothing("queue.run", "the merge queue is empty", locked=False, processed=[])
    merged = [d for d in done if d["state"] == MQ.MERGED]
    parked = [d for d in done if d["state"] == MQ.PARKED]
    data = {"processed": done, "merged": len(merged), "parked": len(parked), "locked": False,
            "left": len(MQ.live(repo))}  # fmt: skip
    if parked:
        why = "; ".join(f"{d['item']}: {d['reason']}" for d in parked)
        return O.Outcome(kind="queue.run", data=data, exit=O.FAIL,
                         reason=f"{len(merged)} merged, {len(parked)} parked -- {why}")  # fmt: skip
    return O.ok("queue.run", **data)
