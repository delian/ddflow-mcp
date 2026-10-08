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
from ..core.schedule import path_in_glob
from . import backfill as BF
from . import gates as G
from . import ledger as LG
from .completion import fixes_of

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"
_WILD = re.compile(r"[*?\[]")
_DOCS = re.compile(r"\.(md|rst|txt|json|toml|ya?ml|lock|csv)$|(^|/)docs?/", re.I)
SHOWN = 6
_MIN_SUBJECT = 12  # shorter subjects ("fix", "wip") identify nothing


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

    r = git(repo, *args, timeout=60)
    return None if r.unavailable else r.ok


def _ever_existed(repo: Path, path: str, rev: str) -> bool:
    """Was `path` ever in the history `rev` descends from? Scoped to that history, not
    `--all`: a sibling branch that creates it says nothing about this landing. When git
    cannot answer, say yes -- an unanswered question must not become an accusation."""
    from ..infra.worktree import git

    try:
        r = git(repo, "log", "--oneline", "-1", rev, "--", path, timeout=60)
    except Exception:  # git missing or hung
        return True
    return bool(r.out) if r.ok else True


def _tracked(repo: Path) -> set[str] | None:
    from ..infra.worktree import git_paths

    got = git_paths(repo, "ls-files")
    return None if got is None else set(got)


def _trim(names: Sequence[str]) -> str:
    shown = ", ".join(names[:SHOWN])
    return shown + (f" (+{len(names) - SHOWN} more)" if len(names) > SHOWN else "")


def _rewritten_twin(repo: Path, sha: str, branches: list[str]) -> str:
    """A commit on one of `branches` with the recorded commit's subject, or "".

    A rebase or history rewrite gives the same work a new hash; the recorded one then
    survives only on an old branch. The subject is a weak identity, so this only softens a
    failure to a warning, never to ok."""
    from ..infra.worktree import git

    subj = git(repo, "log", "-1", "--format=%s", sha, timeout=60)
    if not subj.ok or len(subj.out) < _MIN_SUBJECT:
        return ""
    for br in branches:
        # `--grep` is a substring match over the whole message; keep only a commit whose
        # SUBJECT is exactly the recorded one.
        r = git(repo, "log", "--format=%H%x09%s", "-F", f"--grep={subj.out}", br, timeout=60)
        if r.ok:
            for line in r.out.splitlines():
                h, _, s_ = line.partition("\t")
                if s_ == subj.out:
                    return h
    return ""


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
    prod = default_branch(repo)
    gitflow = cfg.flow.model == "gitflow"
    on: dict[str, bool | None] = {
        br: _git_ok(repo, "merge-base", "--is-ancestor", sha, br)
        for br in ([prod, cfg.flow.develop_branch] if gitflow else [prod])
    }
    if None in on.values():
        return Claim("landed", UNKNOWN, "git could not be asked whether it is on the branch")
    if gitflow:
        if on[cfg.flow.develop_branch]:
            return Claim("landed", OK, f"{sha[:10]} is on {cfg.flow.develop_branch}")
        if on[prod]:
            return Claim(
                "landed",
                WARN,
                f"{sha[:10]} is on {prod} but not on {cfg.flow.develop_branch} "
                f"(a hotfix awaiting its back-merge?)",
            )
    elif on[prod]:
        return Claim("landed", OK, f"{sha[:10]} is on {prod}")
    twin = _rewritten_twin(repo, sha, list(on))
    if twin:
        return Claim(
            "landed",
            WARN,
            f"{sha[:10]} is not on {', '.join(on)}, but a commit with the same subject is "
            f"({twin[:10]}): history was rewritten since",
        )
    return Claim("landed", FAIL, f"{sha[:10]} exists but is on none of {', '.join(on)}")


def _present(repo: Path, path: str, tracked: set[str]) -> bool:
    """Does `path` exist now? Tracked, on disk (untracked and ignored files count: a
    declared `.mcp.json` is real even though git never saw it), or a directory that
    contains tracked files."""
    bare = path.rstrip("/")
    if path in tracked or (repo / bare).exists():
        return True
    prefix = bare + "/"
    return any(t.startswith(prefix) for t in tracked)


