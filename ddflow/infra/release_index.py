"""The release index client: the newest released version of a distribution.

Decision D-self-upgrade (1). One GET of the index's public version list -- the JSON document a
Python package index serves at ``<base>/<distribution>/json`` -- with a short timeout and
nothing else in the request: no project data, no identifier, no telemetry. Standard library
only (`urllib`), and the transport is injectable (`fetch`) so tests never touch a network.

Every failure is ONE exception, `ReleaseIndexError`, because the caller's answer to all of
them is the same: say nothing and try again later.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from urllib.parse import urlparse

from ..core.events import version_key

#: The public Python package index's JSON API; `[upgrade].index_url` replaces it for a
#: private mirror that serves the same document.
DEFAULT_INDEX_URL = "https://pypi.org/pypi"
#: Seconds the request may take: the check runs inside a brief and must never hold it up.
TIMEOUT_S = 3.0
#: The most bytes read of a reply (the full ddflow-mcp document is well under this).
MAX_BYTES = 4_000_000

Fetch = Callable[[str, float], bytes]


class ReleaseIndexError(Exception):
    """The index could not be asked or its answer could not be read."""


def is_prerelease(version: str) -> bool:
    """True for ``0.2.0rc1``, ``0.2.0.dev3``, ... (`version_key` ranks a final release 1)."""
    key = version_key(version)
    return bool(key) and key[1][0] == 0


def url_for(dist: str, index_url: str = "") -> str:
    """The document's address. Only http(s): a ``file:`` or other scheme in a config file is
    refused rather than read."""
    base = (index_url or DEFAULT_INDEX_URL).strip().rstrip("/")
    if urlparse(base).scheme not in ("http", "https"):
        raise ReleaseIndexError(f"index_url must be an http(s) address, not {base!r}")
    return f"{base}/{dist}/json"


def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(MAX_BYTES)


def _candidates(doc: dict) -> list[str]:
    """The versions of an index document that have files and are not yanked; when it lists no
    releases at all (a mirror serving only the summary), the summary's own version."""
    releases = doc.get("releases")
    found: list[str] = []
    if isinstance(releases, dict):
        for version, files in releases.items():
            if not isinstance(files, list) or not files:
                continue  # a version nobody uploaded files for
            if all(isinstance(f, dict) and f.get("yanked") for f in files):
                continue
            found.append(str(version))
    info = doc.get("info")
    if (
        not found
        and not releases
        and isinstance(info, dict)
        and isinstance(info.get("version"), str)
    ):
        found.append(info["version"])
    return found


def newest(
    dist: str,
    *,
    index_url: str = "",
    prereleases: bool = False,
    timeout: float = TIMEOUT_S,
    fetch: Fetch | None = None,
) -> str:
    """The newest released version of ``dist``, pre-releases only when asked for.

    Yanked releases and releases without files are skipped. Raises `ReleaseIndexError` for
    an unreachable index, a bad reply or no usable release."""
    url = url_for(dist, index_url)
    try:
        doc = json.loads((fetch or _get)(url, timeout))
    except ReleaseIndexError:
        raise
    except Exception as exc:  # URLError, timeouts, TLS, bad JSON, anything an injected fetch raises
        raise ReleaseIndexError(f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ReleaseIndexError("the reply is not a JSON object")
    candidates = _candidates(doc)
    usable = [v for v in candidates if version_key(v) and (prereleases or not is_prerelease(v))]
    if not usable:
        raise ReleaseIndexError("the index lists no usable release")
    return max(usable, key=lambda v: (version_key(v), v))
