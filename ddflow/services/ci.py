"""The CI gate: run the project's pre-push/CI checks on the MERGE RESULT, in a scratch worktree.

Every per-task gate used to run a different command set from the pre-push hook, and nothing
tested a branch together with what main had become, so lint, format, bandit and the other
whole-tree checks first ran at push time, in a batch (decision D-ci-parity). `ddflow ci run`
makes "passed its gates" mean "would pass pre-push": it merges the branch with the base in a
throwaway worktree (a conflict is a failure) and runs the configured command there, which by
default is the project's own pre-push stage: `pre-commit run --hook-stage pre-push`.

Exit contract of `run`: 0 every check passed, 1 a check failed (or the merge conflicts),
2 it could not run (no pre-commit, no config, no worktree) -- never a pass.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ..config import CI_ON_MERGE_MODES, Config
from ..core import ids as IDS
from ..core.digest import content_digest
from ..core.slug import ascii_slug
from ..infra import worktree as W
from .cmdrunner import COULD_NOT_RUN, TIMEOUT, CommandRunner, Declared, executable_missing
from .flakes import failure_evidence, failure_text

PRE_COMMIT_CONFIG = ".pre-commit-config.yaml"
DEFAULT_COMMAND = "pre-commit run --hook-stage pre-push --all-files"
_HOOK_LINE = re.compile(r"^(?P<name>.+?)\.{3,}.*?(?P<status>Passed|Failed|Skipped)\s*$")
TAIL_CHARS = 4000


@dataclass
class Check:
    id: str
    ok: bool
    detail: str = ""


@dataclass
class Result:
    status: str  # passed | failed | unavailable
    checks: list[Check] = field(default_factory=list)
    reason: str = ""
    sha: str = ""
    merged_with: str = ""
    command: str = ""
    output_tail: str = ""
    #: failing test ids (and their reasons / failure tails) read from ALL of the output
    failures: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    @property
    def exit(self) -> int:
        return {"passed": 0, "failed": 1}.get(self.status, 2)

    def as_data(self) -> dict:
        return {
            "status": self.status,
            "sha": self.sha,
            "merged_with": self.merged_with,
            "command": self.command,
            "why": self.reason,
            "checks": [{"id": c.id, "ok": c.ok, "detail": c.detail} for c in self.checks],
            "output_tail": self.output_tail,
            **self.failures,
            **({"failure_text": failure_text(self.failures)} if self.failures else {}),
        }


def resolve_command(repo: Path, cfg: Config) -> tuple[str, str]:
    """(command, why-not): the configured command, else the project's pre-push stage, else
    nothing to run (and the reason, so the gate says "unavailable" rather than passing)."""
    if cfg.ci.command.strip():
        return cfg.ci.command.strip(), ""
    if not (Path(repo) / PRE_COMMIT_CONFIG).is_file():
        return "", f"no [ci].command and no {PRE_COMMIT_CONFIG}: there is nothing to run"
    return DEFAULT_COMMAND, ""


def tool_missing(command: str) -> str:
    """The executable the command starts with, when it is not installed ("" when it is)."""
    return executable_missing(command)


@contextmanager
def merge_tree(repo: Path, ref: str, base: str) -> Iterator[tuple[Path | None, str]]:
    """A scratch worktree of `ref` with `base` merged in: (path, "") or (None, why not).

    Removed on exit. A base already contained in `ref` needs no merge; a base that
    conflicts with `ref` is reported as the failure it is."""
    with W.scratch_tree(repo, ref, detach=True, prefix="ddflow-ci.", quiet=True) as s:
        if s.path is None:
            yield None, f"could not create a scratch worktree of {ref}: {s.error}"
            return
        tree = s.path
        if base and W.git(repo, "merge-base", "--is-ancestor", base, ref).code != 0:
            m = W.git(
                tree,
                "-c",
                "user.name=ddflow",
                "-c",
                "user.email=ddflow@localhost",
                "merge",
                "--no-edit",
                "--no-ff",
                base,
            )
            if not m.ok:
                yield None, f"{ref} does not merge with {base}: {(m.out or m.err)[-400:]}"
                return
        yield tree, ""


def parse_checks(output: str) -> list[Check]:
    """Per-hook results from pre-commit's report ("ruff check ......... Failed")."""
    checks = []
    for line in output.splitlines():
        m = _HOOK_LINE.match(line.strip())
        if m and m["status"] != "Skipped":
            checks.append(Check(m["name"].strip(), m["status"] == "Passed"))
    return checks


