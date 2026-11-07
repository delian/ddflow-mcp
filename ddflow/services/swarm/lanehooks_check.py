"""The pre-tool check a lane's private hook runs: may THIS lane write THESE paths (SW-lane-hooks).

One matcher, ``core.globs`` (the one `check-commit` uses); this module reads the lane's own live
lease and nothing else. The hook calls it as ``python -m ddflow.services.swarm.lanehooks_check
--repo R --agent LANE`` with the harness's tool-call JSON on stdin; exit 2 refuses the call and
puts the reason on stderr (the exit-2 block mode of the command-hook dialects, and what the
plugin template turns into a throw). It reads `.ddflow/local/lease-snapshot.json`
(`enforce.hooksnap`), never the event log. It FAILS OPEN: a crash or an unreadable lease allows the
call with one fixed line, because a guard that fails closed on its own bug stops every lane.

Only calls that carry a file path are judged; a shell command carries none, so a write done
through one is caught by the lane's git pre-commit hook (detection, not prevention).
``PROBE_DIR`` is refused whatever the lease says: the hook-fires probe uses it to prove the
door works without needing a lease.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any

from ...core import leasesnap as hooksnap
from ...core.bookkeeping import SELF_MANAGED
from ...core.globs import inside

#: Always refused: the conformance probe's path (never a real project path).
PROBE_DIR = "ddflow-hook-probe"
#: Keys a tool call's input may carry a file path under (Claude, opencode/kilo, Gemini).
PATH_KEYS = ("file_path", "filePath", "path", "notebook_path", "filepath")
REFUSE = 2
NOTE_CRASH = "ddflow lane hook: the lease check could not run; the call is allowed (not checked)."


def paths_of(payload: Any) -> list[str]:
    """File paths named by a tool call: ``tool_input`` (or ``args``) of the payload."""
    if not isinstance(payload, dict):
        return []
    inp = payload.get("tool_input", payload.get("args"))
    if not isinstance(inp, dict):
        return []
    return [str(inp[k]) for k in PATH_KEYS if isinstance(inp.get(k), str) and inp[k]]


def _relative(path: str, cwd: Path, root: Path | None = None) -> str | None:
    """``path`` (resolved against the call's ``cwd``, symlinks followed) relative to the lane
    worktree ``root`` -- where the lease globs are rooted -- or None when it leaves it."""
    root = cwd if root is None else root
    p = Path(path)
    real = os.path.realpath(p if p.is_absolute() else cwd / p)
    rel = Path(os.path.relpath(real, os.path.realpath(root)))
    if rel.parts[:1] == ("..",) or rel.is_absolute():
        return None
    return PurePosixPath(*rel.parts).as_posix()


class SnapshotUnusable(Exception):
    """The lease snapshot cannot be trusted: the call is allowed and the sentence says why."""


def _mine(repo: Path, agent: str) -> tuple[list[str], list[str]]:
    """(globs of the agent's live leases, shared globs) from the lease snapshot, which every
    lease event keeps current (LK-hook-snapshot): milliseconds, where folding the event log
    cost seconds. A missing, torn, newer or stale snapshot raises `SnapshotUnusable`."""
    snap = hooksnap.read(repo)
    if snap.status != hooksnap.OK:
        raise SnapshotUnusable(snap.note)
    globs: list[str] = []
    for lease in snap.live_for(agent, time.time()):
        globs.extend(lease.get("globs", []))
    return globs, snap.shared


def _self_managed(rel: str) -> bool:
    """ddflow's own bookkeeping paths; a directory prefix matches only on its boundary."""
    return any(rel.startswith(m) if m.endswith("/") else rel == m for m in SELF_MANAGED)


def _holder(repo: Path, agent: str, rel: str) -> str:
    """The agent whose live lease covers ``rel`` (never ``agent`` itself), or "" when none is
    in the snapshot or it cannot be read. Cheap: it reads only the snapshot. Fails open: a
    holder it cannot name drops that clause, never the whole refusal."""
    try:
        snap = hooksnap.read(repo)
        if snap.status != hooksnap.OK:
            return ""
        now = time.time()
        for lz in snap.leases.values():
            if lz.get("holder") == agent:
                continue
            if now - float(lz.get("renewed_at", 0)) > int(lz.get("ttl_s", 0)) + snap.grace_s:
                continue
            if any(inside(rel, g) for g in lz.get("globs", [])):
                return str(lz.get("holder", ""))
    except Exception:  # a holder hint is never worth stopping the refusal over
        return ""
    return ""


#: The options a refuse-write names, in the CLI and MCP form the harnesses accept.
OPTIONS = (
    "wait (`ddflow wait --lease <glob>` / MCP `ddflow_wait`), send a suggestion "
    "(`ddflow suggest ...` / MCP `ddflow_suggest`), or open a task "
    "(`ddflow task add ...` / MCP `ddflow_task action=add`)"
)


def verdict(
    repo: Path, agent: str, paths: list[str], cwd: Path, root: Path | None = None
) -> tuple[int, str]:
    """(0, "") to allow; (REFUSE, reason) to refuse. Raises only on what fail-open covers."""
    rels = [(p, _relative(p, cwd, root)) for p in paths]
    for raw, rel in rels:
        if rel is not None and (rel == PROBE_DIR or rel.startswith(PROBE_DIR + "/")):
            return REFUSE, f"ddflow lane hook probe: {raw} is refused on purpose (the hook fired)"
    if not rels:
        return 0, ""
    try:
        mine, shared = _mine(repo, agent)
    except SnapshotUnusable as why:
        return 0, f"{why} The call is allowed (not checked here)."
    if not mine:
        return REFUSE, f"lane {agent} holds no live lease: stop; do not write any file"
    bad = []
    for raw, rel in rels:
        if rel is None:
            bad.append(f"{raw} (outside the lane worktree)")
        elif _self_managed(rel) or any(inside(rel, g) for g in (*mine, *shared)):
            continue
        else:
            bad.append(rel)
    if bad:
        holder = ""
        for rel in bad:
            holder = _holder(repo, agent, rel)
            if holder:
                break
        held = f" and {holder} holds it" if holder else ""
        return REFUSE, (
            f"lane {agent}: {', '.join(bad)} is outside the item's lease globs "
            f"({', '.join(mine)}){held}; {OPTIONS}"
        )
    return 0, ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lanehooks_check")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--worktree", default="", help="the lane worktree (default: the call's cwd)")
    ap.add_argument("--agent", required=True)
    a = ap.parse_args(argv)
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        cwd = Path(payload.get("cwd") or os.getcwd()) if isinstance(payload, dict) else Path.cwd()
        code, msg = verdict(
            Path(a.repo), a.agent, paths_of(payload), cwd, Path(a.worktree) if a.worktree else None
        )
    except Exception:  # never the traceback: it may carry paths or values
        print(NOTE_CRASH, file=sys.stderr)
        return 0
    if msg:
        print(msg, file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
