"""Crash recovery: scan and sweep a repo for work a crash could have left behind.

``scan`` reports expired leases, orphaned worktrees and stale-running items; ``sweep``
records expiry for the un-salvageable ones only.
"""

from __future__ import annotations

import importlib
from dataclasses import replace
from pathlib import Path

from ...config import Config
from ...core import schedule
from ...core.model import REVIEW, Item, State
from ...infra import git as G
from ...infra import worktree as W
from ...infra.gitbatch import RepoFacts
from ...infra.log import EventLog
from .. import leases as _leases
from ..leaseclock import foreign
from .common import _fold
from .lifecycle import expire
from .model import Recovery


def scan(
    log: EventLog,
    cfg: Config,
    repo: Path,
    *,
    now: float | None = None,
    state: State | None = None,
    view: bool = False,
) -> list[Recovery]:
    """Find everything a crash could have left behind, and say what to do with each.

    Three distinct situations, deliberately not merged:

    * **expired_lease** — the holder stopped renewing. Its worktree may hold work.
    * **orphan_worktree** — a worktree exists for an item with no live lease.
    * **stale_running** — the item is RUNNING with no lease at all: the agent died
      between starting and claiming, or someone released without finishing.

    Each carries the measured state of the tree (dirty files, unmerged commits), because
    "is there work in here?" is the only question that decides the remedy, and guessing
    it from the item's status is exactly how real work gets deleted.

    The repository-wide git answers (base branch, commits ahead, which merged commits the
    base holds) are asked once per scan (`RepoFacts`), not once per tree. ``view`` is a
    read-only report (`status`, `brief`): it measures at most ``lease.scan_view_trees_max``
    trees and weighs at most ``lease.scan_view_verdicts_max`` completions, because each is
    a process (or dozens) per tree; a tree left unmeasured is ``salvageable = None`` --
    treated as holding work -- and says so. ``recover`` and ``sweep`` are never capped.
    """
    now = _leases.time.time() if now is None else now
    if (
        state is None
    ):  # a caller that already folded this log passes its State (one fold per command)
        state = _fold(log)
    # The same live-lease view the scheduler computes, so `schedule.interrupted` is
    # answering about the same moment `next` would.
    live = state.active_leases(now, cfg.lease.grace_s)
    out: list[Recovery] = []
    facts = RepoFacts(repo)
    budget = _Budget(
        cfg.lease.scan_view_trees_max if view else None,
        cfg.lease.scan_view_verdicts_max if view else None,
    )
    _prefetch_landed(state, cfg, facts)

    for it in state.items.values():
        if it.removed:
            continue
        lease = it.lease
        if lease and not lease.live(now, cfg.lease.grace_s):
            # Prefer whichever source actually names a tree. These should agree, and
            # after the renew fix they do -- but if they ever diverge, trusting the
            # empty one produces "nothing to salvage" over real work, which is the one
            # error this function must never make.
            rec = Recovery(
                item=it.id,
                holder=lease.holder,
                kind="expired_lease",
                adopted=it.adopted,
                worktree=lease.worktree or it.worktree,
                branch=lease.branch or it.branch,
                age_s=now - lease.renewed_at,
            )
            if foreign(lease, log.clone_id(create=False)):
                # Its tree lives in the OTHER clone, so "gone" here proves nothing, and
                # `release` would take a live claim from its owner (B5a6651cd8e).
                rec.salvageable = None  # not measurable from here: never swept
                rec.advice = (
                    f"worktree is in another clone (held by {lease.holder}, clone "
                    f"{lease.clone}); ask its owner"
                )
            else:
                _measure(rec, repo, cfg, facts, budget)
                _note_landed(rec, it, state, cfg, repo, facts, budget)
            out.append(rec)
        elif not lease and it.worktree and it.state != REVIEW:
            # REVIEW is excluded: its tree is held open ON PURPOSE, for the round of
            # changes a reviewer may request, and the request is its custodian. Reported
            # as an orphan it led every brief as "salvage this" (RESEARCH R16).
            if it.terminal and _tree_is_gone(repo, it.worktree):
                # Finishing an item never clears its recorded worktree, so a finished item
                # whose tree was removed would be re-reported on every run with nothing to
                # do (B999669a8df). An UNFINISHED item that lost its tree still is reported:
                # it may have lost work.
                continue
            rec = Recovery(
                item=it.id,
                holder="(none)",
                kind="orphan_worktree",
                adopted=it.adopted,
                worktree=it.worktree,
                branch=it.branch,
            )
            _measure(rec, repo, cfg, facts, budget)
            _note_landed(rec, it, state, cfg, repo, facts, budget)
            out.append(rec)
        elif schedule.interrupted(state, it, live):
            # Same predicate the scheduler uses, so `recover` and `next` cannot
            # disagree about which items are mid-flight with nobody on them.
            rec = Recovery(
                item=it.id,
                holder="(none)",
                kind="stale_running",
                advice=f"`ddflow claim {it.id}` to resume, or `ddflow block {it.id} --reason ...`",
            )
            out.append(rec)
    return sorted(out, key=lambda r: (r.kind, r.item))


