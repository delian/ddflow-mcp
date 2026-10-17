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
from . import ci_scope as CS
from .cmdrunner import TIMEOUT, CommandRunner, Declared, executable_missing
from .flakes import failure_evidence, failure_text
from .gateenv import child_env

PRE_COMMIT_CONFIG = ".pre-commit-config.yaml"
DEFAULT_COMMAND = "pre-commit run --hook-stage pre-push --all-files"
#: What `merge_tree` says of a base that conflicts with the ref, and what `run` reads back to
#: tell that FAILED from a merge git could not finish (UNAVAILABLE): one constant for both.
NOT_MERGEABLE = "does not merge with"
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
    #: the whole output of the command (not part of `as_data`: it can be megabytes)
    output: str = ""
    #: failing test ids (and their reasons / failure tails) read from ALL of the output
    failures: dict = field(default_factory=dict)
    #: which tests ran (`ci_scope.Plan.evidence`); empty when the command ran as written
    scope: dict = field(default_factory=dict)

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
            **({"scope": self.scope} if self.scope else {}),
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


_REPO_START = re.compile(r"^\s*-\s+repo:")
_HOOKS_KEY = re.compile(r"^(?P<ind>\s*)hooks:\s*(#.*)?$")
_HOOK_ID = re.compile(r"^\s*(?:-\s+)?id:\s*[\"']?(?P<id>[^\s\"'#]+)")
_FAIL_LANGUAGE = re.compile(r"^\s*(?:-\s+)?language:\s*[\"']?fail[\"']?\s*(#.*)?$")
_LIST_ITEM = re.compile(r"^\s*-\s*[\"']?(?P<v>[\w-]+)[\"']?\s*(#.*)?$")
#: The names pre-commit gives the pre-push stage (the old and the new).
_PUSH_STAGES = ("pre-push", "push")


def _runs_at_pre_push(stages: list[str] | None) -> bool:
    return stages is None or any(s in _PUSH_STAGES for s in stages)


def _hook_blocks(lines: list[str]) -> list[list[str]]:
    """The lines of each hook of the config: every list item under a ``hooks:`` key, whatever
    key it starts with (YAML mappings are unordered) -- a line of its own ends the block when it
    is as shallow as the item before it."""
    blocks: list[list[str]] = []
    hooks_indent: int | None = None
    item_indent: int | None = None
    for line in lines:
        text = line.lstrip()
        indent = len(line) - len(text)
        if m := _HOOKS_KEY.match(line):
            hooks_indent, item_indent = len(m["ind"]), None
        elif _REPO_START.match(line):
            hooks_indent = item_indent = None
        elif hooks_indent is not None and text and not text.startswith("#"):
            if indent <= hooks_indent and not text.startswith("-"):
                hooks_indent = item_indent = None  # a key of the enclosing mapping again
            elif text.startswith("-") and (item_indent in (None, indent)):
                item_indent = indent
                blocks.append([line])
            elif blocks:
                blocks[-1].append(line)
    return blocks


def tripwire_ids(tree: Path) -> list[str]:
    """The hooks of the project's pre-commit config that are tripwires (`language: fail`) and
    run at the pre-push stage: they fire when a changed path matches their `files:`, which is a
    statement about a DIFF, and over every tracked file they fail whenever the paths they guard
    exist at all. A tripwire kept to another stage (`stages: [pre-commit]`) is not one the
    pre-push run has, so it is not named."""
    try:
        lines = (Path(tree) / PRE_COMMIT_CONFIG).read_text("utf-8").splitlines()
    except OSError:
        return []
    default_stages = _stage_list(lines, "default_stages", top=True)
    found: list[str] = []
    for body in _hook_blocks(lines):
        ident = next((m["id"] for ln in body if (m := _HOOK_ID.match(ln))), "")
        if not ident or ident in found or not any(_FAIL_LANGUAGE.match(ln) for ln in body):
            continue
        if _runs_at_pre_push(_stage_list(body, "stages", top=False) or default_stages):
            found.append(ident)
    return found


