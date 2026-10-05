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

import hashlib
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..infra import proc as P
from ..infra import worktree as W

PRE_COMMIT_CONFIG = ".pre-commit-config.yaml"
DEFAULT_COMMAND = "pre-commit run --hook-stage pre-push --all-files"
_ENV_WORD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
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
        }


def resolve_command(repo: Path, cfg: Config) -> tuple[str, str]:
    """(command, why-not): the configured command, else the project's pre-push stage, else
    nothing to run (and the reason, so the gate says "unavailable" rather than passing)."""
    if cfg.ci.command.strip():
        return cfg.ci.command.strip(), ""
    if not (Path(repo) / PRE_COMMIT_CONFIG).is_file():
        return "", f"no [ci].command and no {PRE_COMMIT_CONFIG}: there is nothing to run"
    return DEFAULT_COMMAND, ""


_SHELL_WORDS = frozenset(
    {"cd", "export", "set", "source", ".", "(", "{", "if", "for", "test", "[", "[[", "(("}
)


def tool_missing(command: str) -> str:
    """The executable the command starts with, when it is not installed ("" when it is)."""
    try:
        words = shlex.split(command)
    except ValueError:
        return command
    # `SKIP=tests pre-commit run ...`: leading VAR=value words are environment, not the program.
    program = next((w for w in words if not _ENV_WORD.match(w)), "")
    if program in _SHELL_WORDS:  # a builtin or compound command: the shell, not `which`, decides
        return ""
    return "" if (program and shutil.which(program)) else program


@contextmanager
def merge_tree(repo: Path, ref: str, base: str) -> Iterator[tuple[Path | None, str]]:
    """A scratch worktree of `ref` with `base` merged in: (path, "") or (None, why not).

    Removed on exit. A base already contained in `ref` needs no merge; a base that
    conflicts with `ref` is reported as the failure it is."""
    tmp = Path(tempfile.mkdtemp(prefix="ddflow-ci."))
    tree = tmp / "tree"
    added = False
    try:
        r = W.git(repo, "worktree", "add", "--quiet", "--detach", str(tree), ref)
        if not r.ok:
            yield None, f"could not create a scratch worktree of {ref}: {r.err or r.out}"
            return
        added = True
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
    finally:
        if added:
            W.git(repo, "worktree", "remove", "--force", str(tree))
        shutil.rmtree(tmp, ignore_errors=True)


def parse_checks(output: str) -> list[Check]:
    """Per-hook results from pre-commit's report ("ruff check ......... Failed")."""
    checks = []
    for line in output.splitlines():
        m = _HOOK_LINE.match(line.strip())
        if m and m["status"] != "Skipped":
            checks.append(Check(m["name"].strip(), m["status"] == "Passed"))
    return checks


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
    named = base or cfg.ci.base
    base = named or W.default_branch(repo)
    if not W.git(repo, "rev-parse", "--verify", "--quiet", base).ok:
        remote = f"origin/{base}"  # origin/HEAD names a branch that may exist only as a remote ref
        if not named and W.git(repo, "rev-parse", "--verify", "--quiet", remote).ok:
            base = remote
        else:
            return Result(
                "failed" if named else "unavailable",
                command=cmd,
                sha=sha,
                reason=f"base {base!r} is not a commit"
                + (" (misconfigured [ci].base or --base?)" if named else "")
                + ", so the merge result cannot be checked",
            )
    with merge_tree(repo, sha, base) as (tree, failure):
        if tree is None:
            status = "failed" if "does not merge" in failure else "unavailable"
            return Result(status, command=cmd, sha=sha, reason=failure)
        try:
            # The operator's own [ci].command, run as they wrote it; on timeout its whole
            # process group dies, not just the shell (Bed0f5b6d99).
            p = P.run_shell(cmd, timeout=cfg.ci.timeout_s, cwd=tree, text=True)
        except subprocess.TimeoutExpired:
            return Result(
                "unavailable",
                command=cmd,
                sha=sha,
                reason=f"timed out after {cfg.ci.timeout_s}s: raise [ci].timeout_s",
            )
    out = (p.stdout or "") + (p.stderr or "")
    checks = parse_checks(out)
    if p.returncode != 0 and not any(not c.ok for c in checks):
        checks.append(Check("command", False, f"exit {p.returncode}"))
    return Result(
        "passed" if p.returncode == 0 else "failed",
        checks=checks,
        sha=sha,
        merged_with=base,
        command=cmd,
        reason="" if p.returncode == 0 else f"{cmd} exited {p.returncode}",
        output_tail=out[-TAIL_CHARS:],
    )


#: Hooks `[ci].on_merge = "fast"` leaves out of the default pre-commit command: the suites.
FAST_SKIP = "tests,scenarios"
ON_MERGE_MODES = ("off", "fast", "full")


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


def bug_id(check: str) -> str:
    """A stable bug id per failing check, so the same failure is one bug while it is open.

    The slug is for people; the digest of the whole check id is what keeps two long ids
    that share a prefix (pytest node ids) from being one bug."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", check).strip("-").lower()[:30].strip("-")
    return f"Bci-{slug}-{hashlib.sha1(check.encode()).hexdigest()[:10]}"  # nosec B324 - not security