class _Budget:
    """What a read-only view may spend: trees measured, completions weighed (None: no cap)."""

    def __init__(self, trees: int | None, verdicts: int | None):
        self.trees = trees
        self.verdicts = verdicts

    def take_tree(self) -> bool:
        return self._take("trees")

    def take_verdict(self) -> bool:
        return self._take("verdicts")

    def _take(self, name: str) -> bool:
        left = getattr(self, name)
        if left is None:
            return True
        if left <= 0:
            return False
        setattr(self, name, left - 1)
        return True


def _prefetch_landed(state: State, cfg: Config, facts: RepoFacts) -> None:
    """One query for which merged commits the base holds, before the per-item checks: asked
    only about the items a recovery can be reported for (an unfinished item with a lapsed
    lease or a leftover tree), so a state with none needs no git answer at all."""
    shas = [
        it.merged_sha
        for it in state.items.values()
        if not it.removed
        and not it.terminal
        and it.merged_sha
        and (it.lease is not None or it.worktree)
        and it.gate_outcome("merge") == "passed"
    ]
    if shas:
        facts.prefetch_on_base(shas, facts.base(cfg.worktree.base_ref))


def _tree_is_gone(repo: Path, recorded: str) -> bool:
    """Does the recorded worktree path no longer exist?"""
    wt = W.load_path(repo, recorded) if recorded else None
    return not wt or not wt.exists()


def open_session_model(st: State, agent: str) -> str:
    """The model ``agent`` declared on its most recent session, OPEN or ENDED, or "".

    Only this agent's: another agent's session names another author, and borrowing its
    model would judge reviewer independence against the wrong family. A session names an
    agent, not an item. The declaration is the record, not its liveness (the driver's
    close step ends a session, and a completion afterwards must still see the model);
    the agent's LATEST session (by started_at) speaks last, and a newer model-less one
    supersedes older declarations. One definition, shared by `complete`, `gate record`'s
    author-family vet and what `recover` advises."""
    own = [s for s in st.sessions.values() if s.agent == agent]
    return max(own, key=lambda s: s.started_at).model if own else ""


#: How many of `complete`'s refusals the recovery advice names before it counts the rest.
_SHOWN_BLOCKERS = 3


def _landed_on_base(repo: Path, cfg: Config, it: Item, facts: RepoFacts | None = None) -> str:
    """The commit `merge` recorded when it is reachable from the base branch, else "".
    Checked against git, not the log: a recorded merge on a branch since rewound is not
    "on main"."""
    sha = it.merged_sha
    if it.gate_outcome("merge") != "passed" or not sha:
        return ""
    facts = facts or RepoFacts(repo)
    return sha if facts.on_base(sha, facts.base(cfg.worktree.base_ref)) else ""