def _stage_list(lines: list[str], key: str, *, top: bool) -> list[str] | None:
    """The stages a ``key:`` line (inline list or block list) names; None when it is absent."""
    for i, line in enumerate(lines):
        indent = "" if top else r"\s*(?:-\s+)?"
        m = re.match(rf"^{indent}{key}:\s*(?P<inline>\[[^\]]*\])?", line)
        if not m:
            continue
        if m["inline"]:
            return [x.strip(" \"'") for x in m["inline"].strip("[]").split(",") if x.strip()]
        out = []
        for nxt in lines[i + 1 :]:
            if not nxt.strip() or nxt.lstrip().startswith("#"):
                continue  # a comment or a blank line inside the list is not its end
            item = _LIST_ITEM.match(nxt)
            if not item:
                break
            out.append(item["v"])
        return out
    return None


_LEADING_SKIP = re.compile(r"^SKIP=(?P<ids>\S+)\s+")


def with_tripwires_on_the_diff(command: str, tree: Path, since: str) -> str:
    """``command`` (the default pre-push command, maybe after `SKIP=...`) with the tripwire
    hooks taken out of its all-files run and run once on the diff ``since``..HEAD instead; the
    command as it is when the project has no tripwire or ``since`` is not a commit there."""
    ids = tripwire_ids(tree)
    if not ids or not since or not W.rev(tree, since):
        return command
    lead = _LEADING_SKIP.match(command)
    rest = command[lead.end() :] if lead else command
    skip = ",".join([*([lead["ids"]] if lead else []), *ids])
    # The environment's own SKIP (the selected-tests plan sets one) stays in front.
    first = f'SKIP="${{SKIP:+$SKIP,}}{skip}" {rest}'
    base_args = rest.split(" --all-files")[0]  # `pre-commit run --hook-stage pre-push`
    # The command's own SKIP= is the operator's skip: it applies to the named runs as well.
    mine = f"SKIP={lead['ids']} " if lead else ""
    ranged = [f"{mine}{base_args} --from-ref {since} --to-ref HEAD {i}" for i in ids]
    steps = [f"{first} || rc=1", *(f"{r} || rc=1" for r in ranged)]
    return "rc=0; " + "; ".join(steps) + "; exit $rc"


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
            # No hook of the repository runs in the scratch merge (core.hooksPath): the
            # post-merge hook regenerated pins by running the golden suite, which outlived
            # the git timeout and was reported as a conflict (B36c484af97).
            m = W.git(
                tree,
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=ddflow",
                "-c",
                "user.email=ddflow@localhost",
                "merge",
                "--no-edit",
                "--no-ff",
                base,
            )
            said = m.out or m.err
            # A conflict is git's own answer: exit 1, or histories with nothing in common.
            # Anything else (killed at the timeout, a base it cannot resolve, a lock held:
            # exit 128) is a merge it could not do, which the gate cannot call a conflict.
            conflict = m.code == 1 or "unrelated histories" in said
            if not m.ok and not conflict:
                yield None, f"could not merge {base} into {ref}: {m.err or m.out}"[-400:]
                return
            if not m.ok:
                yield None, f"{ref} {NOT_MERGEABLE} {base}: {(m.out or m.err)[-400:]}"
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


