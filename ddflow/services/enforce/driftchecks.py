"""Drift from the base branch, and the add-rulebooks-behind commit gate it feeds."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from ...config import Config
from ...core.outcome import FAIL, OK, Verdict
from ...infra import worktree as W
from .. import drift as D
from .hooks import MAX_LISTED_PATHS
from .staged import _committing_tree
from .views import _verdict

# -- B23: drift from the base branch ------------------------------------------------------


def rulebooks() -> tuple[str, ...]:
    """The files whose change on the base branch means an agent is working to old rules.

    A session loads these ONCE, at start; an edit that lands on the base afterwards is
    silently ignored by every branch forked before it, and the drift compounds -- in the
    source project a branch 98 commits behind had to be hand-ported. The native rules
    files are DERIVED from `adopt.NATIVE_RULES`, never retyped: a second hand-kept list
    is the duplicate-then-drift class, and an agent added there would go unwatched here.
    The driver directory is `adopt`'s default `--docs` location, which the rules point at.
    """

    # `.ddflow/rules/` (the project's rules) and not `.ddflow/config.toml`: that file holds
    # KNOBS, which every tree reads live from the primary checkout -- a changed knob leaves
    # no session working to old rules, yet refusing on it forced every in-flight worktree
    # to merge the base and invalidated settled review rounds
    # (B-stale-rules-hook-forces-main-merge). Decisions live in the event log, not in files
    # a path diff can watch.
    fixed = ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", ".ddflow/rules/")
    from ..adopt import NATIVE_RULES  # deferred: services.adopt imports this module

    native = tuple(r.path for r in NATIVE_RULES.values())
    return tuple(dict.fromkeys((*fixed, "docs/ddflow/drivers/", *native)))


@dataclass(frozen=True)
class Drift:
    """How far a tree is behind the branch its work merges into. ONE computation, read by
    the session hook (which informs) and the commit gate (which blocks)."""

    base: str
    #: commits on `base` that HEAD lacks; None = could not tell, which is NEVER "0".
    behind: int | None
    #: rulebooks changed ON `base` since this branch forked (the three-dot diff), so a
    #: rulebook edited on this branch itself is not staleness.
    rules: tuple[str, ...] = ()
    #: why `behind` is None; empty otherwise.
    detail: str = ""


def drift_base(repo: Path, here: Path, cfg: Config | None = None) -> str:
    """The branch ``here``'s work merges into (`drift.base_of`)."""
    return D.base_of(repo, here, cfg)


def drift(repo: Path, here: Path, base: str = "", cfg: Config | None = None) -> Drift:
    """How far ``here``'s HEAD is behind ``base`` (default: `drift_base`), and which
    rulebooks changed there since the fork. Any checkout, the primary included: the
    primary ON the base branch is simply 0 behind. The calculation is `drift.measure`."""
    r = D.measure(repo, here, base, cfg=cfg, rulebooks=rulebooks())
    return Drift(r.base, r.behind, r.rules, r.detail)


