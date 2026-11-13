"""The CI result types and the failure kinds of a merge the gate could not do.

Split out of ``ddflow/services/ci.py``: ``Failure``, ``Check`` and ``Result`` -- the
dataclasses every runners path returns -- and the ``kind`` constants callers branch on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import ci_marker as CM
from ..flakes import failure_text

#: What `merge_tree` says of a base that conflicts with the ref, and what `run` reads back to
#: tell that FAILED from a merge git could not finish (UNAVAILABLE): one constant for both.
NOT_MERGEABLE = "does not merge with"
CONFLICT = "conflict"  # Failure.kind: git merged and the branches disagree
CANNOT_MERGE = "cannot-merge"  # Failure.kind: the merge could not be done (timeout, lock, bad ref)


class Failure(str):
    """Why merge_tree has no tree: the prose, plus a structured ``kind`` callers branch on."""

    kind: str

    def __new__(cls, text: str, kind: str) -> Failure:
        obj = super().__new__(cls, text)
        obj.kind = kind
        return obj

    def __getnewargs__(self) -> tuple[str, str]:  # copy / pickle rebuild with the kind
        return (str(self), self.kind)


@dataclass
class Check:
    id: str
    ok: bool
    detail: str = ""


@dataclass
class Result:
    status: str  # passed | failed | unavailable
    checks: list[Check] = field(default_factory=list)
    reason: str = ""
    sha: str = ""
    merged_with: str = ""
    command: str = ""
    output_tail: str = ""
    #: the whole output of the command (not part of `as_data`: it can be megabytes)
    output: str = ""
    #: failing test ids (and their reasons / failure tails) read from ALL of the output
    failures: dict = field(default_factory=dict)
    #: which tests ran (`ci_scope.Plan.evidence`); empty when the command ran as written
    scope: dict = field(default_factory=dict)
    #: seconds queued for the host-wide suite lock; None when the run took no lock (B1d51fb9da1)
    waited_s: float | None = None
    #: seconds the check command itself ran (the processes that decided the verdict, summed
    #: across its stages); None when the run never reached a command (B28537b615d)
    elapsed_s: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    @property
    def exit(self) -> int:
        return {"passed": 0, "failed": 1}.get(self.status, 2)

    def _wait_data(self) -> dict:
        if self.waited_s is None:
            return {}
        return {"waited_s": self.waited_s, "wait_line": CM.wait_line(self.waited_s)}

    def as_data(self) -> dict:
        return {
            "status": self.status,
            "sha": self.sha,
            "merged_with": self.merged_with,
            "command": self.command,
            "why": self.reason,
            "checks": [{"id": c.id, "ok": c.ok, "detail": c.detail} for c in self.checks],
            "output_tail": self.output_tail,
            **self._wait_data(),
            **({"scope": self.scope} if self.scope else {}),
            **self.failures,
            **({"failure_text": failure_text(self.failures)} if self.failures else {}),
        }
