"""ONE drift calculation (D-drift-measure, D-item-base-branch).

drift(item) = the commits reachable from the item's recorded base and not from the
item's branch point (`HEAD..base`), optionally narrowed to the item's globs, plus the
decisions added or superseded that govern those globs. Every consumer -- the commit
gate (`enforce.check_drift`), `ddflow sync`, the drift notice, freshness and staleness
checks -- asks THIS module, so "behind" means one thing. Pure over git calls; nothing
here writes the event log. "Could not tell" is `behind=None`, never 0.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from ..config import Config
from ..core.globs import inside
from ..core.model import ABANDONED, DONE, fold
from ..infra import worktree as W
from ..infra.fsio import replace_text
from ..infra.log import EventLog
from . import changes as CH

git = W.git


class Commit(NamedTuple):
    sha: str
    subject: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class DriftReport:
    """What a tree has not taken from its base. ``behind`` is None = could not tell."""

    base: str
    behind: int | None
    ahead: int = 0
    commits: tuple[Commit, ...] = ()
    #: commits touching the globs / pin files / diff paths the caller named.
    relevant: tuple[Commit, ...] = ()
    #: rulebooks changed on the base since the fork AND still different here.
    rules: tuple[str, ...] = ()
    detail: str = ""

    @property
    def unrelated(self) -> tuple[Commit, ...]:
        keep = {c.sha for c in self.relevant}
        return tuple(c for c in self.commits if c.sha not in keep)


@dataclass(frozen=True)
class Change:
    id: str
    how: str  # "added" | "superseded"


def item_base(repo: Path, here: Path, cfg: Config | None = None) -> str:
    """The recorded `Item.base` of the item whose worktree IS ``here``, or "".

    Found by where the item's worktree resolves; a live item wins over a finished one
    whose tree still sits at the path. An unreadable log is "": the caller then measures
    against the default branch, which is still a check, not a pass.
    """
    try:
        cfg = cfg or Config.load(repo)
        state = fold(EventLog(repo, cfg.agent.id or "", log_cfg=cfg.log).read_all(), strict=False)
    except Exception:
        return ""
    here = here.resolve()
    found = [
        it
        for it in state.items.values()
        if it.worktree and it.base and not it.removed
        if W.load_path(repo, it.worktree).resolve() == here
    ]
    found.sort(key=lambda it: it.state in (DONE, ABANDONED))
    return found[0].base if found else ""


def base_of(repo: Path, tree: Path, cfg: Config | None = None) -> str:
    """The branch ``tree``'s work merges into: its item's recorded base, else the default
    branch. Not literal `main`: a stacked or gitflow task forks from its dependency's
    branch or a release line. A recorded base that no longer resolves (a stacked
    dependency deleted after it landed) falls back to the default branch."""
    base = item_base(repo, tree, cfg)
    if base and git(tree, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").ok:
        return base
    return W.default_branch(repo)


def _commits(tree: Path, rng: str) -> tuple[Commit, ...] | None:
    # A merge lists the paths it brought (against its first parent), not nothing.
    r = git(tree, "-c", "core.quotepath=off", "log", "--diff-merges=first-parent", "--no-renames", "--name-only",
            "--format=%x01%H%x1f%s", rng)  # fmt: skip
    if not r.ok:
        return None
    seen: dict[str, tuple[str, list[str]]] = {}
    for chunk in r.out.split("\x01"):
        if not chunk.strip():
            continue
        head, _, rest = chunk.partition("\n")
        sha, _, subject = head.partition("\x1f")
        paths = seen.setdefault(sha, (subject, []))[1]
        paths.extend(p for p in rest.splitlines() if p and p not in paths)
    return tuple(Commit(sha, subj, tuple(ps)) for sha, (subj, ps) in seen.items())


def _touches(c: Commit, globs: Iterable[str]) -> bool:
    gl = tuple(globs)
    return any(inside(p, g) for p in c.paths for g in gl)


def _rules(tree: Path, base: str, rulebooks: tuple[str, ...]) -> tuple[str, ...] | None:
    # Three dots: what changed on `base` since the merge base, not this branch's edits.
    changed = CH.changed_paths(
        tree, "HEAD", tip=base, include=("committed",), renames=True, pathspec=rulebooks
    )
    if changed is None:
        return None
    if changed:
        # ...AND still different here: a rulebook already taken by cherry-pick is no drift.
        differ = CH.changed_paths(
            tree, "HEAD", tip=base, include=("committed",), fork=False, renames=True,
            pathspec=changed, literal=True,
        )  # fmt: skip
        if differ is None:
            return None
        changed = [p for p in changed if p in set(differ)]
    return tuple(changed)


def measure(
    repo: Path,
    tree: Path,
    base: str = "",
    *,
    since: str | None = None,
    cfg: Config | None = None,
    rulebooks: tuple[str, ...] = (),
    globs: Iterable[str] = (),
    pin_files: Iterable[str] = (),
    diff_paths: Iterable[str] = (),
) -> DriftReport:
    """How far ``tree`` is behind ``base`` (default `base_of`). With ``since`` (a sha)
    the commits are those the base gained after it, so a notice fires once per change."""
    base = base or base_of(repo, tree, cfg)
    n = git(tree, "rev-list", "--count", f"HEAD..{base}")
    if not n.ok or not n.out.isdigit():
        return DriftReport(base, None, detail=n.err or n.out or "git rev-list failed")
    behind = int(n.out)
    a = git(tree, "rev-list", "--count", f"{base}..HEAD")
    ahead = int(a.out) if a.ok and a.out.isdigit() else 0
    if behind == 0:
        return DriftReport(base, 0, ahead)
    rng = f"{since}..{base}" if since else f"HEAD..{base}"
    commits = _commits(tree, rng)
    if commits is None:
        return DriftReport(base, None, ahead, detail=f"git could not list {rng}")
    rules = ()
    if rulebooks:
        got = _rules(tree, base, rulebooks)
        if got is None:
            return DriftReport(base, None, ahead, detail=f"git could not diff HEAD...{base}")
        rules = got
    spec = (*globs, *pin_files, *diff_paths)
    relevant = tuple(c for c in commits if spec and _touches(c, spec))
    return DriftReport(base, behind, ahead, commits, relevant, rules)


def moved_or_gone(
    tree: Path, paths: Iterable[str], since_sha: str, tip: str = "HEAD"
) -> dict[str, str | None] | None:
    """Which of ``paths`` were renamed (-> new path) or deleted (-> None) between
    ``since_sha`` and ``tip``. A path not in the result is where it was; None = git could
    not say, which is NOT "nothing moved"."""
    want = set(paths)
    r = git(tree, "-c", "core.quotepath=off", "diff", "--name-status", "-M", since_sha, tip)
    if not r.ok:
        return None
    out: dict[str, str | None] = {}
    for line in r.out.splitlines():
        kind, *names = line.split("\t")
        if kind.startswith("R") and names[0] in want:
            out[names[0]] = names[-1]
        elif kind == "D" and names[0] in want:
            out[names[0]] = None
    return out


def decisions_changed(state, globs: Iterable[str], since_at: str) -> list[Change]:
    """Decisions governing ``globs`` that were recorded, or superseded, after the ISO
    timestamp ``since_at``. A decision with no globs governs nothing here."""
    gl = tuple(globs)
    out: list[Change] = []
    for d in state.decisions.values():
        if not any(inside(g, dg) or inside(dg, g) for g in gl for dg in d.globs):
            continue
        if d.at and d.at > since_at:
            out.append(Change(d.id, "added"))
        by = state.decisions.get(d.superseded_by) if d.superseded_by else None
        if by is not None and by.at and by.at > since_at and d.at <= since_at:
            out.append(Change(d.id, "superseded"))
    return sorted(out, key=lambda c: (c.id, c.how))


def stamp(tree: Path, base: str) -> str:
    """The sha of ``base`` now, for a created_at_sha field; "" when it does not resolve."""
    r = git(tree, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    return r.out if r.ok else ""


def uncovered(tree: Path, merged_shas: Iterable[str], tested_sha: str) -> list[str]:
    """The merged shas that are NOT ancestors of the tested sha."""
    return [
        s for s in merged_shas if not git(tree, "merge-base", "--is-ancestor", s, tested_sha).ok
    ]


def is_covered(tree: Path, merged_shas: Iterable[str], tested_sha: str) -> bool:
    return not uncovered(tree, merged_shas, tested_sha)


def _seen_path(repo: Path) -> Path:
    return repo / ".ddflow" / "local" / "last-seen"


def last_seen(repo: Path, tree: Path) -> str:
    """The base sha last reported for ``tree`` ("" = never). Machine-local, git-ignored."""
    try:
        return json.loads(_seen_path(repo).read_text()).get(str(tree.resolve()), "")
    except (OSError, ValueError, AttributeError):
        return ""


def set_last_seen(repo: Path, tree: Path, sha: str) -> None:
    p = _seen_path(repo)
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[str(tree.resolve())] = sha
    p.parent.mkdir(parents=True, exist_ok=True)
    replace_text(p, json.dumps(data, sort_keys=True))
