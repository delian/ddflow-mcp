"""Enforcement — the part of the workflow that does not depend on the agent agreeing.

There are three layers between "here is a workflow" and "the workflow happened", and
they are not equally strong. Being honest about which is which is the whole point of
this module:

1. **The MCP ``instructions`` field and the tool descriptions.** The server tells the
   model what to do on connect and at every call. This is *persuasion*: it works most of
   the time and fails silently the rest.
2. **The rules file** (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/*.mdc`). Always in
   context, higher weight than a tool description, still persuasion. A model under
   pressure to finish will skip it, and nothing notices.
3. **Git hooks.** The only layer that is *enforcement*, because it is the repository
   refusing rather than the model choosing. A commit touching files no live lease covers
   is rejected, with the command to fix it.

The hook is deliberately narrow. It answers exactly one question — *does a live lease
held by this agent cover the paths being committed?* — because that is the invariant
everything else rests on: if two agents can commit to the same file without claiming it,
no amount of gate bookkeeping upstream means anything.

**It is honest about being bypassable.** `git commit --no-verify` skips every hook, and
nothing in a local repository can prevent that. So the hook does not pretend: bypasses
are detectable after the fact (`orchard doctor` reports commits whose paths no lease
covered), and in a setting where it has to be unbypassable the same check belongs in CI,
where the agent is not the one running it.
"""

from __future__ import annotations

import stat
import subprocess
import sys
import time
from pathlib import Path

from .config import Config
from .events import EventLog
from .model import fold
from .schedule import globs_overlap

HOOK_MARKER = "# ORCHARD-HOOK v1 — managed by `orchard hooks install`"

#: How many offending paths the refusal lists before summarising. Enough to see the
#: shape of the problem (one stray file vs a whole directory) without burying the
#: remedy underneath the list.
MAX_LISTED_PATHS = 12

_PRE_COMMIT = """#!/bin/sh
{marker}
# Refuses a commit touching paths that no live Orchard lease held by this agent covers.
#
# This is the one mechanical guarantee in the workflow: everything else asks the agent
# nicely. Remove it with `orchard hooks uninstall`, or set
# [enforce].commit_without_lease = "warn" (or "off") in .orchard/config.toml.
#
# `git commit --no-verify` skips this, as it skips every hook. That is a property of
# git, not a hole in this check; `orchard doctor` reports the bypasses after the fact.
{invocation}
"""


def _invocation() -> str:
    """The shell line the hook uses to reach Orchard.

    Resolved AT INSTALL TIME and baked in, because a hook runs with git's environment,
    not the agent's: no virtualenv activated, no PYTHONPATH inherited, and a `cwd` that
    is the repo rather than wherever Orchard lives. An earlier version embedded only
    the interpreter path and produced `No module named orchard` on every commit from a
    source checkout -- a hook that fails is indistinguishable from a hook that refuses,
    so the commit was blocked for entirely the wrong reason.
    """
    import shutil

    script = shutil.which("orchard")
    if script and not _running_from_source():
        return f'exec "{script}" hooks check-commit "$@"'
    pkg_parent = str(Path(__file__).resolve().parents[1])
    return (
        f'PYTHONPATH="{pkg_parent}${{PYTHONPATH:+:$PYTHONPATH}}" '
        f'exec "{sys.executable}" -m orchard hooks check-commit "$@"'
    )


def _running_from_source() -> bool:
    here = Path(__file__).resolve()
    return not any(part in ("site-packages", "dist-packages") for part in here.parts)


def hooks_dir(repo: Path) -> Path:
    """The hooks directory, resolved through worktrees and `core.hooksPath`.

    A linked worktree's `.git` is a FILE pointing into the primary checkout, and a repo
    may relocate hooks entirely. Reading the resolved path from git rather than assuming
    `.git/hooks` is what makes this work inside the worktrees Orchard itself creates.
    """
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"not a git repository: {repo}")
    path = Path(r.stdout.strip())
    return path if path.is_absolute() else (Path(repo) / path).resolve()


def install(repo: Path, *, force: bool = False) -> str:
    d = hooks_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    hook = d / "pre-commit"
    if hook.exists():
        existing = hook.read_text("utf-8", errors="replace")
        if HOOK_MARKER in existing:
            hook.write_text(
                _PRE_COMMIT.format(marker=HOOK_MARKER, invocation=_invocation()), "utf-8"
            )
            _chmod_x(hook)
            return f"updated the Orchard hook at {hook}"
        if not force:
            # Never clobber someone else's hook. A workflow tool that silently replaces
            # a project's existing pre-commit checks has done more damage than the
            # discipline it was installing is worth.
            return (
                f"REFUSED: {hook} already exists and is not managed by Orchard. "
                f"Add this line to it yourself:\n"
                f"    {_invocation().replace('exec ', '')} || exit 1\n"
                f"or re-run with --force to replace it."
            )
    hook.write_text(_PRE_COMMIT.format(marker=HOOK_MARKER, invocation=_invocation()), "utf-8")
    _chmod_x(hook)
    return f"installed the Orchard pre-commit hook at {hook}"


def uninstall(repo: Path) -> str:
    hook = hooks_dir(repo) / "pre-commit"
    if not hook.exists():
        return "no pre-commit hook installed"
    if HOOK_MARKER not in hook.read_text("utf-8", errors="replace"):
        return f"REFUSED: {hook} is not managed by Orchard; leaving it alone"
    hook.unlink()
    return f"removed {hook}"


