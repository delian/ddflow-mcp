"""Chores: the class of files that may complete with no reviewer (decision D-chore-class).

A chore is a task tagged `chore` whose whole change matches `[chore].globs` and whose list a
PERSON approved. This module is the policy and nothing else -- `ddflow.api.chore` does the
ceremony (`ddflow chore commit`) and `services.completion` asks `exemption` whether the
reviewer-independence rule applies.

Three things keep it from becoming the way past the pipeline:

* the tag alone proves nothing: the exemption is judged on the paths the item ACTUALLY
  changed, so a code change tagged `chore` is an ordinary task;
* the approval is of the LIST (`approval_digest`): editing `[chore].globs` -- by hand, by
  `config --set`, by an agent -- leaves the chore class unapproved until a person approves
  the new list, and `approval.grant` refuses under an agent identity;
* there is no flag that grants it: the approval is read from the log, where only a person's
  `ddflow chore approve` writes it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..core import globs as GL
from ..core.digest import content_digest
from ..core.model import Item, State
from . import approval as AP

TAG = "chore"
#: The approval's subject; its digest is the list it covers.
SUBJECT = "chore:class"


def approval_digest(cfg: Config) -> str:
    """What a person approves: the chore globs, order and duplicates aside."""
    return content_digest("\n".join(sorted(set(cfg.chore.globs))))


def outside(cfg: Config, paths: list[str]) -> list[str]:
    """The paths no chore glob covers."""
    return [p for p in paths if not any(GL.match(p, g) for g in cfg.chore.globs)]


def standing(state: State, cfg: Config) -> AP.Verdict:
    """Has a person approved THIS chore list?"""
    return AP.check(state, SUBJECT, approval_digest(cfg))


def slug(message: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", message.lower()).strip("-")
    return words[:32].strip("-") or "change"


@dataclass(frozen=True)
class Exemption:
    """Why an item may complete with no reviewer, as the completion records it."""

    approved_by: str
    digest: str
    paths: tuple[str, ...]

    def note(self) -> str:
        return (
            f"chore: {len(self.paths)} file(s) all match [chore].globs; standing approval "
            f"{SUBJECT}@{self.digest[:12]} by {self.approved_by}"
        )


def exemption(
    state: State, cfg: Config, it: Item, paths: list[str] | None
) -> tuple[Exemption | None, str]:
    """(the exemption, "") when ``it`` is a chore the pipeline excuses from a reviewer, else
    (None, why not) -- "" when it never claimed to be one."""
    if it.kind != "task" or TAG not in [t.strip().lower() for t in it.tags]:
        return None, ""
    if not paths:
        return None, "its changed files could not be read, so it is not judged a chore"
    if bad := outside(cfg, paths):
        return None, f"not a chore: {', '.join(bad[:5])} match no [chore].globs"
    verdict = standing(state, cfg)
    if not verdict.ok:
        return None, f"not a chore: {verdict.reason} -- a person runs `ddflow chore approve`"
    who = (verdict.row or {}).get("user", "")
    return Exemption(who, approval_digest(cfg), tuple(paths)), ""


def relative(repo: Path, name: str) -> str | None:
    """``name`` as a clean repo-relative POSIX path, or None when it leaves the repo."""
    p = Path(name)
    full = (p if p.is_absolute() else repo / p).resolve()
    try:
        rel = full.relative_to(repo.resolve())
    except ValueError:
        return None
    return rel.as_posix()


def judge(
    state: State, cfg: Config, it: Item, paths: list[str] | None
) -> tuple[Exemption | None, str, list[str]]:
    """(the exemption, its note for the completion event, the blockers it adds) for ``it``."""
    excused, why = exemption(state, cfg, it, paths)
    return excused, excused.note() if excused else "", [why] if why else []


def skipped_ok(cfg: Config, it: Item, excused: Exemption | None, gate: str) -> bool:
    """Whether ``gate`` counts as settled because a chore skipped it on the record -- even
    a required gate, which a skip otherwise does not satisfy."""
    return (
        excused is not None and gate in cfg.chore.skip_gates and it.gate_outcome(gate) == "skipped"
    )
