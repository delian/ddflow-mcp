"""Fold handlers: worktrees, pull requests, ports, deploys, the branching flow and releases.

Assembled into `model.HANDLERS`; each is `(State, Event) -> None` and pure."""

from __future__ import annotations

from ..events import Event
from ..records import BLOCKED, OPEN, REVIEW, Item, PullRequest, Release, State
from ._common import _item


def _h_worktree_created(st: State, ev: Event) -> None:
    it = _item(st, ev, ev.data.get("kind", "task"))
    it.worktree = ev.data.get("path", "")
    it.branch = ev.data.get("branch", "")
    it.base = ev.data.get("base", "")


def _h_worktree_adopted(st: State, ev: Event) -> None:
    _h_worktree_created(st, ev)
    _item(st, ev, ev.data.get("kind", "task")).adopted = True


def _h_worktree_merged(st: State, ev: Event) -> None:
    it = _item(st, ev, ev.data.get("kind", "task"))
    it.merged_sha = ev.data.get("sha", "")
    it.landed_before = ev.data.get("landed_before", it.landed_before)
    it.landed_after = ev.data.get("landed_after", it.landed_after)


def _h_port_applied(st: State, ev: Event) -> None:
    it = _item(st, ev, ev.data.get("kind", "task"))
    it.port = dict(ev.data)


def _h_deploy_recorded(st: State, ev: Event) -> None:
    st.deployments[ev.data.get("env", ev.subject)] = {
        "sha": ev.data.get("sha", ""),
        "at": ev.ts,
        "agent": ev.agent,
    }


def _h_flow_chosen(st: State, ev: Event) -> None:
    d = ev.data
    st.flow_choices[d.get("knob", ev.subject)] = {
        "value": d.get("value"),
        "by": d.get("by", "explicit"),
        "agent": ev.agent,
        "user": d.get("user", ""),
        "at": ev.ts,
        "reason": d.get("reason", ""),
    }


def _h_worktree_removed(st: State, ev: Event) -> None:
    it = st.items.get(ev.subject)
    if it:
        it.worktree = ""


_PR_FIELDS = (
    "number",
    "url",
    "forge",
    "base",
    "head",
    "state",
    "review",
    "checks",
    "head_sha",
    "merge_sha",
    "feedback",
    "author_model",
    "queue",
    "queue_position",
    "queue_state",
)


def _pr(st: State, ev: Event) -> tuple[Item, PullRequest]:
    it = _item(st, ev, ev.data.get("kind", "task"))
    if it.pr is None:
        it.pr = PullRequest()
    for key in _PR_FIELDS:
        if key in ev.data:
            setattr(it.pr, key, ev.data[key])
    it.pr.synced_at = ev.ts
    return it, it.pr


def _h_pr_opened(st: State, ev: Event) -> None:
    """Opened OR re-pushed: either way the work is now the reviewers' to judge.

    Feedback from an earlier round is cleared, because it was answered by this push --
    carrying it forward would hand the next agent a list of things already fixed.
    """
    it, pr = _pr(st, ev)
    pr.state = "open"
    pr.review = ev.data.get("review", "pending")
    pr.feedback = ""
    it.state = REVIEW
    it.blocked_reason = ""


def _h_pr_synced(st: State, ev: Event) -> None:
    _pr(st, ev)


def _h_pr_changes_requested(st: State, ev: Event) -> None:
    """Back to the queue, WITH the review attached -- or parked, if the operator said so.

    OPEN rather than RUNNING: nobody holds it, and RUNNING-without-a-lease is the shape
    of a crash. The branch and worktree stay on the item, so the next claim adopts the
    same tree and a push updates the same request.
    """
    it, pr = _pr(st, ev)
    pr.review = "changes_requested"
    pr.requested_head = pr.head_sha
    pr.rounds += 1
    if ev.data.get("block"):
        it.state = BLOCKED
        it.blocked_reason = f"changes requested on {pr.url or 'its pull request'}"
    else:
        it.state = OPEN


def _h_pr_merged(st: State, ev: Event) -> None:
    it, pr = _pr(st, ev)
    pr.state = "merged"
    it.merged_sha = pr.merge_sha or it.merged_sha


def _h_pr_closed(st: State, ev: Event) -> None:
    """Closed without merging is a reviewer's NO, and a no is for a person to read.

    Parked, never silently reopened: re-offering it would send an agent to redo work a
    human just declined, which is a loop with a person in it.
    """
    it, pr = _pr(st, ev)
    pr.state = "closed"
    it.state = BLOCKED
    it.blocked_reason = f"pull request closed without merging: {pr.url}"


def _h_release_opened(st: State, ev: Event) -> None:
    st.pending_releases[ev.subject] = {
        k: ev.data.get(k, "") for k in ("branch", "number", "url", "base", "forge")
    }


def _h_back_merge_recorded(st: State, ev: Event) -> None:
    d = ev.data
    into = d.get("into", "")
    st.back_merges[f"{ev.subject}>{into}"] = {
        "item": ev.subject,
        "into": into,
        **{k: d.get(k, "") for k in ("number", "url", "forge")},
        "state": d.get("state") or "open",
    }


def _h_release_closed(st: State, ev: Event) -> None:
    st.pending_releases.pop(ev.subject, None)


def _h_release_tagged(st: State, ev: Event) -> None:
    d = ev.data
    st.pending_releases.pop(d.get("version", ev.subject), None)
    st.releases.append(
        Release(
            version=d.get("version", ev.subject),
            tag=d.get("tag", ""),
            sha=d.get("sha", ""),
            branch=d.get("branch", ""),
            items=list(d.get("items", [])),
            at=ev.ts,
            pushed=bool(d.get("pushed")),
            line=d.get("line", ""),
        )
    )