def _merge_base(
    repo: Path, cfg: Config, base: str, cmd: str, sha: str
) -> tuple[str, Result | None]:
    """(the ref to merge ``sha`` with, None); or ("", why that ref cannot be used)."""
    named = base or cfg.ci.base
    base = named or W.default_branch(repo)
    if W.git(repo, "rev-parse", "--verify", "--quiet", base).ok:
        return base, None
    remote = f"origin/{base}"  # origin/HEAD names a branch that may exist only as a remote ref
    if not named and W.git(repo, "rev-parse", "--verify", "--quiet", remote).ok:
        return remote, None
    return "", Result(
        "failed" if named else "unavailable",
        command=cmd,
        sha=sha,
        reason=f"base {base!r} is not a commit"
        + (" (misconfigured [ci].base or --base?)" if named else "")
        + ", so the merge result cannot be checked",
    )


def run(repo: Path, cfg: Config, *, ref: str = "HEAD", base: str = "", command: str = "") -> Result:
    """Run the CI command on `ref` merged with `base` (default: the repo's default branch)."""
    repo = Path(repo)
    cmd, why = (command.strip(), "") if command.strip() else resolve_command(repo, cfg)
    if not cmd:
        return Result("unavailable", reason=why)
    if missing := tool_missing(cmd):
        return Result(
            "unavailable",
            command=cmd,
            reason=f"{missing!r} is not installed, so nothing was checked "
            f"(install it, e.g. `uv tool install pre-commit`, or set [ci].command)",
        )
    sha = W.rev(repo, ref)
    if not sha:
        return Result("unavailable", command=cmd, reason=f"{ref!r} is not a commit")
    base, refused = _merge_base(repo, cfg, base, cmd, sha)
    if refused is not None:
        return refused
    with merge_tree(repo, sha, base) as (tree, failure):
        if tree is None:
            status = "failed" if "does not merge" in failure else "unavailable"
            return Result(status, command=cmd, sha=sha, reason=failure)
        # The operator's own [ci].command, run as they wrote it; on timeout its whole
        # process group dies, not just the shell (Bed0f5b6d99).
        p = CommandRunner().run(
            Declared(cmd, "[ci].command"),
            cwd=tree,
            timeout_s=cfg.ci.timeout_s,
            check_installed=False,  # checked above, before the scratch worktree was made
        )
        if p.kind == TIMEOUT:
            return Result(
                "unavailable",
                command=cmd,
                sha=sha,
                reason=f"{p.reason}: raise [ci].timeout_s",
            )
        if p.kind == COULD_NOT_RUN:
            return Result("unavailable", command=cmd, sha=sha, reason=p.reason)
    out = p.output  # a shell "not found" exit is the command's verdict here: it ran and failed
    checks = parse_checks(out)
    if p.code != 0 and not any(not c.ok for c in checks):
        checks.append(Check("command", False, f"exit {p.code}"))
    return Result(
        "passed" if p.code == 0 else "failed",
        checks=checks,
        sha=sha,
        merged_with=base,
        command=cmd,
        reason="" if p.code == 0 else f"{cmd} exited {p.code}",
        output_tail=out[-TAIL_CHARS:],
        failures=failure_evidence(out) if p.code != 0 else {},
    )


#: Hooks `[ci].on_merge = "fast"` leaves out of the default pre-commit command: the suites.
FAST_SKIP = "tests,scenarios"
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
