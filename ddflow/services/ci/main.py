"""The after-merge CI command and the stable bug ids a failing check is filed under.

Split out of ``ddflow/services/ci.py``: ``main_command`` (the merged base's check) and the
id service through which a failing check becomes and stays one bug (``bug_id``,
``is_filing_of``, ``refile_id``).
"""

from __future__ import annotations

from pathlib import Path

from ...config import CI_ON_MERGE_MODES, Config
from ...core import ids as IDS
from ...core.digest import content_digest
from ...core.slug import ascii_slug
from .. import ci_scope as CS
from .precommit import resolve_command

#: Hooks `[ci].on_merge = "fast"` leaves out of the default pre-commit command: the suites.
FAST_SKIP = CS.TESTS_HOOKS
ON_MERGE_MODES = CI_ON_MERGE_MODES  # declared beside the knob, where `Config.check` holds it


def main_command(repo: Path, cfg: Config) -> tuple[str, str]:
    """The command that checks the base after a merge: (command, "" | why there is none).

    `full` is the CI command as it stands. `fast` is the same with the test hooks skipped
    when the command is the project's own pre-commit stage; an explicit `[ci].command` is
    the operator's whole decision and is not rewritten, so `fast` runs it as written."""
    if cfg.ci.on_merge == "off":
        return "", "[ci].on_merge is off"
    cmd, why = resolve_command(repo, cfg)
    if not cmd or cfg.ci.on_merge != "fast" or cfg.ci.command.strip():
        return cmd, why
    return f"SKIP={FAST_SKIP} {cmd}", ""


def bug_id(check: str, cfg: Config | None = None) -> str:
    """A stable bug id per failing check, so the same failure is one bug while it is open:
    `[ids].ci_bug` (`Bci-{slug}-{digest}`, a stable template) through the id service.

    The slug is for people; the digest of the whole check id is what keeps two long ids
    that share a prefix (pytest node ids) from being one bug."""

    slug = ascii_slug(check, 30)
    digest = content_digest(check, "sha1", length=10)
    return IDS.render(cfg if cfg is not None else Config(), "ci_bug", slug=slug, digest=digest)


def is_filing_of(bug: str, base: str, cfg: Config | None = None) -> bool:
    """Whether ``bug`` is the CI bug ``base`` itself or one of its re-filings (the id
    service decides, from the `[ids].ci_bug` template)."""

    return IDS.is_filing_of(cfg if cfg is not None else Config(), "ci_bug", bug, base)


def refile_id(base: str, sha: str) -> str:
    """A failing check's bug filed AGAIN after its first bug was fixed: a new bug, not a
    reopening, minted by the id service (`ids.refile`)."""

    return IDS.refile(base, sha)