def _declared(repo: Path, led: dict[str, Any], tracked: set[str] | None) -> Claim:
    globs = led["requirement"]["globs"]
    landed = set(led["done"]["files"])
    if tracked is None:
        return Claim("declared_files", UNKNOWN, "the tracked file list could not be read")
    exact = [g for g in globs if not _WILD.search(g)]
    absent = [g for g in exact if not _present(repo, g, tracked) and g not in landed]
    # Absent now and not in the landing: either it never existed (the false positive) or
    # it existed and was removed or renamed later -- history tells the two apart.
    rev = led["sha"] or "HEAD"
    removed_later = [g for g in absent if _ever_existed(repo, g, rev)]
    never = [g for g in absent if g not in removed_later]
    gone = [g for g in exact if not _present(repo, g, tracked) and g in landed] + removed_later
    if never:
        return Claim("declared_files", FAIL, f"declared but never created: {_trim(never)}")
    notes = []
    if gone:
        notes.append(f"existed once but absent now: {_trim(gone)}")
    # On this machine but unknown to git: real here, absent from any other checkout.
    local_only = [
        g
        for g in exact
        if g not in tracked
        and _present(repo, g, tracked)
        and not any(t.startswith(g.rstrip("/") + "/") for t in tracked)
    ]
    if local_only:
        notes.append(
            f"exists here but is untracked, so no other checkout has it: {_trim(local_only)}"
        )
    # A landed PATH inside a glob, not a prefix of one (B1997c64c5a).
    if (
        led["done"]["files_known"]
        and globs
        and not any(path_in_glob(p, g) for p in landed for g in globs)
    ):
        notes.append("the landing touched nothing inside its declared globs")
    if notes:
        return Claim("declared_files", WARN, "; ".join(notes))
    return Claim("declared_files", OK, f"{len(exact)} exact path(s) exist")


def _tests(led: dict[str, Any], tracked: set[str] | None) -> Claim:
    d = led["done"]
    if not d["files_known"]:
        return Claim("tests", UNKNOWN, "the files the landing changed were not recorded")
    if not d["files"]:
        return Claim("tests", WARN, "the landing changed no files, so there is nothing it tested")
    code = [f for f in d["files"] if f not in d["tests"] and not _DOCS.search(f)]
    if tracked is not None and (missing := [t for t in d["tests"] if t not in tracked]):
        return Claim("tests", WARN, f"tests it added are gone: {_trim(missing)}")
    if code and not d["tests"]:
        return Claim("tests", WARN, f"changed {len(code)} code file(s) and touched no test")
    return Claim("tests", OK, f"{len(d['tests'])} test file(s) among {d['files_total']} changed")


def _regression(
    st: State, item_id: str, tracked: set[str] | None, cfg: Config | None = None
) -> Claim | None:
    # What the item was filed to fix, as `complete` asks it: a bug merely reported
    # against it stays open after it, and is not a broken claim of its (B7bdcc6b212).
    bugs = [st.bugs[b] for b in sorted(fixes_of(st, item_id, cfg))]
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


def _gates(cfg: Config, led: dict[str, Any], pipeline: Sequence[str]) -> Claim:
    """What `complete` itself enforces, asked again of the record: every pipeline gate
    carries an outcome, every required gate PASSED, and a skip has a reason. A failed
    gate that is not required (a review whose findings were triaged) does not block a
    completion, so it is a note here, not a failure."""
    gates = led["gates"]
    silent = [g for g in pipeline if g not in gates] if cfg.gates.require_outcome else []
    required_bad = [
        g
        for g in cfg.gates.required
        if g in pipeline and gates.get(g, {}).get("outcome") != "passed"
    ]
    unreasoned = [g for g, v in gates.items() if v["outcome"] == "skipped" and not v.get("reason")]
    if silent or required_bad or unreasoned:
        parts = []
        if required_bad:
            parts.append(f"required gate(s) not passed: {_trim(required_bad)}")
        if silent:
            parts.append(f"never run and never skipped: {_trim(silent)}")
        if unreasoned:
            parts.append(f"skipped with no reason: {_trim(unreasoned)}")
        return Claim("gates", FAIL, "; ".join(parts))
    notes = []
    if failed := [g for g, v in gates.items() if v["outcome"] == "failed"]:
        notes.append(f"failed but not required: {_trim(failed)}")
    if soft := [g for g, v in gates.items() if v["outcome"] in ("unavailable", "partial")]:
        notes.append(f"unavailable/partial: {_trim(soft)}")
    if led["forced"]:
        notes.append(
            f"completed with --force, overriding: {_trim(led['overridden']) or 'unspecified'}"
        )
    if notes:
        return Claim("gates", WARN, "; ".join(notes))
    return Claim("gates", OK, f"{len(gates)} gate(s) recorded, none blocking")


