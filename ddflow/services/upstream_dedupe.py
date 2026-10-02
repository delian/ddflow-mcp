"""Is this report already filed? The duplicate check before an upstream report.

Decision D-upstream-reporting; research R-upstream-reporting. Two places are asked:

* the local queue, through any ``services.similar`` matcher (optional);
* the issues of the upstream repository, listed with an unauthenticated GET through an
  INJECTABLE ``fetch`` function and indexed with ``similar.build``.

A hit at ``ask_threshold`` offers "add a comment to #N" (with the issue's URL) instead of
a new issue; hits between ``show_floor`` and ``ask_threshold`` are listed. This module
only reads: nothing is sent anywhere, and the single I/O seam is ``fetch``.

Failing to ask is not an answer. A network error, a timeout, a rate limit, a 4xx/5xx or
an unreadable body makes the upstream state ``unavailable`` with the reason -- never
"no duplicates found" (the discipline of ``infra.forge``: ``ForgeUnavailable`` is not an
empty answer). An upstream with zero issues IS an answer: ``no_issues``.
"""

from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

from . import similar

DEFAULT_REPO = "delian/ddflow-mcp"
ASK_THRESHOLD = 0.55
SHOW_FLOOR = 0.35
PER_PAGE = 100
MAX_PAGES = 3
TIMEOUT = 10.0
#: bytes read from one response: a page of 100 issues is well under this.
MAX_BYTES = 8_000_000

_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_.-]{1,100}")


class UpstreamUnavailable(RuntimeError):
    """The upstream could not be asked (or answered unreadably). NOT an empty answer."""


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: str


#: ``fetch(url, timeout) -> Response``; it may raise ``OSError`` (URLError, timeouts).
Fetch = Callable[[str, float], Response]


