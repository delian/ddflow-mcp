"""What a reproduction bundle records, one collector per fact (DX-bug-repro-bundle).

Each collector answers a plain dict and never raises: a fact that cannot be had is
`{"unavailable": why}`, because a bundle that says "could not tell" is worth more than a
bug report that is silent about it. Nothing here redacts or caps; `reprobundle` does, over
the whole, so that no collector can forget to.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import version as V
from ..core.model import State
from ..infra import git as G
from ..infra import proc as P
from ..infra import worktree as W

#: A variable whose NAME says it holds a secret is recorded as set, never with its value.
_SECRET_NAME = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH", "COOKIE")
_VALUE_MAX = 400
_GATE_FINDINGS = 10


def _cut(text: object, limit: int = _VALUE_MAX) -> str:
    s = str(text)
    return s if len(s) <= limit else s[:limit] + "...[cut]"


def tail(text: str, nbytes: int) -> str:
    """The last ``nbytes`` bytes of ``text``, on a character boundary."""
    if nbytes <= 0:
        return ""
    raw = text.encode("utf-8", "replace")
    if len(raw) <= nbytes:
        return text
    return "[...]" + raw[-nbytes:].decode("utf-8", "ignore")


def _rel(path: str, repo: Path) -> str:
    """``path`` relative to the project when it lies inside it; else only its last part (a
    path says where; the machine's own directories say nothing a reader needs)."""
    try:
        return str(Path(path).resolve().relative_to(Path(repo).resolve())) or "."
    except (ValueError, OSError):
        return f"<outside the project>/{Path(path).name}"


def tree(repo: Path) -> dict[str, Any]:
    """The ddflow version, the tree this runs in and main: shas, branch, cwd, worktree."""
    try:
        cwd = str(Path.cwd())
    except OSError:
        cwd = ""
    out: dict[str, Any] = {"ddflow_version": V.running(), "python": sys.version.split()[0]}
    here = cwd or str(repo)
    sha = G.run(here, "rev-parse", "HEAD")
    top = G.run(here, "rev-parse", "--show-toplevel")
    branch = G.run(here, "rev-parse", "--abbrev-ref", "HEAD")
    main = W.default_branch(Path(repo))
    main_sha = G.run(repo, "rev-parse", "--verify", "--quiet", main)
    dirty = G.run(here, "status", "--porcelain")
    out.update(
        cwd=_rel(cwd, repo) if cwd else "",
        worktree=_rel(top.out, repo) if top.ok else "",
        branch=branch.out if branch.ok else "",
        tree_sha=sha.out if sha.ok else "",
        main_branch=main,
        main_sha=main_sha.out if main_sha.ok else "",
        dirty_paths=len(dirty.out.splitlines()) if dirty.ok else None,
        in_linked_worktree=bool(top.ok and Path(top.out).resolve() != Path(repo).resolve()),
    )
    if not sha.ok:
        out["unavailable"] = sha.err or "git could not name HEAD"
    return out


def identity(cfg: Config, requested: str) -> dict[str, Any]:
    """The agent id and how it was derived: explicit, env, config or derived."""
    return {
        "id": cfg.agent.id,
        "derived_from": cfg.sources.get("agent.id", "default"),
        "requested_explicitly": bool(requested),
        "DDFLOW_AGENT_set": bool(os.environ.get("DDFLOW_AGENT")),
    }


def environment(cfg: Config) -> dict[str, str]:
    """Names and values of the variables ``[bugs] repro_env`` matches. A secret-named one
    is `[set]`; the others keep their value for the text redaction to scrub."""
    pats = list(cfg.bugs.repro_env)
    out: dict[str, str] = {}
    for name in sorted(os.environ):
        if not any(fnmatch.fnmatchcase(name, p) for p in pats):
            continue
        secret = any(w in name.upper() for w in _SECRET_NAME)
        out[name] = "[set]" if secret else _cut(os.environ[name])
    return out


def config_deltas(cfg: Config) -> dict[str, Any]:
    """Every config key not at its default, with the layer that set it."""
    out: dict[str, Any] = {}
    for key in sorted(cfg.sources):
        layer = cfg.sources[key]
        if layer == "default" or key == "agent.id":
            continue
        sec, _, name = key.partition(".")
        value = getattr(getattr(cfg, sec, None), name, None)
        secret = any(w in key.upper() for w in _SECRET_NAME)
        out[key] = {"layer": layer, "value": "[set]" if secret else _cut(repr(value))}
    return out


def machine(repo: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"cpus": os.cpu_count()}
    try:
        out["load_avg"] = [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        out["load_avg"] = "unavailable"
    try:
        usage = shutil.disk_usage(repo)
        out["disk_free_mb"] = usage.free // (1024 * 1024)
        out["disk_total_mb"] = usage.total // (1024 * 1024)
    except OSError as exc:
        out["disk_free_mb"] = f"unavailable: {exc}"
    return out


def item_state(st: State, item: str, now: float) -> dict[str, Any]:
    """The item, its lease and its gate records and findings, as they were when filed."""
    it = st.items.get(item) if item else None
    if it is None:
        return {"id": item, "unavailable": "not in the queue"} if item else {}
    lease = None
    if it.lease is not None:
        lease = {
            "holder": it.lease.holder,
            "remaining_s": round(it.lease.remaining_s(now)),
            "globs": list(it.lease.globs),
            "worktree": it.lease.worktree,
        }
    gates = {}
    for name, rec in it.gates.items():
        ev = rec.evidence if isinstance(rec.evidence, dict) else {}
        found = ev.get("findings")
        gates[name] = {
            "outcome": str(rec.outcome),
            "at": rec.at,
            "by": rec.by,
            "reason": _cut(rec.reason),
            "findings": found[:_GATE_FINDINGS] if isinstance(found, list) else found,
        }
    return {
        "id": it.id,
        "kind": it.kind,
        "state": it.state,
        "title": _cut(it.title),
        "globs": list(it.globs),
        "branch": it.branch,
        "lease": lease,
        "gates": gates,
    }


def recent_events(events: list[Any], agent: str, n: int) -> list[dict[str, str]]:
    """The last ``n`` log events written by ``agent``: ddflow keeps no log of the commands
    an agent ran, so these stand in for them."""
    if n <= 0:
        return []
    mine = [e for e in events if getattr(e, "agent", "") == agent]
    return [{"ts": e.ts, "kind": e.kind, "subject": e.subject} for e in mine[-n:]]


def test_run(repo: Path, test_id: str, nbytes: int, timeout_s: int) -> dict[str, Any]:
    """Run pytest on ``test_id`` once and keep the head (plugins, seed) and the output tail."""
    # `--` ends pytest's options: an id that starts with a dash is a path to look for, never
    # an option (`--co` would "pass" without running anything, `-p mod` would import `mod`).
    argv = [sys.executable, "-m", "pytest", "--tb=short", "-p", "no:cacheprovider", "--", test_id]
    try:  # the tree the bug was seen in, which a linked worktree makes unlike the project root
        where = str(Path.cwd())
    except OSError:
        where = str(repo)
    got = P.capture(argv, timeout=timeout_s, cwd=where)
    out: dict[str, Any] = {"id": test_id, "command": "pytest --tb=short -- " + test_id}
    if got.error is not None:
        out["unavailable"] = f"pytest did not run to an exit status: {got.error}"
        return out
    text = got.stdout + got.stderr
    lines = text.splitlines()
    out.update(
        exit_code=got.returncode,
        head=lines[:8],
        seed_lines=[x for x in lines if "seed" in x.lower() or "xdist" in x.lower()][:5],
        tail=tail(text, nbytes),
    )
    return out
