"""Pull/merge requests, through the forge CLI the operator already authenticated.

ddflow holds no token and speaks no HTTP. It shells out to ``gh`` (GitHub) or ``glab``
(GitLab), exactly as a person at the same terminal would, so every permission question —
who may open, approve, merge — is answered by the forge's own rules and the operator's own
login, never by ddflow (RESEARCH R16). Branch protection stays the authority: when ddflow
asks to merge and the forge says no, that is the answer.

Two outcomes are kept apart the whole way up, for the reason `unavailable` is a gate
outcome: a forge that could not be ASKED (CLI missing, not logged in, network down) is
not a forge that said "nothing changed". `ForgeUnavailable` is the first; an ordinary
result is the second.

Only documented CLI contracts are used: ``gh pr list|view|create|merge|edit`` with
``--json`` fields from the gh manual, and for GitLab the REST API through ``glab api``,
whose JSON is the API's own documented shape (``glab mr view -F json`` is the CLI's, and
less stable across versions).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.textcut import clip
from . import fsio
from . import proc as P
from .worktree import git

GITHUB, GITLAB = "github", "gitlab"

#: How much review text is carried into the event log. A review is read by the next agent
#: from its brief; a 40 KB thread would crowd out the lessons the brief exists to deliver.
FEEDBACK_MAX = 4000


class ForgeUnavailable(RuntimeError):
    """The forge could not be asked. NOT the same as an empty answer."""


class ForgeError(RuntimeError):
    """The forge was asked and refused (a protected branch, a stale head, a bad base)."""


@dataclass
class PRInfo:
    number: int
    url: str = ""
    state: str = "open"  # open | merged | closed
    review: str = ""  # approved | changes_requested | pending | ""
    checks: str = ""  # passing | failing | pending | ""
    base: str = ""
    head: str = ""
    head_sha: str = ""
    merge_sha: str = ""
    draft: bool = False
    feedback: str = ""
    #: The commit the DECIDING review (the approval, or the change request) was made on.
    #: "" when the forge does not say. An approval of an older head is not an approval of
    #: this one, and a change request on an older head was answered by the push since.
    review_sha: str = ""
    #: Merge queue (B172). `gh pr merge` on a queue-protected branch ENQUEUES instead of
    #: merging: the request stays open while the queue builds it. "queued" while it has an
    #: entry, "" otherwise; the position is 1 for the next to merge; the state is the
    #: queue's own word for the entry (QUEUED, AWAITING_CHECKS, MERGEABLE, UNMERGEABLE,
    #: LOCKED). An open request that WAS queued and no longer is, was ejected.
    queue: str = ""
    queue_position: int = 0
    queue_state: str = ""
    #: False when the queue could not be ASKED (rate limit, network, auth): "not queued"
    #: would then be a guess, and a guess that reads as an ejection is worse than none.
    queue_known: bool = True
    #: How many commits the request carries (0 when the forge does not say). A rebase-merge
    #: lands exactly this many on the base; a squash or a merge commit lands one (B178).
    commits: int = 0

    def event_data(self, forge: str) -> dict[str, Any]:
        return {
            "number": self.number,
            "url": self.url,
            "forge": forge,
            "base": self.base,
            "head": self.head,
            "state": self.state,
            "review": self.review,
            "checks": self.checks,
            "head_sha": self.head_sha,
            "merge_sha": self.merge_sha,
            "feedback": self.feedback,
            "queue": self.queue,
            "queue_position": self.queue_position,
            "queue_state": self.queue_state,
            "queue_known": self.queue_known,
        }


@dataclass
class Thread:
    """One review thread: a conversation anchored to a line, resolved or not (B176).

    The forge's own id is what `reply` and `resolve` take, so it is carried verbatim.
    """

    id: str
    path: str = ""
    line: int = 0
    resolved: bool = False
    outdated: bool = False
    author: str = ""
    body: str = ""  # the opening comment, clipped
    replies: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "line": self.line,
            "resolved": self.resolved,
            "outdated": self.outdated,
            "author": self.author,
            "body": self.body,
            "replies": self.replies,
        }


#: One thread's opening comment is read for what it asks, not archived.
THREAD_BODY_MAX = 600


def _run(repo: Path, argv: list[str], *, timeout: int = 120) -> Any:
    if shutil.which(argv[0]) is None:
        raise ForgeUnavailable(
            f"`{argv[0]}` is not installed, so the forge cannot be reached. Install and "
            f"log in (`{argv[0]} auth login`), or set [flow].integration = 'merge'."
        )
    done = P.capture(argv, cwd=str(repo), timeout=timeout)
    if done.timed_out:
        raise ForgeUnavailable(f"`{' '.join(argv[:3])}` timed out after {timeout}s") from done.error
    return done.unwrap()


#: stderr fragments meaning "could not ASK" rather than "asked, and the answer is no".
_UNAVAILABLE_HINTS = (
    "auth login",
    "not logged",
    "authentication",
    "401",
    "could not resolve host",
    "connection refused",
    "network is unreachable",
    "timed out",
    "tls handshake",
)


def _check(p, what: str) -> str:
    if p.returncode == 0:
        return p.stdout
    err = (p.stderr or p.stdout or "").strip()
    if any(h in err.lower() for h in _UNAVAILABLE_HINTS):
        raise ForgeUnavailable(f"{what}: {err[:400]}")
    raise ForgeError(f"{what}: {err[:400]}")


def _json(p, what: str) -> Any:
    out = _check(p, what)
    try:
        return json.loads(out or "null")
    except json.JSONDecodeError as exc:
        # Unparseable output is the forge CLI changing under us -- not a "no".
        raise ForgeUnavailable(f"{what}: unparseable output {out[:200]!r}") from exc


def _count(commits: Any) -> int:
    """How many commits `gh pr view --json commits` lists: it flattens the GraphQL
    connection to an array, but a connection-shaped answer is read too."""
    if isinstance(commits, dict):
        return int(commits.get("totalCount") or len(commits.get("nodes") or []))
    return len(commits or [])


def _clip(parts: list[str]) -> str:
    text = "\n\n".join(p.strip() for p in parts if p and p.strip())
    return clip(
        text, FEEDBACK_MAX, keep=FEEDBACK_MAX - 40, marker="\n\n[... truncated; see the request]"
    )


class Forge:
    name = ""

    def __init__(self, repo: Path):
        self.repo = repo

    def find(self, head: str) -> PRInfo | None:  # pragma: no cover - interface
        raise NotImplementedError

    def create(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool,
        labels: list[str],
        reviewers: list[str],
    ) -> PRInfo:  # pragma: no cover - interface
        raise NotImplementedError

    def view(self, number: int) -> PRInfo:  # pragma: no cover - interface
        raise NotImplementedError

    def merge(self, number: int, *, strategy: str, head_sha: str, auto: bool) -> str:
        raise NotImplementedError  # pragma: no cover - interface

    def set_base(self, number: int, base: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def threads(self, number: int) -> list[Thread]:  # pragma: no cover - interface
        raise ForgeError(f"{self.name} review threads are not supported")

    def reply(self, number: int, thread_id: str, body: str) -> None:  # pragma: no cover
        raise ForgeError(f"{self.name} review threads are not supported")

    def resolve(self, number: int, thread_id: str) -> None:  # pragma: no cover - interface
        raise ForgeError(f"{self.name} review threads are not supported")


# -- GitHub ---------------------------------------------------------------------------

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


class GitHub(Forge):
    name = GITHUB

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
        review = {
            "APPROVED": "approved",
            "CHANGES_REQUESTED": "changes_requested",
            "REVIEW_REQUIRED": "pending",
        }.get((d.get("reviewDecision") or "").upper(), "")
        reviews = d.get("latestReviews") or []
        states = [(r.get("state") or "").upper() for r in reviews]
        if not review:
            # `reviewDecision` is null on a branch with no required-review rule, so the
            # decision has to be read from the reviews themselves -- otherwise, in exactly
            # the lightly-protected repos, no approval and no change request is ever seen.
            if "CHANGES_REQUESTED" in states:
                review = "changes_requested"
            elif "APPROVED" in states:
                review = "approved"
        want = {"approved": "APPROVED", "changes_requested": "CHANGES_REQUESTED"}.get(review, "")
        deciding = [r for r, st in zip(reviews, states, strict=True) if st == want]
        review_sha = ((deciding[-1].get("commit") or {}).get("oid") or "") if deciding else ""
        feedback = [
            f"{(r.get('author') or {}).get('login', '?')} ({r.get('state', '').lower()}): "
            f"{r.get('body', '')}"
            for r in d.get("latestReviews") or []
            if (r.get("state") or "").upper() in ("CHANGES_REQUESTED", "COMMENTED")
            and (r.get("body") or "").strip()
        ]
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
            feedback=_clip(feedback),
            review_sha=review_sha,
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


# -- GitLab ---------------------------------------------------------------------------

_GL_PIPELINE = {
    "success": "passing",
    "failed": "failing",
    "canceled": "failing",
    "running": "pending",
    "pending": "pending",
    "created": "pending",
    "waiting_for_resource": "pending",
    "preparing": "pending",
    "scheduled": "pending",
    "manual": "pending",
}


class GitLab(Forge):
    name = GITLAB

    def _api(self, *args: str, what: str) -> Any:
        return _json(_run(self.repo, ["glab", "api", *args]), what)

    def _info(self, d: dict[str, Any], approved: bool) -> PRInfo:
        state = {"opened": "open", "locked": "open"}.get(d.get("state", ""), d.get("state", ""))
        status = d.get("detailed_merge_status") or ""
        if status == "requested_changes":
            review = "changes_requested"
        elif approved:
            review = "approved"
        else:
            review = "pending"
        pipeline = (d.get("head_pipeline") or {}).get("status") or ""
        return PRInfo(
            number=int(d.get("iid") or 0),
            url=d.get("web_url", ""),
            state=state or "open",
            review=review,
            checks=_GL_PIPELINE.get(pipeline, ""),
            base=d.get("target_branch", ""),
            head=d.get("source_branch", ""),
            head_sha=d.get("sha") or "",
            merge_sha=d.get("merge_commit_sha") or d.get("squash_commit_sha") or "",
            draft=bool(d.get("draft")),
        )

    def find(self, head: str) -> PRInfo | None:
        rows = self._api(
            "-X",
            "GET",
            "projects/:id/merge_requests",
            "-f",
            f"source_branch={head}",
            "-f",
            "state=all",
            what=f"list merge requests for {head}",
        )
        if not rows:
            return None
        rows.sort(key=lambda r: (r.get("state") != "opened", -int(r["iid"])))
        return self.view(int(rows[0]["iid"]))

    def view(self, number: int) -> PRInfo:
        d = self._api("-X", "GET", f"projects/:id/merge_requests/{number}", what=f"view !{number}")
        ap = self._api(
            "-X",
            "GET",
            f"projects/:id/merge_requests/{number}/approvals",
            what=f"approvals !{number}",
        )
        # `approved` alone is true on a project with no approval rules at all -- "nobody
        # needed to approve" is not "somebody approved". A named approver is required.
        approved = bool(ap and ap.get("approved") and ap.get("approved_by"))
        info = self._info(d, approved)
        if info.review == "changes_requested":
            notes = self._api(
                "-X",
                "GET",
                f"projects/:id/merge_requests/{number}/notes",
                "-f",
                "sort=asc",
                what=f"notes !{number}",
            )
            info.feedback = _clip(
                [
                    f"{(n.get('position') or {}).get('new_path', '')}"
                    f"{':' + str((n.get('position') or {}).get('new_line')) if (n.get('position') or {}).get('new_line') else ''} "
                    f"{(n.get('author') or {}).get('username', '?')}: {n.get('body', '')}"
                    for n in notes or []
                    if not n.get("system")
                ]
            )
        return info

    def create(self, *, head, base, title, body, draft, labels, reviewers) -> PRInfo:
        argv = ["glab", "mr", "create", "--source-branch", head, "--target-branch", base]
        argv += ["--title", title, "--description", body, "--yes"]
        if draft:
            argv.append("--draft")
        if labels:
            argv += ["--label", ",".join(labels)]
        if reviewers:
            argv += ["--reviewer", ",".join(reviewers)]
        _check(_run(self.repo, argv), f"glab mr create --source-branch {head}")
        found = self.find(head)
        if found is None:
            raise ForgeUnavailable(
                f"glab mr create reported success but no request exists for {head}"
            )
        return found

    def merge(self, number: int, *, strategy: str, head_sha: str, auto: bool) -> str:
        args = ["-X", "PUT", f"projects/:id/merge_requests/{number}/merge"]
        args += ["-f", f"squash={'true' if strategy == 'squash' else 'false'}"]
        if head_sha:
            args += ["-f", f"sha={head_sha}"]
        if auto:
            # `auto_merge` is the current name; `merge_when_pipeline_succeeds` the one
            # older instances know. GitLab ignores a parameter it does not recognise.
            args += ["-f", "auto_merge=true", "-f", "merge_when_pipeline_succeeds=true"]
        d = self._api(*args, what=f"merge !{number}")
        return (d or {}).get("web_url", "") if isinstance(d, dict) else ""

    def threads(self, number: int) -> list[Thread]:
        rows = self._api(
            "-X",
            "GET",
            f"projects/:id/merge_requests/{number}/discussions",
            "-f",
            "per_page=100",
            what=f"discussions of !{number}",
        )
        out = []
        for d in rows or []:
            notes = [n for n in d.get("notes") or [] if not n.get("system")]
            if not notes or not any(n.get("resolvable") for n in notes):
                continue  # a plain comment is not a thread that can be resolved
            first = notes[0]
            pos = first.get("position") or {}
            resolvable = [n for n in notes if n.get("resolvable")]
            out.append(
                Thread(
                    id=str(d.get("id", "")),
                    path=pos.get("new_path") or pos.get("old_path") or "",
                    line=int(pos.get("new_line") or pos.get("old_line") or 0),
                    resolved=all(n.get("resolved") for n in resolvable),
                    author=(first.get("author") or {}).get("username", ""),
                    body=(first.get("body") or "").strip()[:THREAD_BODY_MAX],
                    replies=len(notes) - 1,
                )
            )
        return out

    def reply(self, number: int, thread_id: str, body: str) -> None:
        self._api(
            "-X",
            "POST",
            f"projects/:id/merge_requests/{number}/discussions/{thread_id}/notes",
            "-f",
            f"body={body}",
            what=f"reply on discussion {thread_id} of !{number}",
        )

    def resolve(self, number: int, thread_id: str) -> None:
        self._api(
            "-X",
            "PUT",
            f"projects/:id/merge_requests/{number}/discussions/{thread_id}",
            "-f",
            "resolved=true",
            what=f"resolve discussion {thread_id} of !{number}",
        )

    def set_base(self, number: int, base: str) -> None:
        self._api(
            "-X",
            "PUT",
            f"projects/:id/merge_requests/{number}",
            "-f",
            f"target_branch={base}",
            what=f"retarget !{number}",
        )


def detect(repo: Path, cfg: Config) -> Forge:
    """The forge for this repository. Raises ForgeUnavailable when it cannot be told.

    'auto' reads the remote URL. A self-hosted forge on a domain naming neither is
    common, so the fallback is whichever ONE CLI is installed -- and when both or neither
    are, the answer is to ask the operator to set it, never to guess: opening a request
    on the wrong forge is not an error anything downstream would catch.
    """
    name = cfg.flow.forge
    if name == "auto":
        url = git(repo, "remote", "get-url", cfg.flow.remote)
        low = url.out.lower() if url.ok else ""
        if "github" in low:
            name = GITHUB
        elif "gitlab" in low:
            name = GITLAB
        else:
            have = [n for n, b in ((GITHUB, "gh"), (GITLAB, "glab")) if shutil.which(b)]
            if len(have) != 1:
                raise ForgeUnavailable(
                    f"cannot tell which forge hosts remote {cfg.flow.remote!r} "
                    f"({url.out or url.err or 'no URL'}). Set [flow].forge to github or gitlab."
                )
            name = have[0]
    if name == GITHUB:
        return GitHub(repo)
    if name == GITLAB:
        return GitLab(repo)
    raise ForgeUnavailable(f"[flow].forge = {name!r} is not github, gitlab or auto")
