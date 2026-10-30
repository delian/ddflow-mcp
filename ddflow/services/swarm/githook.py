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
  `extensions.worktreeConfig` (needed for a per-worktree setting) stays on: it only
  activates `config.worktree` files, which no other worktree has unless it asked for one.
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
#: git's other client-side hook names; each gets a run-time wrapper to the project's hook.
GIT_HOOKS = (
    "applypatch-msg", "pre-applypatch", "post-applypatch", "commit-msg", "prepare-commit-msg",
    "post-commit", "pre-rebase", "post-checkout", "post-merge", "pre-push", "pre-auto-gc",
    "post-rewrite", "reference-transaction", "push-to-checkout", "post-index-change",
)  # fmt: skip

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
  *'Traceback (most recent call last)'*) printf '%s\\n' "ddflow lane hook: the lease check crashed; commit allowed (not checked)." >&2 ;;
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


def _original(worktree: Path, d: Path) -> Path:
    """The hooks directory the lane hook chains to: the worktree's own setting before the
    install (resolved as git does, against the worktree top), else the shared hooks dir."""
    prior = G.run(worktree, "config", "--worktree", "--get", "core.hooksPath")
    value = prior.out.strip() if prior.ok else ""
    top = G.run(worktree, "rev-parse", "--show-toplevel", timeout=P.TIMEOUTS["probe"])
    base = Path(top.out.strip()) if top.ok and top.out.strip() else Path(worktree)
    here = (base / value).resolve() if value else None
    if here is not None and here != d.resolve():
        return here
    saved = d / "original"
    if _had_prior(d) and saved.is_file():  # the worktree's own setting before the first install
        return Path(saved.read_text().strip())
    # Never the lane dir itself (the generated hook would exec itself): the shared setting.
    for scope in ("--local", "--global", "--system"):  # all but the worktree's own value
        shared = G.run(worktree, "config", scope, "--get", "core.hooksPath")
        value = shared.out.strip() if shared.ok else ""
        if value and (base / value).resolve() != d.resolve():
            return (base / value).resolve()
    c = G.run(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
    common = Path(c.out.strip()) if c.ok and c.out.strip() else base / ".git"
    return common / "hooks"


def install(worktree: Path, *, agent: str = "", invocation: str = "") -> str:
    """Install the lane hook for ``worktree``; one line saying what was done. Idempotent."""
    worktree = Path(worktree)
    d = hook_dir(worktree)
    original = _original(worktree, d)
    cur = G.run(worktree, "config", "--worktree", "--get", "core.hooksPath")
    had_prior = (cur.ok and cur.out.strip() != str(d)) or _had_prior(d)
    d.mkdir(parents=True, exist_ok=True)
    for stale in d.iterdir():  # wrappers of hooks the project no longer has
        stale.unlink()
    _write_exec(d / "pre-commit", render(chain_to=original, agent=agent, invocation=invocation))
    replace_text(d / "original", str(original) + "\n")
    replace_text(d / "had_prior", ("1" if had_prior else "0") + "\n")
    for name in GIT_HOOKS:  # looked up at run time: a hook added or removed later is honoured
        target = shlex.quote(str(original / name))
        body = f'#!/bin/sh\n{MARKER}\n[ -x {target} ] && exec {target} "$@"\nexit 0\n'
        _write_exec(d / name, body)
    G.run(worktree, "config", "extensions.worktreeConfig", "true")
    r = G.run(worktree, "config", "--worktree", "core.hooksPath", str(d))
    if not r.ok:
        raise RuntimeError(f"could not set the worktree's core.hooksPath: {r.err.strip()}")
    return (
        f"installed the lane lease hook at {d}: a commit touching files outside the item's "
        f"lease globs is refused (detection at commit, not prevention at edit)"
    )


def _had_prior(d: Path) -> bool:
    flag = d / "had_prior"
    return flag.is_file() and flag.read_text().strip() == "1"


def installed(worktree: Path) -> bool:
    r = G.run(worktree, "config", "--worktree", "--get", "core.hooksPath")
    return r.ok and r.out.strip() == str(hook_dir(worktree))


def uninstall(worktree: Path) -> str:
    """Remove the lane hook and restore the worktree's hooks directory."""
    worktree = Path(worktree)
    d = hook_dir(worktree)
    if not d.is_dir() and not installed(worktree):
        return "no lane hook installed"
    if installed(worktree):
        saved = d / "original"
        if _had_prior(d) and saved.is_file():  # the worktree had its own setting: put it back
            G.run(worktree, "config", "--worktree", "core.hooksPath", saved.read_text().strip())
        else:
            G.run(worktree, "config", "--worktree", "--unset", "core.hooksPath")
    if d.is_dir():
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
    return f"removed the lane lease hook at {d}"
