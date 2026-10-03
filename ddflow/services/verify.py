"""`ddflow verify`: re-derive the claims behind a `done` mark from the log and the repository.

A completion is a claim -- "this landed, it was tested, its gates held" -- and a claim made
by whoever completed it is only as good as that agent. This module computes each part
again, from the completion ledger (`services/ledger.py`) and git, and says which parts hold:

* it LANDED: the recorded commit exists and is on an integration branch;
* the files it DECLARED exist (an exact path that never existed is the false positive);
* it touched something inside what it declared, and a test when it changed code;
* the tests it names exist, and a closed bug has a regression test that exists;
* every gate carries an honest outcome (a skip has a reason, nothing failed, nothing
  required is missing);
* what it changed was not removed afterwards, and its requirement was not rewritten.

Each check answers ok / warn / fail / unknown. "Could not tell" is never reported as ok:
a ledger with no recorded files is `unknown`, not a pass.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.events import Event
from ..core.model import State
from ..core.schedule import conflicts
from . import ledger as LG

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"
_WILD = re.compile(r"[*?\[]")
_DOCS = re.compile(r"\.(md|rst|txt|json|toml|ya?ml|lock|csv)$|(^|/)docs?/", re.I)
SHOWN = 6


@dataclass
class Claim:
    id: str
    status: str
    detail: str


@dataclass
class Report:
    item: str
    claims: list[Claim] = field(default_factory=list)
    completed: bool = True

    @property
    def verdict(self) -> str:
        if not self.completed:
            return "not completed"
        statuses = {c.status for c in self.claims}
        if FAIL in statuses:
            return "does not hold"
        if UNKNOWN in statuses:
            return "cannot tell"
        return "holds with notes" if WARN in statuses else "holds"

    @property
    def failed(self) -> bool:
        return self.completed and any(c.status == FAIL for c in self.claims)

    def as_data(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "verdict": self.verdict,
            "claims": [{"id": c.id, "status": c.status, "detail": c.detail} for c in self.claims],
        }


def _git_ok(repo: Path, *args: str) -> bool | None:
    from ..infra.worktree import git

    try:
        return git(repo, *args, timeout=60).ok
    except Exception:
        return None


def _tracked(repo: Path) -> set[str] | None:
    from ..infra.worktree import git_paths

    got = git_paths(repo, "ls-files")
    return None if got is None else set(got)


def _trim(names: Sequence[str]) -> str:
    shown = ", ".join(names[:SHOWN])
    return shown + (f" (+{len(names) - SHOWN} more)" if len(names) > SHOWN else "")


def _landed(repo: Path, cfg: Config, led: dict[str, Any]) -> Claim:
    from ..infra.worktree import default_branch

    sha = led["sha"]
    if not sha:
        return Claim("landed", UNKNOWN, "no commit was recorded with the completion")
    exists = _git_ok(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    if exists is None:
        return Claim("landed", UNKNOWN, "git could not be asked")
    if not exists:
        return Claim("landed", FAIL, f"commit {sha[:10]} is not in this repository")
    branches = [default_branch(repo)]
    if cfg.flow.model == "gitflow":
        branches.append(cfg.flow.develop_branch)
    for b in branches:
        if _git_ok(repo, "merge-base", "--is-ancestor", sha, b):
            return Claim("landed", OK, f"{sha[:10]} is on {b}")
    return Claim("landed", FAIL, f"{sha[:10]} exists but is on none of {', '.join(branches)}")


def _declared(led: dict[str, Any], tracked: set[str] | None) -> Claim:
    globs = led["requirement"]["globs"]
    landed = set(led["done"]["files"])
    if tracked is None:
        return Claim("declared_files", UNKNOWN, "the tracked file list could not be read")
    exact = [g for g in globs if not _WILD.search(g)]
    never = [g for g in exact if g not in tracked and g not in landed]
    gone = [g for g in exact if g not in tracked and g in landed]
    if never:
        return Claim("declared_files", FAIL, f"declared but never created: {_trim(never)}")
    notes = []
    if gone:
        notes.append(f"touched by the landing but absent now: {_trim(gone)}")
    if led["done"]["files_known"] and globs and not conflicts(sorted(landed), globs):
        notes.append("the landing touched nothing inside its declared globs")
    if notes:
        return Claim("declared_files", WARN, "; ".join(notes))
    return Claim("declared_files", OK, f"{len(exact)} exact path(s) exist")


def _tests(led: dict[str, Any], tracked: set[str] | None) -> Claim:
    d = led["done"]
    if not d["files_known"]:
        return Claim("tests", UNKNOWN, "the files the landing changed were not recorded")
    code = [f for f in d["files"] if f not in d["tests"] and not _DOCS.search(f)]
    if tracked is not None and (missing := [t for t in d["tests"] if t not in tracked]):
        return Claim("tests", WARN, f"tests it added are gone: {_trim(missing)}")
    if code and not d["tests"]:
        return Claim("tests", WARN, f"changed {len(code)} code file(s) and touched no test")
    return Claim("tests", OK, f"{len(d['tests'])} test file(s) among {d['files_total']} changed")


def _regression(st: State, item_id: str, tracked: set[str] | None) -> Claim | None:
    bugs = [b for b in st.bugs.values() if b.fix_task == item_id]
    if not bugs:
        return None
    for b in bugs:
        named = [t for t in (b.regression_tests or [b.regression_test]) if t]
        if not b.fixed_at and not b.invalid_at:
            return Claim("regression", FAIL, f"bug {b.id} is still open")
        if b.fixed_at and not named:
            return Claim("regression", FAIL, f"bug {b.id} was closed with no regression test")
        files = [t.split("::", 1)[0] for t in named]
        if tracked is not None and (gone := [f for f in files if f and f not in tracked]):
            return Claim(
                "regression", FAIL, f"bug {b.id}: regression test file missing: {_trim(gone)}"
            )
    return Claim("regression", OK, f"{len(bugs)} bug(s) closed with a regression test that exists")


def _gates(cfg: Config, led: dict[str, Any]) -> Claim:
    gates = led["gates"]
    bad = [g for g, v in gates.items() if v["outcome"] == "failed"]
    unreasoned = [g for g, v in gates.items() if v["outcome"] == "skipped" and not v.get("reason")]
    missing = [g for g in cfg.gates.required if g not in gates]
    if bad or unreasoned or missing:
        parts = []
        if bad:
            parts.append(f"failed: {_trim(bad)}")
        if unreasoned:
            parts.append(f"skipped with no reason: {_trim(unreasoned)}")
        if missing:
            parts.append(f"required but never recorded: {_trim(missing)}")
        return Claim("gates", FAIL, "; ".join(parts))
    soft = [g for g, v in gates.items() if v["outcome"] in ("unavailable", "partial")]
    notes = []
    if soft:
        notes.append(f"unavailable/partial: {_trim(soft)}")
    if led["forced"]:
        notes.append(
            f"completed with --force, overriding: {_trim(led['overridden']) or 'unspecified'}"
        )
    if notes:
        return Claim("gates", WARN, "; ".join(notes))
    return Claim("gates", OK, f"{len(gates)} gate(s) recorded, none failed")


def _survives(led: dict[str, Any], tracked: set[str] | None) -> Claim:
    files = led["done"]["files"]
    if not led["done"]["files_known"] or tracked is None:
        return Claim("survives", UNKNOWN, "no recorded file list to compare with")
    gone = [f for f in files if f not in tracked]
    if files and len(gone) == len(files):
        return Claim("survives", FAIL, "every file the landing changed is gone")
    if gone:
        return Claim("survives", WARN, f"later removed or renamed: {_trim(gone)}")
    return Claim("survives", OK, f"all {len(files)} changed file(s) still exist")


def check(repo: Path, cfg: Config, st: State, events: Sequence[Event], item_id: str) -> Report:
    """Verify one item's completion. A not-done item is reported as such, not as a failure."""
    led = LG.build(events, item_id)
    if led is None:
        return Report(item_id, completed=False)
    tracked = _tracked(repo)
    claims = [
        _landed(repo, cfg, led),
        _declared(led, tracked),
        _tests(led, tracked),
        _gates(cfg, led),
        _survives(led, tracked),
    ]
    if reg := _regression(st, item_id, tracked):
        claims.append(reg)
    if led["requirement_changed_after"]:
        claims.append(
            Claim("requirement", WARN, "its requirement text was edited after completion")
        )
    if led["reconstructed"]:
        claims.append(
            Claim("ledger", UNKNOWN, "completed before ledgers existed; rebuilt from the log")
        )
    return Report(item_id, claims)
