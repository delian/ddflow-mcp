"""The pre-tool check a lane's private hook runs: may THIS lane write THESE paths (SW-lane-hooks).

One matcher, ``core.globs`` (the one `check-commit` uses); this module reads the lane's own live
lease and nothing else. The hook calls it as ``python -m ddflow.services.swarm.lanehooks_check
--repo R --agent LANE`` with the harness's tool-call JSON on stdin; exit 2 refuses the call and
puts the reason on stderr (the exit-2 block mode of the command-hook dialects, and what the
plugin template turns into a throw). It FAILS OPEN: a crash or an unreadable lease allows the
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

from ...config import Config
from ...core.bookkeeping import SELF_MANAGED
from ...core.globs import inside
from ...core.model import fold
from ...core.schedule import shared_globs
from ...infra.log import EventLog
from .. import leases
from ..leaseclock import localize

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


def _relative(path: str, cwd: Path) -> str | None:
    """``path`` relative to the lane worktree, or None when it leaves it."""
    p = Path(path)
    p = p if p.is_absolute() else cwd / p
    try:
        rel = Path(os.path.normpath(p)).relative_to(os.path.normpath(cwd))
    except ValueError:
        return None
    return PurePosixPath(*rel.parts).as_posix()


def _mine(repo: Path, agent: str) -> tuple[list[str], list[str]]:
    """(globs of the agent's live leases, shared globs) from the project's event log."""
    cfg = Config.load(repo)
    log = EventLog(repo, agent, log_cfg=cfg.log)
    now = time.time()
    state = localize(fold(log.read_all(), strict=False), log, now)
    globs: list[str] = []
    for lease in leases.unfinished_leases(state, cfg, now).values():
        if lease.holder == agent:
            globs.extend(lease.globs)
    return globs, list(shared_globs(cfg))


def verdict(repo: Path, agent: str, paths: list[str], cwd: Path) -> tuple[int, str]:
    """(0, "") to allow; (REFUSE, reason) to refuse. Raises only on what fail-open covers."""
    rels = [(p, _relative(p, cwd)) for p in paths]
    for raw, rel in rels:
        if rel is not None and (rel == PROBE_DIR or rel.startswith(PROBE_DIR + "/")):
            return REFUSE, f"ddflow lane hook probe: {raw} is refused on purpose (the hook fired)"
    if not rels:
        return 0, ""
    mine, shared = _mine(repo, agent)
    if not mine:
        return REFUSE, f"lane {agent} holds no live lease: stop; do not write any file"
    bad = []
    for raw, rel in rels:
        if rel is None:
            bad.append(f"{raw} (outside the lane worktree)")
        elif rel.startswith(SELF_MANAGED) or any(inside(rel, g) for g in (*mine, *shared)):
            continue
        else:
            bad.append(rel)
    if bad:
        return REFUSE, (
            f"lane {agent}: {', '.join(bad)} is outside the item's lease globs "
            f"({', '.join(mine)}); do not edit it, or ask the lead to widen the globs"
        )
    return 0, ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lanehooks_check")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--agent", required=True)
    a = ap.parse_args(argv)
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        cwd = Path(payload.get("cwd") or os.getcwd()) if isinstance(payload, dict) else Path.cwd()
        code, msg = verdict(Path(a.repo), a.agent, paths_of(payload), cwd)
    except Exception:  # never the traceback: it may carry paths or values
        print(NOTE_CRASH, file=sys.stderr)
        return 0
    if msg:
        print(msg, file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
