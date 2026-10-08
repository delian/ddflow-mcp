"""Delivering a report bundle upstream (D-upstream-reporting (4), (5)).

Three routes, in two kinds. PREPARATION sends nothing and needs no consent: a local file
under `.ddflow/local/reports/` and a prefilled issue URL (which this module never opens).
SENDING (`gh issue create`) requires a `Consent` whose digest equals the digest of the exact
bytes being sent, which is not expired and not already used; with none, the function refuses
and calls no runner. The consent object is DEFINED here and MINTED elsewhere (the operator's
interactive yes, or a one-time approval); no function here mints one.

This module checks that a consent is BOUND to these bytes, unexpired and unused; it does
not authenticate who made it, and nothing here can enforce that a caller obtained it from the operator. Authenticity is
the minter's job (B-upstream-consent); callers MUST pass only a consent minted from the
operator's interactive yes or a one-time approval, never one an agent constructed.

The bundle arrives already redacted. This module verifies its digest before writing or
sending a byte, so what is previewed, what is on disk and what is sent are the same bytes.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import quote

from ..config import Config
from ..core.outcome import FAIL, NOTHING, OK, REFUSED
from ..infra import fsio
from ..infra import upstream_gh as gh
from .bugreport import Bundle
from .redact_report import redactor

#: A prefilled URL longer than this is not offered; the file is.
URL_MAX = 8000
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.][A-Za-z0-9_.-]*")

FAILED, UNAVAILABLE = FAIL, NOTHING


@runtime_checkable
class ConsentLike(Protocol):
    """What delivery needs from an operator consent (implemented by B-upstream-consent)."""

    digest: str
    expires_at: float  # epoch seconds
    used: bool

    def consume(self) -> bool:
        """Mark used; False when it already was (single use, race-safe in the minter)."""
        ...


@dataclass
class Consent:
    """The minimal consent: bound to one digest, expiring, single use."""

    digest: str
    expires_at: float
    used: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def consume(self) -> bool:
        with self._lock:
            if self.used:
                return False
            self.used = True
            return True


@dataclass(frozen=True)
class Outcome:
    #: prepared | fallback | sent | unavailable | failed | refused
    status: str
    exit_code: int
    reason: str = ""
    digest: str = ""
    markdown_path: Path | None = None
    json_path: Path | None = None
    url: str = ""
    issue_number: int | None = None


def reports_dir(root: Path) -> Path:
    return Path(root) / ".ddflow" / "local" / "reports"


def _write(path: Path, text: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def write_report(root: Path, bundle: Bundle) -> tuple[Path, Path]:
    """Write the markdown and JSON, named by digest, owner-only, under the git-ignored
    local dir. Refuses a bundle whose digest does not match its bytes."""
    if not bundle.verify():
        raise ValueError("the bundle does not match its digest; nothing was written")
    local = Path(root) / ".ddflow" / "local"
    d = reports_dir(root)
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)
    fsio.ensure_ignored_dir(local, mode=0o600)
    md, js = d / f"{bundle.digest}.md", d / f"{bundle.digest}.json"
    _write(md, bundle.rendered)
    _write(js, bundle.json)
    return md, js


def issue_url(bundle: Bundle, repo: str) -> tuple[str | None, str]:
    """The prefilled new-issue URL, or (None, why) when it would exceed `URL_MAX`.
    Nothing is truncated: an oversize bundle is simply not offered as a URL."""
    if not REPO.fullmatch(repo or ""):
        raise ValueError(f"{repo!r} is not an owner/name repository")
    url = (
        f"https://github.com/{repo}/issues/new"
        f"?title={quote(bundle.data['title'], safe='')}&body={quote(bundle.rendered, safe='')}"
    )
    if len(url) > URL_MAX:
        return None, f"the prefilled URL would be {len(url)} bytes (limit {URL_MAX})"
    return url, ""


def prepare(root: Path, bundle: Bundle, repo: str) -> Outcome:
    """File + prefilled URL. Sends and opens nothing. An oversize URL falls back to the
    file and says so."""
    try:
        md, js = write_report(root, bundle)
        url, why = issue_url(bundle, repo)
    except (ValueError, OSError) as exc:
        return Outcome("failed", FAILED, _safe(exc, root), bundle.digest)
    if url is None:
        return Outcome(
            "fallback",
            OK,
            f"{why}; attach the file {md} to the issue instead",
            bundle.digest,
            md,
            js,
        )
    return Outcome("prepared", OK, "", bundle.digest, md, js, url)


def _safe(exc: Exception, root: Path | str) -> str:
    """``exc``'s message with the upstream profile's masks AND the project's configured
    redaction patterns ([session].redact_patterns / redact_extra). A config that cannot
    be read, or holds a pattern that does not compile, falls back to the profile alone:
    this runs while reporting a failure, so it must never raise a second one."""
    text = str(exc)
    try:
        return redactor("upstream", Config.load(Path(root))).text(text).text
    except (OSError, ValueError, KeyError, TypeError):
        return redactor("upstream").text(text).text


def _refusal(consent: ConsentLike | None, digest: str, now: float) -> str:
    if consent is None:
        return "no operator consent was given"
    if getattr(consent, "digest", None) != digest:
        return "the consent is for different text than the text being sent"
    if getattr(consent, "used", True):
        return "the consent was already used (one consent, one submission)"
    expires = getattr(consent, "expires_at", None)
    if not isinstance(expires, (int, float)) or now >= expires:
        return "the consent has expired"
    return ""


def send_gh(
    root: Path,
    bundle: Bundle,
    repo: str,
    consent: ConsentLike | None,
    *,
    runner: Callable | None = None,
    now: Callable[[], float] = time.time,
) -> Outcome:
    """`gh issue create` with the exact previewed bytes. Refused (exit 3) without a valid
    consent for THIS digest, and then no runner is called at all."""
    digest = bundle.digest
    why = _refusal(consent, digest, now())
    if why:
        return Outcome("refused", REFUSED, why, digest)
    if not REPO.fullmatch(repo or ""):
        return Outcome("refused", REFUSED, f"{repo!r} is not an owner/name repository", digest)
    if not bundle.verify():
        return Outcome("refused", REFUSED, "the bundle does not match its digest", digest)
    run = runner or gh._run
    try:
        md, js = write_report(root, bundle)
        if md.read_bytes() != bundle.rendered.encode("utf-8"):
            return Outcome("failed", FAILED, "the file differs from the preview; not sent", digest)
        gh.ensure_ready(Path(root), runner=run)
    except gh.Unavailable as exc:
        return Outcome("unavailable", UNAVAILABLE, _safe(exc, root), digest)
    except gh.GhError as exc:
        return Outcome("failed", FAILED, _safe(exc, root), digest)
    except (ValueError, OSError) as exc:
        return Outcome("failed", FAILED, _safe(exc, root), digest)
    assert consent is not None
    if not consent.consume():
        return Outcome("refused", REFUSED, "the consent was already used", digest)
    try:
        url, number = gh.create_issue(Path(root), repo, bundle.data["title"], md, runner=run)
    except gh.Unavailable as exc:
        return Outcome("unavailable", UNAVAILABLE, _safe(exc, root), digest, md, js)
    except gh.GhError as exc:
        return Outcome("failed", FAILED, _safe(exc, root), digest, md, js)
    return Outcome("sent", OK, "", digest, md, js, url, number)
