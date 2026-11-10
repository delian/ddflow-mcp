"""The `Forge` port: pull/merge requests, review threads and remote CI through a forge's CLI.

Home of the forge family. The port is `Forge`, declared below next to its default adapters,
each registered in `FORGES` under its `name` -- the worked example of a multi-adapter family
(`GitHub` lives in `ddflow.adapters.forge_github`, `GitLab` in `ddflow.adapters.forge_gitlab`,
each registered lazily, so the modules stay under the 20 KB ceiling). A forge reached through
REST is a later adapter in the same registry (`FORGES.lazy(...)`); a new forge is a new
module, never an edit to a shared one.

ddflow holds no token and speaks no HTTP here. It shells out to ``gh`` (GitHub) or ``glab``
(GitLab), exactly as a person at the same terminal would, so every permission question --
who may open, approve, merge -- is answered by the forge's own rules and the operator's own
login, never by ddflow (RESEARCH R16). Branch protection stays the authority: when ddflow
asks to merge and the forge says no, that is the answer.

Two outcomes are kept apart the whole way up, for the reason `unavailable` is a gate
outcome: a forge that could not be ASKED (CLI missing, not logged in, network down) is
not a forge that said "nothing changed". `ForgeUnavailable` is the first; an ordinary
result is the second.

Only documented CLI contracts are used: ``gh pr list|view|create|merge|edit`` with
``--json`` fields from the gh manual, and for GitLab the REST API through ``glab api``,
whose JSON is the API's own documented shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..core.textcut import clip
from . import proc as P
from .fs import which
from .ports import port
from .registry import Registry

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
    if which(argv[0]) is None:
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


@port
class Forge(Protocol):
    """One forge, reached through its CLI, holding no token and speaking no HTTP.

    A multi-adapter family: `GitHub` and `GitLab` are two of its adapters, each with a
    `name`, registered in `FORGES` (keyed by `name`). `name` is data, so this port is not
    `runtime_checkable`: a runtime `isinstance` check refuses a data member; the registry
    reads it.

    Home: `ddflow.adapters.forge` (compat re-export `ddflow.infra.forge`).
    """

    name: str

    def find(self, head: str) -> PRInfo | None:
        """The request for `head`, or None."""

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
    ) -> PRInfo:
        """Open the request."""

    def view(self, number: int) -> PRInfo:
        """The request's current state."""

    def merge(self, number: int, *, strategy: str, head_sha: str, auto: bool) -> str:
        """Merge (or enqueue) the request; the forge's answer."""

    def set_base(self, number: int, base: str) -> None:
        """Change the request's base branch."""

    def threads(self, number: int) -> list[Thread]:
        """The review threads."""

    def reply(self, number: int, thread_id: str, body: str) -> None:
        """Reply to a review thread."""

    def resolve(self, number: int, thread_id: str) -> None:
        """Mark a review thread resolved."""


#: The forge adapters, keyed by their `name`. `GitHub` and `GitLab` register when first
#: named (their modules stay under the 20 KB ceiling); a `GitHubRest` or `Gitea` adapter
#: is the same, one more lazy entry, never an edit to a shared file.
FORGES: Registry[type[Forge]] = Registry("forge")
FORGES.lazy(GITHUB, "ddflow.adapters.forge_github:GitHub")
FORGES.lazy(GITLAB, "ddflow.adapters.forge_gitlab:GitLab")
