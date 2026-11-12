"""Baseline results kept per base sha, by test id (GM-baseline-store).

ci and unit_tests ask `baseline_for` how the base fails instead of running a clean-main
baseline each time. One entry per base sha AND gate command lives in
``.ddflow/local/baseline-store/`` (per project, git-ignored): the ids that failed, each with a
digest of its failure text, and whether the measurement was ``complete``.

* A sha not in the store is measured once, under the suite lock when one is given, and the
  store is read again after the lock is won: a run that waited finds what the holder stored.
* A partial entry (the run was cut short) is never complete: the next ask measures again.
* An id the caller marks ``worktree_only`` (it fails only for being in a worktree, see
  GM-lane-testcapable-doctor) is not stored: it is not the base's failure.
* An entry older than ``[baseline].keep`` days is pruned when the store is written; 0 keeps
  nothing.

The comparer that decides new-versus-baseline stays `gates.baseline_red`; this is only the
memory it can read.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from contextlib import AbstractContextManager as ContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..adapters.clock import DEFAULT as CLOCK
from ..core.digest import content_digest
from ..infra import fsio
from ..infra.tomlcfg import atomic_write
from . import suitelock
from .ci.failure_text import digest as tree_digest

STORE_DIR = Path(".ddflow") / "local" / "baseline-store"
DAY_S = 86400.0


@dataclass(frozen=True)
class Measured:
    """What one run of the base found: ``failures`` is ``{test id: failure text}``."""

    failures: dict[str, str]
    complete: bool = True
    worktree_only: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Baseline:
    """A base sha's stored result: ``failing`` is ``{test id: failure digest}``."""

    sha: str
    complete: bool
    measured_at: float
    failing: dict[str, str] = field(default_factory=dict)
    cached: bool = False


def digest_of(text: str) -> str:
    """The digest a failure text is compared by: the whole text with the ci tree's path blanked
    (`ci.failure_text`, GM-ci-path-normalise), so one failure from two trees has one digest."""
    return tree_digest(text)


def store_dir(repo: Path) -> Path:
    return Path(repo) / STORE_DIR


def _path(repo: Path, sha: str, command: str) -> Path:
    return store_dir(repo) / f"{sha[:40]}.{content_digest(command)[:12]}.json"


def _fresh(measured_at: float, keep_days: int, now: float) -> bool:
    return keep_days > 0 and now - measured_at <= keep_days * DAY_S


def _read(path: Path, sha: str) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("sha") != sha:
        return None
    if not isinstance(raw.get("tests"), dict) or not isinstance(
        raw.get("measured_at"), int | float
    ):
        return None
    return raw


def load(
    repo: Path, sha: str, command: str, *, keep_days: int, now: float | None = None
) -> Baseline | None:
    """The stored baseline of ``sha``/``command``, or None (absent, torn, foreign, expired)."""
    clock = CLOCK.wall() if now is None else now
    raw = _read(_path(repo, sha, command), sha)
    if raw is None or not _fresh(float(raw["measured_at"]), keep_days, clock):
        return None
    failing = {str(k): str(v) for k, v in raw["tests"].items()}
    return Baseline(sha, bool(raw.get("complete")), float(raw["measured_at"]), failing, True)


def prune(repo: Path, keep_days: int, now: float | None = None) -> int:
    """Delete entries older than ``keep_days`` (all of them for 0); how many went."""
    clock = CLOCK.wall() if now is None else now
    gone = 0
    for path in store_dir(repo).glob("*.json"):
        try:
            at = float(json.loads(path.read_text(encoding="utf-8")).get("measured_at", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            at = 0.0
        if not _fresh(at, keep_days, clock):
            with contextlib.suppress(OSError):
                path.unlink()
                gone += 1
    return gone


def _store(
    repo: Path, sha: str, command: str, got: Measured, keep_days: int, now: float
) -> Baseline:
    failing = {
        i: digest_of(text) for i, text in sorted(got.failures.items()) if i not in got.worktree_only
    }
    try:
        if keep_days > 0:
            path = _path(repo, sha, command)
            path.parent.mkdir(parents=True, exist_ok=True)
            fsio.ensure_ignored_dir(path.parent)
            body = {"sha": sha, "complete": got.complete, "measured_at": now, "tests": failing}
            atomic_write(path, json.dumps(body))
        prune(repo, keep_days, now)  # also with keep 0: "keeps nothing" removes what was kept
    except OSError:
        pass  # best effort: a failed write costs a re-measure
    return Baseline(sha, got.complete, now, failing, False)


def baseline_for(
    repo: Path,
    sha: str,
    command: str,
    measure: Callable[[], Measured],
    *,
    keep_days: int,
    now: float | None = None,
    lock: Callable[[], ContextManager[Any]] | None = None,
) -> Baseline:
    """The baseline of ``sha``: the stored complete one, else ``measure()`` once (under
    ``lock()`` when given, reading the store again once it is held) and stored."""
    clock = CLOCK.wall() if now is None else now
    held = load(repo, sha, command, keep_days=keep_days, now=clock)
    if held is not None and held.complete:
        return held
    with (lock or contextlib.nullcontext)():
        if lock is not None:
            held = load(repo, sha, command, keep_days=keep_days, now=clock)
            if held is not None and held.complete:
                return held
        return _store(repo, sha, command, measure(), keep_days, clock)


def baseline_for_cfg(
    repo: Path,
    cfg: Any,
    sha: str,
    command: str,
    measure: Callable[[], Measured],
    holder: str = "baseline-store",
) -> Baseline:
    """`baseline_for` as the gates call it: ``[baseline].keep`` bounds the store and a missing
    sha is measured under the host suite lock (`services.suitelock`), so two lanes that need
    the same base at once measure it once."""
    return baseline_for(
        repo,
        sha,
        command,
        measure,
        keep_days=int(cfg.baseline.keep),
        lock=lambda: suitelock.hold(cfg, holder),
    )