def _concludes_merge_of(here: Path, base: str) -> bool:
    """Is the commit being made the conclusion of `git merge <base>`?

    MERGE_HEAD exists and is the base tip or an earlier first-parent tip of the base (the base may have
    moved on since the merge began; the next commit is judged afresh). That commit IS the
    remedy this gate asks for: refusing it would make the refusal impossible to clear,
    and a conflicting merge is exactly the case that needs a commit. HEAD still reads as
    behind until the commit lands, so this is asked before the drift is judged. A stale
    MERGE_HEAD opens no hole: if it contains the base tip and HEAD contains it, HEAD is
    not behind in the first place.
    """
    head = W.git(here, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    if not head.ok or not head.out:
        return False
    if W.git(here, "merge-base", "--is-ancestor", base, head.out).ok:
        return True
    # An earlier tip of the base: on its first-parent line (a side branch the base merged
    # is not the base), and not already in HEAD.
    line = W.git(here, "rev-list", "--first-parent", f"HEAD..{base}")
    return line.ok and head.out in line.out.split()


def _sync_remedy(item: str) -> str:
    """`    ddflow sync {item} --keep-local`, the stale-rules remedy: it names the item when
    ``here`` is its worktree (`ddflow sync <id>` works from anywhere), else is bare."""
    return f"    ddflow sync {item} --keep-local" if item else "    ddflow sync --keep-local"


def check_drift(repo: Path, cfg: Config | None = None, *, here: Path | None = None) -> Verdict:
    """(exit_code, message) for B23: refuse a commit on a branch whose base changed the
    rules since it forked, and warn when it has fallen far behind.

    The asymmetry is the source project's, settled after a 98-commits-behind branch had
    to be hand-ported: the session hook INFORMS (it cannot unload rules a session has
    already read), the commit gate BLOCKS (the one point where the agent must stop and
    merge). Rulebook staleness blocks by default because it is precise and the fix is one
    command; a bare behind-count only warns, because being behind is not by itself wrong.
    "Could not tell" warns with the reason and never blocks: a gate that refuses on
    missing evidence teaches `--no-verify`, and one that passes in silence lies.
    """
    cfg = cfg or Config.load(repo)
    e = cfg.enforce
    if e.stale_rules == "off" and e.behind == "off":
        return Verdict(OK, "")
    if e.behind != "off" and (not isinstance(e.max_behind, int) or e.max_behind < 1):
        # Refused, not read as "off": a 0 that quietly disabled the check would be the
        # silent-knob-drop class. Turning the check off is the policy knob's job. The
        # loader refuses it too; this catches a Config built in code.
        return Verdict(
            FAIL,
            (
                f"ddflow: [enforce].max_behind = {e.max_behind!r} is invalid: it must be >= 1.\n"
                'To stop the behind-count check, set [enforce].behind = "off" instead.'
            ),
        )
    here = here or _committing_tree(repo)
    if here is None:
        return Verdict(
            OK,
            (
                "ddflow: could not tell which working tree this commit is in, so its drift "
                "from the base branch was not checked.\n\n(warning only)"
            ),
        )
    d = drift(repo, here, cfg=cfg)
    if d.behind is None:
        return Verdict(
            OK,
            (
                f"ddflow: could not tell whether this branch is behind `{d.base}`: {d.detail}\n"
                "Its rulebooks were not compared, so this is NOT a pass.\n\n(warning only)"
            ),
        )
    if d.behind == 0 or _concludes_merge_of(here, d.base):
        return Verdict(OK, "")
    code, parts = 0, []
    if d.rules and e.stale_rules != "off":
        sync_remedy = _sync_remedy(D.item_id(repo, here, cfg=cfg))
        c, m = _verdict(
            e.stale_rules,
            [
                f"ddflow: `{d.base}` changed the project's rules since this branch forked, "
                "and this branch has not merged them:",
                "",
                *(f"  {p}" for p in d.rules[:MAX_LISTED_PATHS]),
                *(
                    [f"  ... and {len(d.rules) - MAX_LISTED_PATHS} more"]
                    if len(d.rules) > MAX_LISTED_PATHS
                    else []
                ),
                "",
                "A session loads its rules once; edits landing on the base afterwards are",
                "silently ignored here. Merge the base, then RE-READ the files named above:",
                sync_remedy,
                "(merges the base even with uncommitted work, which it sets aside and puts",
                f"back; or `git merge {shlex.quote(d.base)}` on a clean tree. The commit",
                "concluding that merge is never refused)",
            ],
            "stale_rules",
        )
        code, parts = max(code, c), [*parts, m]
    if d.behind > e.max_behind and e.behind != "off":
        c, m = _verdict(
            e.behind,
            [
                f"ddflow: this branch is {d.behind} commits behind `{d.base}` (more than "
                f"[enforce].max_behind = {e.max_behind}).",
                "Drift compounds: merging now is cheaper than porting later.",
                f"    git merge {shlex.quote(d.base)}",
            ],
            "behind",
        )
        code, parts = max(code, c), [*parts, m]
    return Verdict(code, "\n\n".join(parts))