def _note_landed(
    rec: Recovery,
    it: Item,
    state: State,
    cfg: Config,
    repo: Path,
    facts: RepoFacts | None = None,
    budget: _Budget | None = None,
) -> None:
    """An item whose merge gate is done and whose commit is on the base, with nothing left in
    its tree, needs COMPLETING, not releasing (Ba2eff5726b): `release` only returned it to the
    queue to be claimed and merged again. Says what `complete` still wants, if anything."""
    if rec.salvageable is not False or rec.adopted or it.terminal:
        return  # a finished item is owed nothing: `complete` would record it twice
    sha = _landed_on_base(repo, cfg, it, facts)
    if not sha:
        return
    if budget is not None and not budget.take_verdict():
        # A read-only view does not weigh the completion (dozens of git calls per item):
        # it says the item is merged and where the full answer is.
        rec.advice = (
            f"merged to the base ({sha[:9]}, merge gate passed); nothing left to salvage. "
            f"Finish it: `ddflow complete {it.id} --model <author model>`; "
            "`ddflow recover` says what `complete` still wants."
        )
        return
    # Resolved at call time: completion imports the gate stack, which imports this module
    # (a module-level import is a cycle; a deferred `import` statement is a ratchet breach).
    CM = importlib.import_module("..completion", package=__package__)

    # The holder's own model, standing in for the author's (`complete` judges reviewer
    # independence against it); whoever completes may not be the holder, so the advice says to
    # pass `--model`.
    model = open_session_model(state, rec.holder)
    v = CM.verdict(state, cfg, it.id, repo=repo, model=model)
    head = f"merged to the base ({sha[:9]}, merge gate passed); nothing left to salvage. "
    if v.may_complete:
        rec.advice = (
            head
            + f"Finish it: `ddflow complete {it.id} --model <author model>`"
            + (" (it also clears the lease)." if rec.kind == "expired_lease" else ".")
            + " (An advisory read: `complete` also runs the phase-suite and review checks.)"
        )
    else:
        rec.advice = (
            head
            + f"`ddflow complete {it.id}` is refused for now: "
            + "; ".join(b.split("\n")[0][:200] for b in v.blockers[: _leases._SHOWN_BLOCKERS])
            + (
                f" (+{len(v.blockers) - _leases._SHOWN_BLOCKERS} more)"
                if len(v.blockers) > _leases._SHOWN_BLOCKERS
                else ""
            )
            + f". Settle what `ddflow gate status {it.id}` shows, then "
            + f"`ddflow complete {it.id} --model <author model>`."
        )


def _branch_ahead(repo: Path, facts: RepoFacts, cfg: Config, branch: str) -> int:
    """Commits ``branch`` holds that the base lacks; -1 when it cannot be told.

    Asked only when the worktree is gone: the branch may still carry unmerged work, and
    "nothing to salvage" over it deletes real work (B5b1c6272c1). A branch that no longer
    exists means there is genuinely nothing left, reported as 0; one git could not count is
    -1, never folded into "empty" by `_measure`.
    """
    if not branch:
        return -1
    if not W.branch_exists(repo, branch):
        return 0
    return G.commits_ahead(repo, facts.base(cfg.worktree.base_ref), branch)


