"""Checks: the engine's one way to hold guidance to account (B-uni-guidance-enforce).

A rule ("every public function has a docstring") and a decision ("no library X") both carry
``checks`` (`GuidanceRecord.checks`): small tables, each naming a ``kind`` and that kind's own
fields. This is the plumbing every concrete check kind plugs into; P-decisions registers the
kinds (deps, imports, pattern, files, config, command, radar), the engine neither knows nor
cares what they look at:

* an EVALUATOR is a pure function ``(check, ctx) -> list[Finding]`` registered under its kind
  (`register`); no finding is a pass, any finding a fail;
* `run_record` runs every check of one record and returns one `CheckResult` per check. A check
  that cannot be evaluated -- no such kind, a malformed table, an evaluator that raised or
  that said it could not run (`Unavailable`) -- is ``unavailable``, NEVER a pass: a gate
  that quietly dropped a check it could not run would report green for work nobody checked;
* `gate_verdict` is the gate hook: the results of every check that governs an item, with the
  enforcement of each record, folded into the one outcome the gate records. Waivers
  (`waivers.apply`) turn findings into ``waived`` BEFORE this, and say so in the verdict.

Pure: an evaluator reads the tree through ``ctx``; this module does no I/O of its own.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core import digest as DG
from ...core import globs as G
from .record import GuidanceRecord

PASS, FAIL, UNAVAILABLE, WAIVED = "pass", "fail", "unavailable", "waived"
RESULT_STATUSES = (PASS, FAIL, UNAVAILABLE, WAIVED)

#: The gate outcomes `gate_verdict` answers, as `gate record --outcome` spells them.
PASSED, FAILED = "passed", "failed"


class Unavailable(Exception):
    """Raised by an evaluator that cannot run (a tool it needs is not installed, a file it
    must read is missing): the check is reported unavailable with this message, not passed."""


@dataclass(frozen=True)
class Finding:
    """One violation, with where it is."""

    path: str
    message: str
    line: int = 0

    def where(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


@dataclass(frozen=True)
class CheckContext:
    """What an evaluator may look at: the repository, and the repo-relative paths to check
    (the whole tracked tree, or the files of a diff). ``paths`` is already narrowed to the
    record's scope globs by `run_record`."""

    repo: Path
    paths: tuple[str, ...] = ()
    #: Whether ``paths`` is a diff (checks may then report only what the change introduced).
    diff: bool = False


Evaluator = Callable[[dict[str, Any], CheckContext], list[Finding]]
_KINDS: dict[str, Evaluator] = {}


def register(kind: str, evaluator: Evaluator) -> None:
    """Make ``kind`` a check kind. A second registration of a name is an error: two
    evaluators for one kind would make a record's verdict depend on import order."""
    if not kind or not kind.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"check kind {kind!r} must be letters, digits, '_' or '-'")
    if kind in _KINDS:
        raise ValueError(f"check kind {kind!r} is already registered")
    _KINDS[kind] = evaluator


def kinds() -> list[str]:
    return sorted(_KINDS)


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one check of one record."""

    record: str
    check_id: str
    kind: str
    status: str
    findings: tuple[Finding, ...] = ()
    #: Findings a waiver covered (status ``waived`` when every finding was).
    waived: tuple[Finding, ...] = ()
    #: Why it is unavailable; "" otherwise.
    reason: str = ""
    #: The ids of the waivers that covered findings.
    waivers: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        """Equal outcomes, equal digest: what a cache keyed by the tree fingerprint stores."""
        parts = [self.record, self.check_id, self.status, self.reason]
        parts += [f"{f.path}:{f.line}:{f.message}" for f in self.findings]
        return DG.content_digest("\n".join(parts), "sha256", length=16)


def check_id(rec: GuidanceRecord, index: int, check: dict[str, Any]) -> str:
    """The id a check goes by: its own ``id``, else ``<record>#<position>``."""
    given = check.get("id")
    return given if isinstance(given, str) and given else f"{rec.id}#{index + 1}"


def lint_check(check: Any) -> list[str]:
    """Why ``check`` is not a usable check table, one sentence each; [] when it is."""
    if not isinstance(check, dict):
        return [f"a check is a table with a kind, got {type(check).__name__}"]
    out = []
    kind = check.get("kind")
    if not isinstance(kind, str) or not kind:
        out.append("a check needs a kind")
    if "id" in check and not (isinstance(check["id"], str) and check["id"]):
        out.append("a check id must be a non-empty string")
    return out