def installed(repo: Path) -> bool:
    try:
        hook = hooks_dir(repo) / "pre-commit"
    except RuntimeError:
        return False
    return hook.exists() and HOOK_MARKER in hook.read_text("utf-8", errors="replace")


def _chmod_x(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def staged_paths(repo: Path) -> list[str]:
    """Paths this commit will write.

    `--diff-filter=ACMR` over the INDEX, plus `--cached`, because a file the agent just
    created is not in HEAD and a diff against HEAD alone would not see it.
    """
    r = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


#: Paths Orchard's own bookkeeping writes. Requiring a lease for these would make it
#: impossible to commit the event log that records the lease.
SELF_MANAGED = (".orchard/", "docs/orchard/", "AGENTS.md", "CLAUDE.md", ".cursor/rules/")


def check_commit(repo: Path, cfg: Config | None = None, *, agent: str = "") -> tuple[int, str]:
    """(exit_code, message). 0 allows the commit; 1 refuses it.

    Returns a *message*, not a print, so the same logic serves the hook, `doctor`, and
    a CI job without three copies of the wording.
    """
    cfg = cfg or Config.load(repo)
    policy = getattr(cfg, "enforce", None)
    mode = getattr(policy, "commit_without_lease", "warn") if policy else "warn"
    if mode == "off":
        return 0, ""

    paths = [
        p for p in staged_paths(repo) if not any(p.startswith(prefix) for prefix in SELF_MANAGED)
    ]
    if not paths:
        return 0, ""

    log = EventLog(repo, agent or cfg.agent.id or "")
    state = fold(log.read_all(), strict=False)
    now = time.time()
    me = log.agent_id
    mine: list[str] = []
    others: dict[str, str] = {}
    for item_id, lease in state.active_leases(now, cfg.lease.grace_s).items():
        if lease.holder == me:
            mine.extend(lease.globs)
        else:
            for g in lease.globs:
                others[g] = f"{item_id} ({lease.holder})"

    uncovered = [p for p in paths if not any(globs_overlap(p, g) for g in mine)]
    if not uncovered:
        return 0, ""

    # A path another agent holds is the dangerous case and gets named separately: the
    # remedy is not "claim it", it is "stop".
    stolen = {p: owner for p in uncovered for g, owner in others.items() if globs_overlap(p, g)}

    lines = [
        f"Orchard: {len(uncovered)} staged path(s) are not covered by a lease you hold.",
        "",
    ]
    for p in uncovered[:MAX_LISTED_PATHS]:
        lines.append(f"  {p}" + (f"   <-- held by {stolen[p]}" if p in stolen else ""))
    if len(uncovered) > MAX_LISTED_PATHS:
        lines.append(f"  ... and {len(uncovered) - MAX_LISTED_PATHS} more")
    lines.append("")
    if stolen:
        lines += [
            "STOP: paths marked above are leased by ANOTHER agent. Committing them races",
            "that agent's work. Coordinate, or wait for the lease to be released.",
            "",
        ]
    lines += [
        f"You hold: {', '.join(mine) if mine else '(no live lease)'}",
        "",
        "Fix by claiming the work, or by widening the claim you already hold:",
        "    orchard next                     # what may be started",
        "    orchard claim <ID> --globs '...' # lease it and get a worktree",
        "    orchard update <ID> --globs '...'# widen an existing claim",
        "",
        f'Policy is [enforce].commit_without_lease = "{mode}" in .orchard/config.toml.',
    ]
    msg = "\n".join(lines)
    if mode == "warn":
        return 0, msg + '\n\n(warning only; set the policy to "block" to refuse)'
    return 1, msg


def check_item_trailer(repo: Path) -> tuple[int, str]:
    """Require an ``Item: <id>`` trailer on the commit being made.

    Enabled by ``[enforce].require_item_trailer``. The trailer is what lets an audit
    reconcile shipped commits against the queue with
    ``git log --format='%(trailers:key=Item,valueonly)'`` instead of parsing prose —
    which is the difference between a check that can run on every commit and one that
    needs an agent to read the whole history.

    Off by default: on a repository with human contributors it is noise, and a rule
    that most commits violate is a rule everyone learns to bypass.
    """
    msg_file = Path(repo) / ".git" / "COMMIT_EDITMSG"
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-path", "COMMIT_EDITMSG"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode == 0 and r.stdout.strip():
        cand = Path(r.stdout.strip())
        msg_file = cand if cand.is_absolute() else (Path(repo) / cand)
    if not msg_file.is_file():
        # No message to inspect yet (e.g. `-m` handled later in the hook order). Do not
        # invent a failure from missing input -- that is the vacuous-FAIL mirror of the
        # vacuous pass.
        return 0, ""
    text = msg_file.read_text("utf-8", errors="replace")
    if any(ln.startswith("Item:") and ln[5:].strip() for ln in text.splitlines()):
        return 0, ""
    return 1, (
        "Orchard: this commit has no `Item: <id>` trailer, and "
        "[enforce].require_item_trailer is on.\n\n"
        "Add a final line to the commit message, e.g.:\n"
        "    Item: P1.T3\n\n"
        "It is what lets an audit match commits to queue items mechanically."
    )