def _measure(
    rec: Recovery,
    repo: Path,
    cfg: Config,
    facts: RepoFacts | None = None,
    budget: _Budget | None = None,
) -> None:
    """Measure the worktree rather than trusting the item's recorded state.

    'Dirty' alone is NOT evidence of unshipped work — a dirty tree is just as often a
    superseded draft. So we report both halves (uncommitted files AND commits not on
    the base branch) and let the operator judge, with the diff command spelled out.

    Git access goes through ``worktree.py``, which owns it. This module briefly carried
    its own copy, and the copy drifted: its branch resolver fell back to the literal
    string ``"HEAD"`` where the real one falls back to the current branch name. On any
    repository whose default branch is not ``main`` or ``master`` that turned the
    unmerged-commit count into ``rev-list HEAD..HEAD`` = 0, so a tree holding unmerged
    work was reported "safe to remove" — the one error this function must never make.
    """
    # Only an expired lease is still there to release. An orphan's lease is already
    # null, and telling someone to release it sends them to an exit 2 (B4f9019c0d7).
    held = rec.kind == "expired_lease"
    facts = facts or RepoFacts(repo)
    wt = W.load_path(repo, rec.worktree) if rec.worktree else None
    if not wt or not wt.exists():
        # The tree is gone (a merge removed it), so `recover` must not name a path that
        # no longer exists (B-recover-forgets-author-model): the advice says it is gone,
        # and the row carries no worktree at all. But a gone TREE proves nothing about
        # the BRANCH that carried it: `merge` (or an agent) removed the tree while the
        # branch still held commits the base lacks, and reporting those "nothing to
        # salvage" made cleanup propose deleting real work (B5b1c6272c1). Ask the branch
        # before declaring it empty.
        rec.worktree = ""
        ahead = _branch_ahead(repo, facts, cfg, rec.branch)
        if ahead > 0:
            rec.unmerged_commits = ahead
            rec.salvageable = True
            base = facts.base(cfg.worktree.base_ref)
            rec.advice = (
                f"the worktree is gone but its branch {rec.branch} holds {ahead} "
                f"unmerged commit(s) — salvage them before sweeping: "
                f"`git -C {repo} log {base}..{rec.branch}`"
                + (f", then `ddflow release {rec.item} --note salvaged`." if held else ".")
            )
            return
        rec.advice = "worktree is gone; nothing to salvage" + (
            f". `ddflow release {rec.item}` to clear the claim."
            if held
            else ", and no lease is held on it."
        )
        return
    if budget is not None and not budget.take_tree():
        rec.salvageable = None  # not measured: unknown is never clean
        rec.advice = (
            "NOT MEASURED in this view (it measures at most "
            f"{cfg.lease.scan_view_trees_max} trees, `lease.scan_view_trees_max`). Treat it as "
            f"containing work until `ddflow recover` has measured it: `git -C {wt} status`."
        )
        return
    base = facts.base(cfg.worktree.base_ref)
    tw = W.tree_work(wt, base, ahead=False)
    if tw.readable:
        tw = replace(tw, ahead=facts.ahead(wt, base))
    rec.dirty_files = len(tw.dirty) if tw.readable else -1
    rec.unmerged_commits = tw.ahead
    if rec.dirty_files < 0 or rec.unmerged_commits < 0:
        rec.salvageable = None
        rec.advice = (
            f"COULD NOT MEASURE this worktree (git returned "
            f"{tw.err or 'an error'!r:.80}). Treat it as containing work until "
            f"you have looked: `git -C {wt} status` and `git -C {wt} log {base}..HEAD`. "
            f"It will NOT be swept automatically."
        )
        return
    rec.salvageable = rec.dirty_files > 0 or rec.unmerged_commits > 0
    if rec.salvageable:
        rec.advice = (
            f"INSPECT FIRST — {rec.dirty_files} uncommitted file(s), "
            f"{rec.unmerged_commits} unmerged commit(s). "
            f"`git -C {wt} diff {base}` then salvage"
            + (f", then `ddflow release {rec.item} --note salvaged`." if held else ".")
            + (
                " The tree is adopted: the agent harness's own working tree, not "
                "ddflow's -- salvage from it and leave the tree to the harness."
                if rec.adopted
                else ""
            )
        )
    elif rec.adopted:
        rec.advice = (
            "clean and fully merged, but adopted: this is the agent harness's own working "
            "tree, not ddflow's -- leave it to the harness. "
        ) + (
            f"`ddflow release {rec.item}` is all ddflow needs."
            if held
            else "No lease is held on it."
        )
    else:
        # `git worktree remove`, not `ddflow cleanup --apply`: that one removes EVERY
        # merged tree, a live agent's freshly claimed one included (B5a009b185a).
        rec.advice = f"clean and fully merged — safe to remove: `git worktree remove {wt}`" + (
            f" then `ddflow release {rec.item}`." if held else " (no lease is held on it)."
        )


def sweep(log: EventLog, cfg: Config, repo: Path, *, apply: bool = False) -> list[Recovery]:
    """Report recoverable state; with ``apply``, record expiry for the UNSALVAGEABLE
    ones only. Salvageable trees are never touched automatically, whatever the policy —
    the knob controls convenience, not safety."""
    found = scan(log, cfg, repo)
    if apply:
        for rec in found:
            # `is False` NOT `not ...` -- None means "could not measure" and must never
            # be swept. `not None` is True, which is how unknown became clean.
            if rec.kind == "expired_lease" and rec.salvageable is False:
                expire(log, rec.item, reason=f"swept: {rec.advice}")
    return found