def in_scope(rec: GuidanceRecord, paths: Iterable[str]) -> tuple[str, ...]:
    """``paths`` narrowed to the record's globs; all of them when it has none."""
    paths = tuple(paths)
    globs = rec.scope.globs
    if not globs:
        return paths
    return tuple(p for p in paths if any(G.inside(p, g) for g in globs))


def _unavailable(rec: GuidanceRecord, cid: str, kind: str, why: str) -> CheckResult:
    return CheckResult(rec.id, cid, kind, UNAVAILABLE, reason=why)


def run_check(rec: GuidanceRecord, index: int, ctx: CheckContext) -> CheckResult:
    """Evaluate check ``index`` of ``rec`` over ``ctx``. Never raises: whatever stops a check
    from giving an answer is ``unavailable`` with the reason."""
    check = rec.checks[index]
    cid = check_id(rec, index, check) if isinstance(check, dict) else f"{rec.id}#{index + 1}"
    bad = lint_check(check)
    if bad:
        return _unavailable(rec, cid, "", "; ".join(bad))
    kind = check["kind"]
    evaluator = _KINDS.get(kind)
    if evaluator is None:
        return _unavailable(rec, cid, kind, f"no check kind {kind!r} is registered here")
    scoped = CheckContext(ctx.repo, in_scope(rec, ctx.paths), ctx.diff)
    try:
        found = evaluator(check, scoped)
    except Unavailable as exc:
        return _unavailable(rec, cid, kind, str(exc) or "the check could not run")
    except Exception as exc:  # an evaluator bug must not read as a pass, nor stop the rest
        return _unavailable(rec, cid, kind, f"{type(exc).__name__}: {exc}")
    if not isinstance(found, list) or not all(isinstance(f, Finding) for f in found):
        return _unavailable(rec, cid, kind, "the evaluator did not return a list of findings")
    return CheckResult(rec.id, cid, kind, FAIL if found else PASS, tuple(found))


def run_record(rec: GuidanceRecord, ctx: CheckContext) -> list[CheckResult]:
    """Every check of ``rec``, in order. A record without checks has no results; only LIVE
    guidance is held to its checks."""
    if not rec.live:
        return []
    return [run_check(rec, i, ctx) for i in range(len(rec.checks))]


@dataclass
class Verdict:
    """The gate hook's answer for the guidance that governs one item."""

    outcome: str  # passed | failed | unavailable
    #: Failing results of ``block`` records: the reasons the gate fails.
    blocking: list[CheckResult] = field(default_factory=list)
    #: Failing results of ``warn`` records: shown in gate status, brief and review.
    warnings: list[CheckResult] = field(default_factory=list)
    #: Failing results of ``advisory`` records.
    notes: list[CheckResult] = field(default_factory=list)
    unavailable: list[CheckResult] = field(default_factory=list)
    #: Results where a waiver covered every finding or part of them: visible, never silent.
    waived: list[CheckResult] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{len(x)} {name}"
            for name, x in (
                ("blocking", self.blocking),
                ("warning", self.warnings),
                ("note", self.notes),
                ("unavailable", self.unavailable),
                ("waived", self.waived),
            )
            if x
        ]
        return ", ".join(parts) or "no findings"


def gate_verdict(records: Iterable[GuidanceRecord], results: Iterable[CheckResult]) -> Verdict:
    """Fold ``results`` into the one outcome a gate records. ``failed`` when a ``block``
    record has a failing check; else ``unavailable`` when any check could not run (a check
    that did not run is a coverage gap, not a pass); else ``passed``. Warnings, notes and
    waived findings never change the outcome but are always carried."""
    level = {r.id: r.enforcement for r in records}
    v = Verdict(PASSED)
    for res in results:
        if res.waived:
            v.waived.append(res)
        if res.status == UNAVAILABLE:
            v.unavailable.append(res)
        elif res.status == FAIL:
            how = level.get(res.record, "advisory")
            (v.blocking if how == "block" else v.warnings if how == "warn" else v.notes).append(res)
    v.outcome = FAILED if v.blocking else "unavailable" if v.unavailable else PASSED
    return v