def real_fetch(url: str, timeout: float) -> Response:
    """The one real network call: an unauthenticated GET. HTTP error statuses come back
    as a ``Response`` (the caller classifies them); transport failures raise OSError."""
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "ddflow-dedupe"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 - https only
            return Response(r.status, dict(r.headers), r.read(MAX_BYTES).decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return Response(exc.code, dict(exc.headers or {}), "")


@dataclass(frozen=True)
class Hit:
    source: str  # "local" | "upstream"
    id: str
    title: str
    score: float
    band: str  # "ask" | "show"
    url: str = ""
    kind: str = ""  # local hits: what the record is (bug, task...); the matcher keeps no title


@dataclass
class Result:
    #: "ok" when every place asked answered; "unavailable" when any could not.
    status: str = "ok"
    #: "checked" | "no_issues" | "unavailable"
    upstream_state: str = "checked"
    #: "checked" | "not_checked" | "unavailable"
    local_state: str = "not_checked"
    reason: str = ""  # why the check is unavailable: both places' reasons, joined
    upstream_reason: str = ""
    local_reason: str = ""
    upstream: list[Hit] = field(default_factory=list)
    local: list[Hit] = field(default_factory=list)
    #: the upstream hit to comment on instead of filing a new issue, when one is close
    offer: Hit | None = None
    truncated: bool = False
    repo: str = ""
    issues_seen: int = 0

    def summary(self) -> str:
        lines: list[str] = []
        if self.upstream_state == "unavailable":
            lines.append(f"upstream check unavailable: {self.upstream_reason}")
        elif self.upstream_state == "no_issues":
            lines.append(f"no upstream issues in {self.repo} to compare with")
        else:
            seen = f"{self.issues_seen} upstream issue(s) of {self.repo} compared"
            if self.truncated:
                seen += f" (only the first {self.issues_seen}; the listing was capped)"
            lines.append(seen)
        if self.offer:
            o = self.offer
            lines.append(f"add a comment to {o.id} instead of a new issue: {o.url}  ({o.title})")
        for h in self.upstream:
            if h is not self.offer:
                lines.append(f"  similar upstream {h.id} ({h.score:.2f}): {h.title}  {h.url}")
        if self.local_state == "unavailable":
            lines.append(f"local check unavailable: {self.local_reason}")
        for h in self.local:
            tag = "likely already filed locally" if h.band == "ask" else "similar locally"
            lines.append(f"  {tag}: {h.kind} {h.id} ({h.score:.2f})")
        return "\n".join(lines)


def report_text(bundle) -> tuple[str, str]:
    """The (title, body) to check, from a ``bugreport.Bundle``: the redacted title and
    what the reporter expected."""
    return str(bundle.data.get("title") or ""), str(bundle.data.get("expected") or "")


def _band(score: float, ask: float, floor: float) -> str:
    return "ask" if score >= ask else "show" if score >= floor else ""


def _rate_limited(r: Response) -> bool:
    h = {k.lower(): v for k, v in r.headers.items()}
    return (
        r.status == HTTPStatus.TOO_MANY_REQUESTS
        or "retry-after" in h
        or (h.get("x-ratelimit-remaining", "").strip() == "0")
    )


def _page(fetch: Fetch, repo: str, page: int, timeout: float) -> tuple[list[dict], bool]:
    """One page of issues and whether the rate limit is spent. Raises
    ``UpstreamUnavailable`` for anything but a readable list."""
    url = f"https://api.github.com/repos/{repo}/issues?state=all&per_page={PER_PAGE}&page={page}"
    try:
        r = fetch(url, timeout)
    except UpstreamUnavailable:
        raise
    except TimeoutError as exc:
        raise UpstreamUnavailable(f"upstream timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise UpstreamUnavailable(f"upstream unreachable: {exc}") from exc
    except http.client.HTTPException as exc:  # a cut-short or garbled response
        raise UpstreamUnavailable(f"upstream answer unreadable: {exc!r}") from exc
    spent = _rate_limited(r)
    if r.status in (HTTPStatus.FORBIDDEN, HTTPStatus.TOO_MANY_REQUESTS):
        if spent:
            raise UpstreamUnavailable(f"GitHub rate limit reached (HTTP {r.status})")
        raise UpstreamUnavailable(f"GitHub refused the request (HTTP {r.status})")
    if r.status != HTTPStatus.OK:
        raise UpstreamUnavailable(f"GitHub answered HTTP {r.status}")
    try:
        data: Any = json.loads(r.body or "null")
    except ValueError as exc:
        raise UpstreamUnavailable("upstream answer unparseable (not JSON)") from exc
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]  # the shape of a GitHub search/issues response
    if not isinstance(data, list):
        raise UpstreamUnavailable("upstream answer unparseable (not an issue list)")
    if not all(isinstance(d, dict) and isinstance(d.get("number"), int) for d in data):
        raise UpstreamUnavailable("upstream answer unparseable (items are not issues)")
    return data, spent


def list_issues(
    repo: str, fetch: Fetch, *, max_pages: int = MAX_PAGES, timeout: float = TIMEOUT
) -> tuple[list[dict], bool]:
    """Issues (pull requests removed) and whether the listing was capped. Raises
    ``UpstreamUnavailable``; a partial listing is never returned."""
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1: an unasked upstream is not 'no issues'")
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        items, spent = _page(fetch, repo, page, timeout)
        out += [i for i in items if "pull_request" not in i]
        if len(items) < PER_PAGE:
            return out, False
        if spent:
            raise UpstreamUnavailable("GitHub rate limit reached (no requests remaining)")
    return out, True


def check(
    title: str,
    body: str = "",
    *,
    repo: str = DEFAULT_REPO,
    fetch: Fetch | None = None,
    local: similar.Matcher | None = None,
    ask_threshold: float = ASK_THRESHOLD,
    show_floor: float = SHOW_FLOOR,
    max_pages: int = MAX_PAGES,
    timeout: float = TIMEOUT,
) -> Result:
    """Check a report against the local queue (``local``, when given) and the issues of
    ``repo``. ``fetch`` defaults to the real unauthenticated GET."""
    if not _REPO.fullmatch(repo or "") or ".." in repo:
        raise ValueError(f"upstream repo {repo!r} must look like owner/name")
    res = Result(repo=repo)
    record = {"title": title, "body": body}
    if local is not None:
        try:
            hits = local.query(record)
        except (LookupError, OSError) as exc:
            res.local_state, res.status = "unavailable", "unavailable"
            res.local_reason = f"local index: {exc}"
        else:
            res.local_state = "checked"
            for rid, score in hits:
                band = _band(score, ask_threshold, show_floor)
                if band:
                    d = local.doc(rid)
                    res.local.append(Hit("local", rid, "", score, band, kind=d.kind if d else ""))
    try:
        issues, res.truncated = list_issues(
            repo, fetch or real_fetch, max_pages=max_pages, timeout=timeout
        )
    except UpstreamUnavailable as exc:
        res.upstream_state, res.status = "unavailable", "unavailable"
        res.upstream_reason = str(exc)
        res.reason = "; ".join(r for r in (res.local_reason, res.upstream_reason) if r)
        return res
    res.reason = res.local_reason
    res.issues_seen = len(issues)
    if not issues and res.truncated:
        # Every listed entry was a pull request and the listing was capped: older
        # issues were never seen, so this is not "no issues".
        res.upstream_state, res.status = "unavailable", "unavailable"
        res.upstream_reason = "the capped listing held only pull requests; no issue was compared"
        res.reason = "; ".join(r for r in (res.local_reason, res.upstream_reason) if r)
        return res
    if not issues:
        res.upstream_state = "no_issues"
        return res
    meta = {
        f"#{i['number']}": (str(i.get("title") or ""), str(i.get("html_url") or ""))
        for i in issues
        if isinstance(i.get("number"), int)
    }
    index = similar.build(
        {
            "id": f"#{i['number']}",
            "kind": "issue",
            "title": str(i.get("title") or ""),
            "body": str(i.get("body") or ""),
        }
        for i in issues
        if isinstance(i.get("number"), int)
    )
    for rid, score in index.query(record):
        band = _band(score, ask_threshold, show_floor)
        if band:
            t, url = meta[rid]
            res.upstream.append(Hit("upstream", rid, t, score, band, url))
    res.offer = next((h for h in res.upstream if h.band == "ask"), None)
    return res