def run(
    repo: Path,
    cfg: Config,
    *,
    ref: str = "HEAD",
    base: str = "",
    command: str = "",
    timeout_s: int = 0,
    for_item: bool = False,
    full: bool = False,
    changed_from: str = "",
) -> Result:
    """Run the CI command on `ref` merged with `base` (default: the repo's default branch).
    ``timeout_s`` replaces `[ci].timeout_s` for this run when it is positive.

    ``for_item``: the run is one item's (D-test-economy-r2), so `[ci].tests_scope =
    "selected"` replaces the whole-suite hook by the tests the change reaches; ``full``
    (`ci run --full`, the merge queue's batch) always runs the whole suite.

    ``changed_from`` is the commit the change being checked began at, for the tripwire hooks of
    the default command (they run on that diff, not over every file): default ``base``."""
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
            status = "failed" if NOT_MERGEABLE in failure else "unavailable"
            return Result(status, command=cmd, sha=sha, reason=failure)
        budget = timeout_s if timeout_s > 0 else cfg.ci.timeout_s
        cmd = _default_with_tripwires(repo, cfg, cmd, tree, sha, base, changed_from)
        scope = None
        if for_item and not command.strip():
            asked = "selected" if not full and cfg.ci.tests_scope == "selected" else "full"
            scope = CS.plan(cfg, tree, base, requested=asked)
        p, out_prefix = _execute(cfg, tree, cmd, scope, budget)
        if p.kind == TIMEOUT:
            return Result(
                "unavailable",
                command=cmd,
                sha=sha,
                reason=f"{p.reason}: raise [ci].timeout_s",
                scope=scope.evidence(sha) if scope is not None else {},
            )
        if not p.ran:  # the runner is missing, the slots are taken, the process did not start
            return Result(
                "unavailable",
                command=cmd,
                sha=sha,
                reason=p.reason,
                scope=scope.evidence(sha) if scope is not None else {},
            )
    return _result(p, out_prefix, cmd, sha, base, scope)


def _default_with_tripwires(
    repo: Path, cfg: Config, cmd: str, tree: Path, sha: str, base: str, changed_from: str
) -> str:
    """``cmd`` with the tripwire hooks on the diff, when it is the default command (an operator's
    own `[ci].command` is run as written). The diff is the one the check is about: on the base
    itself (the after-merge check) there is no branch to compare with, so the commit just made
    against its first parent."""
    if cfg.ci.command.strip() or DEFAULT_COMMAND not in cmd:
        return cmd
    # (`merge` always says where its range began; this is for a caller that does not.)
    since = changed_from or (f"{sha}^" if W.rev(repo, base) == sha else base)
    return with_tripwires_on_the_diff(cmd, tree, since)


def _execute(cfg: Config, tree: Path, cmd: str, scope, budget: int):
    """(the run that decided the verdict, the output of the run before it): the command as
    written, or -- for a selected scope -- the hooks without the suite, then the selected
    tests on the same tree."""
    selected = scope is not None and scope.scope == "selected"
    # The operator's own [ci].command, run as they wrote it; on timeout its whole
    # process group dies, not just the shell (Bed0f5b6d99).
    p = CommandRunner().run(
        Declared(cmd, "[ci].command"),
        cwd=tree,
        env={**child_env(cfg, ci=True), **({"SKIP": scope.skip} if selected else {})},
        timeout_s=budget,
        check_installed=False,  # checked before the scratch worktree was made
    )
    if not (selected and p.ran and p.code == 0):
        return p, ""
    prefix = p.output + f"\n$ {scope.command}\n"
    return CommandRunner().run(
        Declared(scope.command, "the pre-commit `tests` hook"),
        cwd=tree,
        env=child_env(cfg, ci=True),
        timeout_s=max(min(budget, 60), budget - int(p.elapsed_s)),  # what is left, 60 s at least
        check_installed=True,  # a missing runner is unavailable, not a failed test
    ), prefix


def _result(p, out_prefix: str, cmd: str, sha: str, base: str, scope) -> Result:
    out = out_prefix + p.output  # a shell "not found" exit is the command's verdict: it ran
    checks = parse_checks(out)
    if out_prefix:  # the hooks passed and the selected tests ran after them
        checks.append(Check(f"selected tests ({len(scope.tests)} files)", p.code == 0))
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
        output=out,
        failures=failure_evidence(out) if p.code != 0 else {},
        scope=scope.evidence(sha) if scope is not None else {},
    )


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
