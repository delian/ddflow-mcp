"""A content-addressed cache of ci / unit_tests results (RC-tree-result-cache).

The key names everything a result depends on: the CODE tree (tracked files, a dirty tree by its
content, minus ``.ddflow/`` and ``[gates].doc_globs`` documents), the gate, the resolved command,
the Python version, the dependency lock files and ``[resultcache].env_vars``. Anything that does
not read back whole is a miss; only ``passed`` and ``failed`` are stored; a ``failed`` hit is
reported failed. The store is per project, under the git-ignored ``.ddflow/local`` of the
PRIMARY checkout (worktrees of one project share it), bounded by ``keep_days`` and ``max_mb``."""

from __future__ import annotations

import contextlib
import functools
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from ..adapters.digest import hasher
from ..core import bookkeeping
from ..core.globs import inside
from ..infra import fsio
from ..infra import git as G
from ..infra import worktree as W
from .cmdrunner import CommandRunner, Declared

DIR = ("resultcache",)
STORED = ("passed", "failed")
GATES = ("ci", "unit_tests")
LOCKFILES = (
    "uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt", "package-lock.json",
    "pnpm-lock.yaml", "yarn.lock", "Cargo.lock", "go.sum",
)  # fmt: skip
#: Evidence that names a log on the machine of the run: a hit cites the source run instead.
DROPPED = ("output_log", "output_log_host", "output_log_error")
MB = 1024 * 1024
DAY_S = 86400.0


def _python() -> str:
    return sys.version


def _digest(*parts: str) -> str:
    h = hasher(b"")
    for p in parts:
        h.update(p.encode("utf-8", "replace") + b"\0")
    return h.hexdigest()


def _code_entries(cwd: Path) -> dict[str, str] | None:
    """path -> blob id of the code the tree holds now: HEAD's entries with the worktree's edits,
    deletions and untracked files laid over them. None when git cannot say."""
    try:
        entries = {e[2]: f"{e[0]} {e[1]}" for e in G.tree_entries(cwd, "HEAD")}
    except G.GitError:
        return None
    changed = G.paths(cwd, "diff", "HEAD", "--name-only")
    untracked = G.paths(cwd, "ls-files", "--others", "--exclude-standard")
    if changed is None or untracked is None:
        return None
    for rel in {*changed, *untracked}:
        path = cwd / rel
        if not os.path.lexists(path):  # a broken symlink is still something
            entries.pop(rel, None)
            continue
        if path.is_dir() and (path / ".git").exists():  # a submodule: the commit it is at now
            head = G.run(path, "rev-parse", "HEAD")
            if not head.ok or not head.out.strip():
                return None
            dirt = _submodule_dirt(path)
            if dirt is None:
                return None
            entries[rel] = f"submodule {head.out.strip()} {dirt}"
            continue
        if path.is_symlink():  # the link itself is the code, not what it points at
            entries[rel] = f"symlink {_digest(os.readlink(path))}"
            continue
        blob = G.run(cwd, "hash-object", "--", rel)
        if not blob.ok or not blob.out.strip():
            return None
        mode = "x" if os.lstat(path).st_mode & 0o111 else "-"  # the mode is part of the code
        entries[rel] = f"worktree {mode} {blob.out.strip()}"
    return entries


def code_key(cwd: Path, doc_globs: list[str]) -> str | None:
    entries = _code_entries(Path(cwd))
    if entries is None:
        return None
    rows = sorted(
        f"{rel} {blob}"
        for rel, blob in entries.items()
        if not bookkeeping.is_state(rel) and not any(inside(rel, g) for g in doc_globs)
    )
    return _digest(*rows)


def _submodule_dirt(path: Path) -> str | None:
    """A digest of a submodule's uncommitted edits and untracked files ('' when clean)."""
    diff = G.run(path, "diff", "HEAD")
    others = G.paths(path, "ls-files", "--others", "--exclude-standard")
    if not diff.ok or others is None:
        return None
    parts = []
    for rel in sorted(others):
        try:
            parts.append(f"{rel}={_digest((path / rel).read_bytes().decode('utf-8', 'replace'))}")
        except OSError:
            return None
    return _digest(diff.out, *parts) if diff.out or parts else ""


@functools.cache
def _probe(command: str, cwd: str) -> str:
    """What ``command`` prints (a tool's version) in ``cwd``, or ``unavailable`` when it cannot
    run. Remembered per process for that command and project."""
    p = CommandRunner().run(
        Declared(command, "[resultcache].probe_commands"), cwd=cwd or None, timeout_s=10
    )
    return _digest(p.output) if p.ran and p.code == 0 else "unavailable"


