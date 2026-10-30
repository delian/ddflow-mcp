"""The lane worktree's lease-checking git pre-commit hook (SW-git-fallback, D-swarm-answers Q7).

For an agent with no blocking pre-tool hook (an opencode lane): `git commit` is where an
edit outside the item's lease globs is caught. This is DETECTION at commit, not
prevention at edit -- the file was already written, and `git commit --no-verify` skips
it, as it skips every hook.

The check is `ddflow hooks check-commit`, the one lease/glob matcher the project's own
pre-commit hook runs (``core.globs``); this module writes no second one. What it adds:

* lane-private: the script lives in the lane worktree's own git dir and is selected by the
  worktree's own ``core.hooksPath`` (``git config --worktree``), so the project's tracked
  or shared hooks are never written; the project's hooks are still run after it (a wrapper
  per hook name for the others), so installing it removes no check;
* strict: the lane runs it with the policy ``block`` (``DDFLOW_ENFORCE_COMMIT_WITHOUT_LEASE``),
  whatever the project's default;
* fail open: ddflow missing, a crash or a lease that cannot be read allows the commit
  with one fixed line (never the traceback, which may carry paths or values);
* removable: `uninstall` restores the worktree's setting and deletes the directory.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from ...infra import git as G
from ...infra import proc as P
from ...infra.fsio import replace_text
from ..enforce import command_line

#: The lease policy a lane commit runs under (the knob `[enforce].commit_without_lease`).
POLICY = "block"
#: The directory, inside the worktree's git dir, that holds the lane's hooks.
DIR_NAME = "ddflow-lane-hooks"
MARKER = "# DDFLOW-LANE-HOOK v1 -- managed by ddflow swarm githook; remove with uninstall"
#: Hooks the project already has, wrapped so the lane's hooksPath does not hide them.
CHAINED_SKIP = ("pre-commit",)

_SCRIPT = """#!/bin/sh
{marker}
# Refuses a commit touching paths outside this lane's item lease globs. Detection at
# commit, not prevention at edit: the file is already written, and `git commit
# --no-verify` skips every hook. Fails OPEN (allows the commit) when ddflow or the lease
# cannot be read. Remove: uninstall the lane hook (or unset core.hooksPath for this
# worktree: git config --worktree --unset core.hooksPath).
{export}
out=$({invocation} 2>&1)
rc=$?
case "$rc" in
0) [ -z "$out" ] || printf '%s\\n' "$out" >&2 ;;
1)
  case "$out" in
  *Traceback*) printf '%s\\n' "ddflow lane hook: the lease check crashed; commit allowed (not checked)." >&2 ;;
  *) printf '%s\\n' "$out" >&2; exit 1 ;;
  esac ;;
*) printf '%s\\n' "ddflow lane hook: the lease check could not run (exit $rc); commit allowed (not checked)." >&2 ;;
esac
{chain}
exit 0
"""


def _git_dir(worktree: Path) -> Path:
    r = G.run(worktree, "rev-parse", "--absolute-git-dir", timeout=P.TIMEOUTS["probe"])
    if not r.ok:
        raise RuntimeError(f"not a git worktree: {worktree}")
    return Path(r.out.strip())


def hook_dir(worktree: Path) -> Path:
    """The lane's private hooks directory (inside the worktree's git dir)."""
    return _git_dir(worktree) / DIR_NAME


def _current_hooks_path(worktree: Path) -> Path:
    r = G.run(worktree, "rev-parse", "--git-path", "hooks", timeout=P.TIMEOUTS["probe"])
    p = Path(r.out.strip()) if r.ok and r.out.strip() else Path(".git/hooks")
    return p if p.is_absolute() else (Path(worktree) / p).resolve()


def render(*, chain_to: Path | None = None, agent: str = "", invocation: str = "") -> str:
    """The pre-commit script. ``invocation`` replaces the ddflow command line (tests)."""
    exports = [f"DDFLOW_ENFORCE_COMMIT_WITHOUT_LEASE={shlex.quote(POLICY)}"]
    if agent:
        exports.append(f"DDFLOW_AGENT={shlex.quote(agent)}")
    inv = invocation or command_line("hooks check-commit", extra='"$@"')
    chain = ""
    if chain_to is not None:
        orig = shlex.quote(str(chain_to / "pre-commit"))
        chain = f'[ -x {orig} ] && exec {orig} "$@"'
    return _SCRIPT.format(
        marker=MARKER, export="export " + " ".join(exports), invocation=inv, chain=chain
    )


def _write_exec(path: Path, text: str) -> None:
    replace_text(path, text)
    path.chmod(0o755)


def install(worktree: Path, *, agent: str = "", invocation: str = "") -> str:
    """Install the lane hook for ``worktree``; one line saying what was done. Idempotent."""
    worktree = Path(worktree)
    d = hook_dir(worktree)
    prior = G.run(worktree, "config", "--worktree", "--get", "core.hooksPath")
    original = Path(prior.out.strip()) if prior.ok and prior.out.strip() != str(d) else None
    if original is None and prior.ok:  # already ours: keep the project's recorded hooks dir
        saved = d / "original"
        original = Path(saved.read_text().strip()) if saved.is_file() else None
    if original is None:
        original = _current_hooks_path(worktree)
    d.mkdir(parents=True, exist_ok=True)
    _write_exec(d / "pre-commit", render(chain_to=original, agent=agent, invocation=invocation))
    replace_text(d / "original", str(original) + "\n")
    if original.is_dir():
        for h in sorted(original.iterdir()):
            if h.is_file() and h.name not in CHAINED_SKIP and not h.name.endswith(".sample"):
                body = f'#!/bin/sh\n{MARKER}\nexec {shlex.quote(str(h))} "$@"\n'
                _write_exec(d / h.name, body)
    G.run(worktree, "config", "extensions.worktreeConfig", "true")
    r = G.run(worktree, "config", "--worktree", "core.hooksPath", str(d))
    if not r.ok:
        raise RuntimeError(f"could not set the worktree's core.hooksPath: {r.err.strip()}")
    return (
        f"installed the lane lease hook at {d}: a commit touching files outside the item's "
        f"lease globs is refused (detection at commit, not prevention at edit)"
    )


def installed(worktree: Path) -> bool:
    r = G.run(worktree, "config", "--worktree", "--get", "core.hooksPath")
    return r.ok and r.out.strip() == str(hook_dir(worktree))


def uninstall(worktree: Path) -> str:
    """Remove the lane hook and restore the worktree's hooks directory."""
    worktree = Path(worktree)
    d = hook_dir(worktree)
    if not d.is_dir():
        return "no lane hook installed"
    if installed(worktree):
        G.run(worktree, "config", "--worktree", "--unset", "core.hooksPath")
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
    return f"removed the lane lease hook at {d}"
