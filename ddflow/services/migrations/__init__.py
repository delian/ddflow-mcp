"""The migration registry and its runner (decision D-compat, task B-uni-compat-migrations).

`register` adds a `Migration` (see `base`); the breaking changes of P-unify register theirs
here and are not implemented ad hoc. `pending` runs every detector and the dry-run plan;
`run` applies the pending ones, each as: detect, plan, BACK UP the files the plan names,
apply, append the corrective events, then check the result -- a fresh detect finds nothing
and `verify` reports no problem. A migration whose check fails is reported `failed` with
what remains, never `applied`; one whose detector (or apply, before it wrote) could not run
is `unavailable`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ... import FORMAT_LEVEL
from ...config import Config
from ...core.events import is_older, version_key
from ...infra.log import EventLog, running_version
from ..backups import make_backup
from .base import (
    AGENT,
    KINDS,
    OPERATOR,
    Change,
    Context,
    Corrective,
    Finding,
    Migration,
    Unavailable,
    context,
)

__all__ = [
    "AGENT",
    "OPERATOR",
    "Change",
    "Context",
    "Corrective",
    "Finding",
    "Migration",
    "Outcome",
    "Pending",
    "Unavailable",
    "by_id",
    "context",
    "pending",
    "register",
    "registry",
    "run",
]

APPLIED = "applied"
FAILED = "failed"
UNAVAILABLE = "unavailable"

_REGISTRY: list[Migration] = []


def register(m: Migration) -> Migration:
    """Add ``m`` to the registry. Refused (ValueError) for a duplicate id, a version that is
    not a release number, a format level the code does not know or an unknown artifact kind:
    a migration the plan cannot place is a bug at import time, not at upgrade time."""
    if any(x.id == m.id for x in _REGISTRY):
        raise ValueError(f"migration {m.id!r} is already registered")
    if not version_key(m.since_version):
        raise ValueError(f"migration {m.id!r}: since_version {m.since_version!r} is not a version")
    if not 1 <= m.format_level <= FORMAT_LEVEL:
        raise ValueError(
            f"migration {m.id!r}: format_level {m.format_level} is outside 1..{FORMAT_LEVEL}"
        )
    bad = [k for k in m.kinds if k not in KINDS]
    if bad or not m.kinds:
        raise ValueError(
            f"migration {m.id!r}: kinds {m.kinds!r} must be a subset of {sorted(KINDS)}"
        )
    if m.consent not in (AGENT, OPERATOR):
        raise ValueError(f"migration {m.id!r}: unknown consent {m.consent!r}")
    _REGISTRY.append(m)
    return m


def registry() -> list[Migration]:
    """Every registered migration, in registration order (the order they are applied)."""
    return list(_REGISTRY)


def by_id(mid: str) -> Migration:
    for m in _REGISTRY:
        if m.id == mid:
            return m
    raise KeyError(f"unknown migration {mid!r}; known: {', '.join(m.id for m in _REGISTRY)}")


@dataclass
class Pending:
    migration: Migration
    findings: list[Finding] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    #: Why the detector could not run; "" when it did.
    unavailable: str = ""

    def files(self, repo: Path) -> list[Path]:
        """The files the plan rewrites, absolute: what a backup must hold first."""
        return [Path(repo) / c.path for c in self.changes]


@dataclass
class Outcome:
    migration: str
    status: str
    detail: str
    findings: int = 0
    backup: str = ""
    problems: list[str] = field(default_factory=list)

    def data(self) -> dict[str, Any]:
        return {
            "migration": self.migration,
            "status": self.status,
            "detail": self.detail,
            "findings": self.findings,
            "backup": self.backup,
            "problems": list(self.problems),
        }


def _offered(m: Migration, running: str) -> bool:
    return not is_older(running, m.since_version)


def _detect(ctx: Context, m: Migration) -> Pending:
    try:
        findings = m.detect(ctx)
        changes = m.plan(ctx, findings) if findings else []
    except Unavailable as exc:
        return Pending(m, unavailable=str(exc) or "the detector could not run")
    return Pending(m, findings, changes)


def pending(ctx: Context, ids: Iterable[str] | None = None, *, running: str = "") -> list[Pending]:
    """The migrations with something to do (or whose detector could not run), in registry
    order, each with its dry-run plan. Only reads. A migration of a release newer than the
    running ddflow is not offered; ``ids`` narrows to the named ones (KeyError if unknown)."""
    running = running or running_version()
    wanted = None if ids is None else {by_id(i).id for i in ids}
    out = [
        _detect(ctx, m)
        for m in _REGISTRY
        if _offered(m, running) and (wanted is None or m.id in wanted)
    ]
    return [p for p in out if p.findings or p.unavailable]


def run(
    repo: Path,
    log: EventLog,
    cfg: Config,
    ids: Sequence[str] | None = None,
    *,
    backup: Callable[[list[Path]], str] | None = None,
    running: str = "",
) -> list[Outcome]:
    """Apply the pending migrations: every `agent`-consent one, or exactly the named ones
    (an `operator`-consent migration runs only when named). ``backup`` receives the files a
    migration's plan names and returns where it saved them (default: a local backup under
    `.ddflow/backups`); it raises OSError when it cannot, and the migration then writes
    NOTHING (reported `failed`). One outcome per
    migration that had something to do, in registry order. Raises KeyError for an unknown id."""
    running = running or running_version()
    named = None if ids is None else {by_id(i).id for i in ids}
    out: list[Outcome] = []
    for m in _REGISTRY:
        if not _offered(m, running):
            continue
        if (named is None and m.consent != AGENT) or (named is not None and m.id not in named):
            continue
        # Read, decide and append under the log's lock: two agents migrating at once must
        # not both apply the same step.
        try:
            with log.transaction():
                outcome = _run_one(m, context(repo, log, cfg), backup)
        except (OSError, ValueError) as exc:
            # A detector that raises: this migration is `failed`, and the ones that already
            # ran keep their outcomes -- the loop goes on.
            outcome = Outcome(m.id, FAILED, f"{type(exc).__name__}: {exc}")
        if outcome:
            out.append(outcome)
    return out


def _default_backup(repo: Path) -> Callable[[list[Path]], str]:
    """The backup used when the caller passes none: every migration saves the files its plan
    names first (`.ddflow/backups`, local), so none rewrites a file without a copy."""

    def save(files: list[Path]) -> str:
        return str(make_backup(repo, files, "migrate", running_version()))

    return save


def _run_one(
    m: Migration, ctx: Context, backup: Callable[[list[Path]], str] | None
) -> Outcome | None:
    p = _detect(ctx, m)
    if p.unavailable:
        return Outcome(m.id, UNAVAILABLE, f"could not run: {p.unavailable}")
    if not p.findings:
        return None
    where = ""
    files = p.files(ctx.repo)
    if files:
        try:
            where = (backup or _default_backup(ctx.repo))(files)
        except OSError as exc:
            return Outcome(
                m.id,
                FAILED,
                f"no backup could be written ({exc}); nothing was changed",
                len(p.findings),
            )
    try:
        corrective = m.apply(ctx, p.findings)
    except Unavailable as exc:
        # `apply` raises it only BEFORE it writes anything (see `Migration`): nothing ran.
        return Outcome(m.id, UNAVAILABLE, f"could not run: {exc}", len(p.findings), where)
    except (OSError, ValueError) as exc:
        return Outcome(m.id, FAILED, f"{type(exc).__name__}: {exc}", len(p.findings), where)
    try:
        for kind, subject, data in corrective:
            ctx.log.append(kind, subject, data)
        after = context(ctx.repo, ctx.log, ctx.cfg)
        left = [f.detail for f in m.detect(after)]
        problems = left + list(m.verify(after))
    except Unavailable as exc:
        # Past the apply: files are written and events appended, so this is never "nothing
        # happened" -- the migration ran and its result is not confirmed.
        return Outcome(
            m.id, FAILED, f"ran, but could not be confirmed: {exc}", len(p.findings), where
        )
    except (OSError, ValueError) as exc:
        return Outcome(m.id, FAILED, f"{type(exc).__name__}: {exc}", len(p.findings), where)
    if problems:
        return Outcome(
            m.id,
            FAILED,
            f"applied, but {len(problems)} problem(s) remain",
            len(p.findings),
            where,
            problems,
        )
    return Outcome(m.id, APPLIED, f"{len(p.findings)} finding(s) migrated", len(p.findings), where)