def _env(cwd: Path, cfg: Any) -> str | None:
    """Digest of the environment a result depends on; None when a lock file that exists cannot
    be read (then nothing is cached: no guess)."""
    locks = []
    for name in LOCKFILES:
        p = Path(cwd) / name
        if not p.is_file():
            continue
        try:
            locks.append(f"{name}={_digest(p.read_text('utf-8', 'replace'))}")
        except OSError:
            return None
    tools = [f"{c}={_probe(c, str(cwd))}" for c in cfg.resultcache.probe_commands]
    envs = [f"{n}={os.environ.get(n, '')}" for n in sorted(cfg.resultcache.env_vars)]
    return _digest(_python(), sys.platform, *locks, *tools, *envs)


def key(gate: str, command: str, cwd: Path, cfg: Any) -> str | None:
    """The cache key of ``gate`` running ``command`` in ``cwd``; None when caching is off, the
    gate is not cached, or the tree cannot be read (never a guess)."""
    if not cfg.resultcache.enabled or gate not in GATES or not command:
        return None
    tree = code_key(Path(cwd), list(cfg.gates.doc_globs or []))
    if tree is None:
        return None
    extra = ""
    if gate == "ci":  # a ci result is about the merge with the base: its tip is part of the key
        extra = W.rev(Path(cwd), W.default_branch(Path(cwd))) or ""
    env = _env(cwd, cfg)
    if env is None:
        return None
    return _digest("v1", gate, command, tree, env, extra, ",".join(cfg.ci.always_tests))


def _dir(cwd: Path) -> Path:
    return W.repo_root(Path(cwd)) / ".ddflow" / "local" / DIR[0]


def entry_path(cwd: Path, key_: str) -> Path:
    return _dir(cwd) / f"{key_}.json"


def lookup(cwd: Path, key_: str | None, cfg: Any) -> dict[str, Any] | None:
    """The stored entry for ``key_``, or None. An entry that is unreadable, not whole, names
    another key, an outcome the cache does not store, or is older than ``keep_days`` is a miss
    (and removed)."""
    if not key_ or not cfg.resultcache.enabled:
        return None
    path = entry_path(cwd, key_)
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        data = None
    ok = (
        isinstance(data, dict)
        and data.get("key") == key_
        and data.get("outcome") in STORED
        and isinstance(data.get("evidence"), dict)
        and isinstance(data.get("created"), int | float)
        and time.time() - float(data["created"]) <= cfg.resultcache.keep_days * DAY_S
    )
    if not ok:
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    if data["outcome"] == "failed" and not cfg.resultcache.reuse_failed:
        return None  # kept for the record, not served: a rerun tells a flake from a failure
    return data


def store(cwd: Path, key_: str | None, outcome: str, evidence: dict[str, Any], cfg: Any) -> None:
    """Remember a ``passed`` or ``failed`` result; anything else is not stored. Best effort: a
    failed write costs a rerun."""
    if not key_ or outcome not in STORED or not cfg.resultcache.enabled:
        return
    body = {
        "key": key_,
        "outcome": outcome,
        "created": time.time(),
        "evidence": {k: v for k, v in evidence.items() if k not in DROPPED},
    }
    path = entry_path(cwd, key_)
    with contextlib.suppress(OSError, TypeError, ValueError):
        path.parent.mkdir(parents=True, exist_ok=True)
        fsio.replace_text(path, json.dumps(body, default=str), fsync=False)
        prune(cwd, cfg)


def prune(cwd: Path, cfg: Any) -> None:
    """Remove entries past ``keep_days``, then the oldest until the cache fits ``max_mb``."""
    d = _dir(cwd)
    now = time.time()
    rows = []
    for p in d.glob("*.json"):
        with contextlib.suppress(OSError):
            st = p.stat()
            if now - st.st_mtime > cfg.resultcache.keep_days * DAY_S:
                p.unlink()
            else:
                rows.append((st.st_mtime, st.st_size, p))
    total = sum(r[1] for r in rows)
    for _mtime, size, p in sorted(rows):
        if total <= cfg.resultcache.max_mb * MB:
            break
        with contextlib.suppress(OSError):
            p.unlink()
            total -= size


def hit_evidence(entry: dict[str, Any], key_: str) -> dict[str, Any]:
    """The evidence of a gate run served from the cache: the stored evidence with its own time
    spent set to 0 (the original in ``cached_elapsed_s``) and the source named."""
    ev = {k: v for k, v in entry["evidence"].items() if k not in DROPPED}
    ran = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(entry["created"])))
    return {
        **ev,
        "elapsed_s": 0.0,
        "cached_elapsed_s": ev.get("elapsed_s"),
        "cached": True,
        "cache_key": key_[:16],
        "note": f"cached from run {key_[:12]} of {ran}",
    }