def _survives(led: dict[str, Any], tracked: set[str] | None) -> Claim:
    files = led["done"]["files"]
    if not led["done"]["files_known"] or tracked is None:
        return Claim("survives", UNKNOWN, "no recorded file list to compare with")
    if not files:
        return Claim("survives", WARN, "the landing changed no files, so nothing can survive")
    gone = [f for f in files if f not in tracked]
    if len(gone) == len(files):
        return Claim("survives", FAIL, "every file the landing changed is gone")
    if gone:
        return Claim("survives", WARN, f"later removed or renamed: {_trim(gone)}")
    return Claim("survives", OK, f"all {len(files)} changed file(s) still exist")


def _rebuilt_claim(rebuilt: dict[str, str], why: str) -> Claim:
    if rebuilt.get("kind") == "recorded":
        what = f"the recorded landing {rebuilt['sha'][:10]} had no file facts; they were rebuilt from git"
    else:
        what = f"the landing {rebuilt['sha'][:10]} was found afterwards by {rebuilt['how']}"
    return Claim(
        "ledger", WARN, f"{why}; {what} -- reconstructed, not what the completing agent recorded"
    )


def check(
    repo: Path,
    cfg: Config,
    st: State,
    events: Sequence[Event],
    item_id: str,
    *,
    tracked: set[str] | str | None = "read",
) -> Report:
    """Verify one item's completion. A not-done item is reported as such, not as a failure.

    `tracked` lets a sweep read `git ls-files` once for all its items."""
    led = LG.build(events, item_id)
    if led is None:
        return Report(item_id, completed=False)
    if tracked == "read":
        tracked = _tracked(repo)
    led = BF.apply(repo, st, led, item_id)
    rebuilt = led.get("backfill")
    if led["imported"]:
        # Closed in a document before ddflow existed: there is no gate history to check,
        # and saying "no gates ran" would accuse work nobody recorded. What CAN still be
        # checked is a declared file that never existed -- and, when git still shows the
        # landing, that it landed and survived.
        note = led["import_evidence"] or "no evidence recorded"
        claims = [_declared(repo, led, tracked)]
        if rebuilt:
            claims += [_landed(repo, cfg, led), _survives(led, tracked)]
            claims.append(_rebuilt_claim(rebuilt, f"imported as already closed ({note})"))
        else:
            claims.append(
                Claim(
                    "ledger",
                    UNKNOWN,
                    f"imported as already closed ({note}); no gate or landing history",
                )
            )
        return Report(item_id, claims)
    pipeline = G.pipeline_for(st.items[item_id], cfg) if item_id in st.items else []
    claims = [
        _landed(repo, cfg, led),
        _declared(repo, led, tracked),
        _tests(led, tracked),
        _gates(cfg, led, pipeline),
        _survives(led, tracked),
    ]
    if reg := _regression(st, item_id, tracked, cfg):
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


#: How much each kind of finding weighs in a sweep's ranking: a failed claim dwarfs any
#: number of notes, so the worst completions are listed first whatever the count of items.
_WEIGHT = {FAIL: 100, UNKNOWN: 10, WARN: 3}


def suspicion(rep: Report) -> int:
    return sum(_WEIGHT.get(c.status, 0) for c in rep.claims)


@dataclass
class Sweep:
    checked: int
    counts: dict[str, int]
    worst: list[tuple[int, Report]]

    def as_data(self, limit: int) -> dict[str, Any]:
        shown = self.worst[:limit]
        return {
            "checked": self.checked,
            "counts": self.counts,
            "shown": len(shown),
            "worst": [
                {
                    "item": r.item,
                    "verdict": r.verdict,
                    "score": score,
                    "problems": [
                        {"id": c.id, "status": c.status, "detail": c.detail}
                        for c in r.claims
                        if c.status != OK
                    ],
                }
                for score, r in shown
            ],
        }


def sweep(
    repo: Path, cfg: Config, st: State, events: Sequence[Event], items: Sequence[str]
) -> Sweep:
    """Check every item, most suspicious first. Git's file list is read once."""
    tracked = _tracked(repo)
    counts = {"holds": 0, "holds with notes": 0, "cannot tell": 0, "does not hold": 0}
    ranked: list[tuple[int, Report]] = []
    for item in items:
        rep = check(repo, cfg, st, events, item, tracked=tracked)
        if not rep.completed:
            continue
        counts[rep.verdict] += 1
        ranked.append((suspicion(rep), rep))
    ranked.sort(key=lambda t: (-t[0], t[1].item))
    return Sweep(sum(counts.values()), counts, [t for t in ranked if t[0] > 0])
