"""`[docs].check_links = "network"` for the documentation check: do the http(s) links answer?

A read-only GET per distinct address (like ``upgrade.release_check``: nothing is written
anywhere). Only a definite "gone" -- the server answered 404 or 410 -- is a ``link`` finding.
A server that answers something else (403, 429, 5xx) said nothing about the page, and a
network that cannot be reached says nothing about any link: those are counted, and the
check reports itself ``unavailable`` rather than passing the links it could not look at.
"""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field

from .docscheck import _LINK, _REFDEF
from .docscheck_md import lines
from .docscheck_types import Finding

#: Statuses that mean the page is gone, as opposed to refusing or failing to answer.
GONE = (404, 410)


@dataclass
class RemoteResult:
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0
    #: Why the links could not all be checked ("" when every address answered).
    unavailable: str = ""


def remote_links(texts: Mapping[str, str]) -> list[tuple[str, int, str]]:
    """(document, line, url) for every http(s) link in ``texts``, prose lines only."""
    out = []
    for path in sorted(texts):
        for n, line, in_code in lines(texts[path]):
            if in_code:
                continue
            hits = [m.group(1) for m in _LINK.finditer(line)]
            ref = _REFDEF.match(line)
            hits += [ref.group(1)] if ref else []
            out += [(path, n, u) for u in hits if u.lower().startswith(("http://", "https://"))]
    return out


def _verdict(url: str, timeout_s: int) -> tuple[str, str]:
    """("ok" | "gone" | "refused" | "silent", why): what the address said to a GET."""
    req = urllib.request.Request(url, headers={"User-Agent": "ddflow-docs-check"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s):  # nosec B310 -- http(s) only
            return "ok", ""
    except urllib.error.HTTPError as exc:
        return ("gone" if exc.code in GONE else "refused"), str(exc.code)
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        return "silent", str(getattr(exc, "reason", exc))


def check_remote(texts: Mapping[str, str], timeout_s: int) -> RemoteResult:
    """GET each distinct http(s) link of ``texts`` once."""
    result = RemoteResult()
    seen: dict[str, tuple[str, str]] = {}
    for path, n, url in remote_links(texts):
        if url not in seen:
            seen[url] = _verdict(url, timeout_s)
            result.checked += 1
        if seen[url][0] == "gone":
            result.findings.append(
                Finding("link", path, n, url, f"the server answered {seen[url][1]}")
            )
    silent = [why for kind, why in seen.values() if kind == "silent"]
    refused = sum(kind == "refused" for kind, _ in seen.values())
    if silent or refused:
        result.unavailable = (
            f"{len(silent)} of {result.checked} remote link(s) could not be reached"
            f"{' (' + silent[0] + ')' if silent else ''}; {refused} answered with an error "
            "that says nothing about the page"
        )
    return result
