"""`GitHub`, the forge adapter reached through ``gh`` — a second `Forge` adapter.

Kept in its own module so `ddflow.adapters.forge` stays under the 20 KB ceiling. The
port (`Forge`) and the registry (`FORGES`) live in `ddflow.adapters.forge`; this module
holds only `GitHub` (and its helpers), and importing it registers it under its `name`
("github").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import fsio
from .forge import (
    GITHUB,
    THREAD_BODY_MAX,
    ForgeError,
    ForgeUnavailable,
    PRInfo,
    Thread,
    _check,
    _clip,
    _count,
    _json,
    _run,
)

_GH_FIELDS = (
    "number,url,state,isDraft,reviewDecision,statusCheckRollup,baseRefName,"
    "headRefName,headRefOid,mergeCommit,latestReviews,commits"
)
_GH_FAILED = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}
_GH_OK = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def _gh_checks(rollup: list[dict[str, Any]] | None) -> str:
    """One word for a statusCheckRollup: failing beats pending beats passing.

    Two shapes arrive in one list -- a CheckRun has `status`/`conclusion`, a legacy
    StatusContext has `state` -- and reading only one would call every commit status
    from an external CI "passing" by omission.
    """
    if not rollup:
        return ""
    pending = False
    for c in rollup:
        verdict = (c.get("conclusion") or c.get("state") or "").upper()
        if verdict in _GH_FAILED:
            return "failing"
        status = (c.get("status") or "").upper()
        if (status and status != "COMPLETED") or verdict in ("PENDING", "EXPECTED", ""):
            if verdict not in _GH_OK:
                pending = True
    return "pending" if pending else "passing"


def _gh_review(review_decision: str, states: list[str]) -> str:
    """One review word from `reviewDecision` and the `latestReviews` states.

    `reviewDecision` is null on a branch with no required-review rule, so the decision
    has to be read from the reviews themselves -- otherwise, in exactly the
    lightly-protected repos, no approval and no change request is ever seen.
    """
    review = {
        "APPROVED": "approved",
        "CHANGES_REQUESTED": "changes_requested",
        "REVIEW_REQUIRED": "pending",
    }.get(review_decision, "")
    if not review:
        if "CHANGES_REQUESTED" in states:
            review = "changes_requested"
        elif "APPROVED" in states:
            review = "approved"
    return review


def _gh_review_sha(reviews: list[dict[str, Any]], states: list[str], review: str) -> str:
    """The commit the deciding review was made on ("" when none says)."""
    want = {"approved": "APPROVED", "changes_requested": "CHANGES_REQUESTED"}.get(review, "")
    deciding = [r for r, st in zip(reviews, states, strict=True) if st == want]
    return ((deciding[-1].get("commit") or {}).get("oid") or "") if deciding else ""


def _gh_feedback(reviews: list[dict[str, Any]]) -> list[str]:
    """The change-request/comment review bodies, one clipped line each."""
    return [
        f"{(r.get('author') or {}).get('login', '?')} ({r.get('state', '').lower()}): "
        f"{r.get('body', '')}"
        for r in reviews
        if (r.get("state") or "").upper() in ("CHANGES_REQUESTED", "COMMENTED")
        and (r.get("body") or "").strip()
    ]


#: The merge queue is not in `gh pr view --json`'s documented fields; it is GraphQL's
#: `PullRequest.isInMergeQueue` / `mergeQueueEntry`, asked through `gh api graphql` (whose
#: `{owner}`/`{repo}` placeholders gh fills in from the repository it runs in).
_GH_QUEUE_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name)"
    "{pullRequest(number:$number){isInMergeQueue mergeQueueEntry{position state}}}}"
)

_GH_THREADS_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name)"
    "{pullRequest(number:$number){reviewThreads(first:100){nodes{id isResolved isOutdated "
    "path line comments(first:50){totalCount nodes{author{login} body}}}}}}}"
)
_GH_REPLY_MUTATION = (
    "mutation($thread:ID!,$body:String!){addPullRequestReviewThreadReply(input:"
    "{pullRequestReviewThreadId:$thread,body:$body}){comment{id}}}"
)
_GH_RESOLVE_MUTATION = (
    "mutation($thread:ID!){resolveReviewThread(input:{threadId:$thread}){thread{id isResolved}}}"
)


class GitHub:
    name = GITHUB

    def __init__(self, repo: Path):
        self.repo = repo

    def _graphql(self, query: str, what: str, *fields: tuple[str, str]) -> Any:
        argv = ["gh", "api", "graphql", "-f", f"query={query}"]
        for key, value in fields:
            argv += ["-f", f"{key}={value}"]
        data = _json(_run(self.repo, argv), what)
        if isinstance(data, dict) and data.get("errors"):
            errors = data["errors"]
            raise ForgeError(f"{what}: {str(errors[0].get('message', errors))[:300]}")
        return (data or {}).get("data") or {}

    def threads(self, number: int) -> list[Thread]:
        argv = ["gh", "api", "graphql", "-F", "owner={owner}", "-F", "name={repo}"]
        argv += ["-F", f"number={number}", "-f", f"query={_GH_THREADS_QUERY}"]
        data = _json(_run(self.repo, argv), f"review threads of #{number}")
        if isinstance(data, dict) and data.get("errors"):
            raise ForgeError(f"review threads of #{number}: {str(data['errors'])[:300]}")
        try:
            nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        except (KeyError, TypeError) as exc:
            raise ForgeUnavailable(f"review threads of #{number}: unexpected answer") from exc
        out = []
        for n in nodes or []:
            comments = (n.get("comments") or {}).get("nodes") or []
            first = comments[0] if comments else {}
            total = int((n.get("comments") or {}).get("totalCount") or len(comments))
            out.append(
                Thread(
                    id=n.get("id", ""),
                    path=n.get("path") or "",
                    line=int(n.get("line") or 0),
                    resolved=bool(n.get("isResolved")),
                    outdated=bool(n.get("isOutdated")),
                    author=(first.get("author") or {}).get("login", ""),
                    body=(first.get("body") or "").strip()[:THREAD_BODY_MAX],
                    replies=max(0, total - 1),
                )
            )
        return out

    def reply(self, number: int, thread_id: str, body: str) -> None:
        self._graphql(
            _GH_REPLY_MUTATION,
            f"reply on thread {thread_id} of #{number}",
            ("thread", thread_id),
            ("body", body),
        )

    def resolve(self, number: int, thread_id: str) -> None:
        self._graphql(
            _GH_RESOLVE_MUTATION,
            f"resolve thread {thread_id} of #{number}",
            ("thread", thread_id),
        )

    def _queue(self, number: int) -> tuple[str, int, str] | None:
        """(``queued`` | ``""``, position, state), or None when the queue could not be
        asked. A GitHub without merge queues (older Enterprise: the schema has no such
        field) answers "not queued", which is what it is; a rate limit or a network error
        is NOT that answer -- it is "do not know", and nothing here may turn it into an
        ejection."""
        argv = ["gh", "api", "graphql", "-F", "owner={owner}", "-F", "name={repo}"]
        argv += ["-F", f"number={number}", "-f", f"query={_GH_QUEUE_QUERY}"]
        try:
            p = _run(self.repo, argv)
        except ForgeUnavailable:
            return None  # a hung or failing call: the view itself already worked
        text = (p.stdout or "") + (p.stderr or "")
        if "mergeQueueEntry" in text and "doesn't exist" in text:
            return "", 0, ""  # a schema with no merge queues: definitely not queued
        if p.returncode != 0:
            return None
        try:
            pr = json.loads(p.stdout or "{}")["data"]["repository"]["pullRequest"] or {}
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
        entry = pr.get("mergeQueueEntry") or {}
        if not (pr.get("isInMergeQueue") or entry):
            return "", 0, ""
        return "queued", int(entry.get("position") or 0), str(entry.get("state") or "")

    def _info(self, d: dict[str, Any]) -> PRInfo:
        reviews = d.get("latestReviews") or []
        states = [(r.get("state") or "").upper() for r in reviews]
        review = _gh_review((d.get("reviewDecision") or "").upper(), states)
        return PRInfo(
            number=int(d.get("number") or 0),
            url=d.get("url", ""),
            state=(d.get("state") or "OPEN").lower(),
            review=review,
            checks=_gh_checks(d.get("statusCheckRollup")),
            base=d.get("baseRefName", ""),
            head=d.get("headRefName", ""),
            head_sha=d.get("headRefOid", ""),
            merge_sha=((d.get("mergeCommit") or {}).get("oid") or ""),
            draft=bool(d.get("isDraft")),
            feedback=_clip(_gh_feedback(reviews)),
            review_sha=_gh_review_sha(reviews, states, review),
            commits=_count(d.get("commits")),
        )

    def find(self, head: str) -> PRInfo | None:
        rows = _json(
            _run(
                self.repo,
                ["gh", "pr", "list", "--head", head, "--state", "all", "--json", "number,state"],
            ),
            f"gh pr list --head {head}",
        )
        if not rows:
            return None
        # An OPEN request first: a branch can carry a closed one and a newer open one.
        rows.sort(key=lambda r: ((r.get("state") or "").upper() != "OPEN", -int(r["number"])))
        return self.view(int(rows[0]["number"]))

    def view(self, number: int) -> PRInfo:
        d = _json(
            _run(self.repo, ["gh", "pr", "view", str(number), "--json", _GH_FIELDS]),
            f"gh pr view {number}",
        )
        info = self._info(d)
        if info.state == "open":
            got = self._queue(number)
            if got is None:
                info.queue_known = False
            else:
                info.queue, info.queue_position, info.queue_state = got
        if info.review == "changes_requested":
            info.feedback = _clip([info.feedback, self._inline(number)])
        return info

    def _inline(self, number: int) -> str:
        """Line comments. `gh pr view` carries review BODIES only, and the actionable
        part of a review is usually on the lines -- "rename this", "this leaks"."""
        p = _run(
            self.repo,
            ["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{number}/comments"],
        )
        if p.returncode != 0:
            return "(line comments could not be fetched; read them on the request)"
        try:
            rows = json.loads(p.stdout or "[]")
        except json.JSONDecodeError:
            return "(line comments could not be parsed; read them on the request)"
        return "\n".join(
            f"{r.get('path', '?')}:{r.get('line') or r.get('original_line') or '?'} "
            f"{(r.get('user') or {}).get('login', '?')}: {r.get('body', '')}"
            for r in rows
        )

    def create(self, *, head, base, title, body, draft, labels, reviewers) -> PRInfo:
        with fsio.temp_text(body, ".md") as body_file:
            argv = ["gh", "pr", "create", "--head", head, "--base", base, "--title", title]
            argv += ["--body-file", str(body_file)]
            if draft:
                argv.append("--draft")
            for label in labels:
                argv += ["--label", label]
            for r in reviewers:
                argv += ["--reviewer", r]
            _check(_run(self.repo, argv), f"gh pr create --head {head}")
        found = self.find(head)
        if found is None:
            raise ForgeUnavailable(
                f"gh pr create reported success but no request exists for {head}"
            )
        return found

    def merge(self, number: int, *, strategy: str, head_sha: str, auto: bool) -> str:
        flag = {"squash": "--squash", "ff-only": "--rebase"}.get(strategy, "--merge")
        argv = ["gh", "pr", "merge", str(number), flag]
        if head_sha:
            # The head the reviewers approved. A push after the approval must not ride
            # into the base on it.
            argv += ["--match-head-commit", head_sha]
        if auto:
            argv.append("--auto")
        return _check(_run(self.repo, argv), f"gh pr merge {number}").strip()

    def set_base(self, number: int, base: str) -> None:
        _check(
            _run(self.repo, ["gh", "pr", "edit", str(number), "--base", base]),
            f"gh pr edit {number} --base {base}",
        )
