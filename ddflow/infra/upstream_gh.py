"""`gh issue create`, through the forge CLI the operator already logged in to.

ddflow holds no credential here and never asks for one: whether `gh` is logged in is
answered by `gh auth status` alone, and the login itself stays inside `gh`. This module
only SENDS when its caller has already checked a consent (see `services/upstream_delivery`);
it does not decide anything about consent itself.

`gh` missing or not logged in is `Unavailable` with the reason; a `gh` error is `GhError`
with its redacted message. The runner is injectable so tests never start a real `gh`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..services.redact_report import redact_report
from .forge import ForgeError, ForgeUnavailable, _check, _run

Runner = Callable[..., Any]

ERROR_MAX = 400
_ISSUE = re.compile(r"/issues/(\d+)")


class Unavailable(RuntimeError):
    """`gh` could not be asked (missing, not logged in, offline)."""


class GhError(RuntimeError):
    """`gh` was asked and said no."""


def _safe(text: str) -> str:
    return redact_report(text).text[:ERROR_MAX]


def _ask(runner: Runner, cwd: Path, argv: list[str], what: str) -> str:
    try:
        return _check(runner(cwd, argv), what)
    except ForgeUnavailable as exc:
        raise Unavailable(_safe(str(exc))) from exc
    except ForgeError as exc:
        raise GhError(_safe(str(exc))) from exc


def ensure_ready(cwd: Path, *, runner: Runner = _run) -> None:
    """Raise `Unavailable` unless `gh` is installed and reports a logged-in account."""
    _ask(runner, cwd, ["gh", "auth", "status"], "gh auth status")


def create_issue(
    cwd: Path, repo: str, title: str, body_file: Path, *, runner: Runner = _run
) -> tuple[str, int | None]:
    """Run `gh issue create -R <repo> --title <title> --body-file <path>`; return the
    issue URL and number the command printed."""
    out = _ask(
        runner,
        cwd,
        ["gh", "issue", "create", "-R", repo, "--title", title, "--body-file", str(body_file)],
        "gh issue create",
    ).strip()
    url = out.splitlines()[-1].strip() if out else ""
    m = _ISSUE.search(url)
    return url, int(m.group(1)) if m else None
