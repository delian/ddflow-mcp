"""The git hook: install, uninstall, status, and the launch line it runs."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from ...core import clock
from ...infra import git as G
from ...infra import proc as P
from ...infra.fsio import Managed, NewerContent, RegionError, replace_text
from ...infra.paths import LAUNCH_ROOT_ENV, launch_parent, launch_python, redirected_from
from ..backups import make_backup
from ..install_info import running_from_source
from .precommit import (
    UnreadableYaml,
    _generated_args,
    precommit_hooks_running,
    read_precommit_yaml,
)
from .staged import _chmod_x

#: The line every ddflow git hook has carried since the first. Kept INSIDE the stamped
#: region (below) so an older ddflow, which knows only this line, still sees the hook as
#: its own; a hook written before the region existed is recognised by it alone.
HOOK_MARKER = "# DDFLOW-HOOK v1 — managed by `ddflow hooks install`"


#: What a refusal for a hook a NEWER ddflow wrote says (`fsio.NewerContent`), so the caller
#: can tell that refusal (exit 3) from the others (exit 1).
NEWER_HINT = "upgrade ddflow to >= "


def hook_region(name: str) -> Managed:
    """The region a git hook's text is: version, format level and body digest on its begin
    line (fsio.Managed, the grammar every managed file shares), so a hook an older ddflow
    wrote, a newer one wrote or a person edited are told apart, not just "has the marker"."""
    return Managed(f"hooks/{name}", open="#", close="")


#: How many offending paths the refusal lists before summarising. Enough to see the
#: shape of the problem (one stray file vs a whole directory) without burying the
#: remedy underneath the list.
MAX_LISTED_PATHS = 12

_PRE_COMMIT = """#!/bin/sh
{marker}
# Refuses a commit touching paths that no live ddflow lease held by this agent covers.
#
# This is the one mechanical guarantee in the workflow: everything else asks the agent
# nicely. Remove it with `ddflow hooks uninstall`, or set
# [enforce].commit_without_lease = "warn" (or "off") in .ddflow/config.toml.
#
# `git commit --no-verify` skips this, as it skips every hook. That is a property of
# git, not a hole in this check; `ddflow doctor` reports the bypasses after the fact.
{invocation}
"""


def _invocation() -> str:
    """The shell line the hook uses to reach ddflow.

    Resolved AT INSTALL TIME and baked in, because a hook runs with git's environment,
    not the agent's: no virtualenv activated, no PYTHONPATH inherited, and a `cwd` that
    is the repo rather than wherever ddflow lives. An earlier version embedded only
    the interpreter path and produced `No module named ddflow` on every commit from a
    source checkout -- a hook that fails is indistinguishable from a hook that refuses,
    so the commit was blocked for entirely the wrong reason.
    """
    return command_line("hooks check-commit", exec_=True, extra='"$@"')


def command_line(
    args: str, *, exec_: bool = False, extra: str = "", refresh: str = "ddflow hooks install"
) -> str:
    """A shell line running `ddflow <args>` from an environment that has none of ours.

    Shared by the git hook and the Claude Code SessionStart hook: both run with the
    caller's environment rather than the agent's, and both must reach THIS ddflow.

    The launcher recorded at install time is only the FIRST choice, tried at RUN time:
    when it no longer exists (the venv was deleted, the tool uninstalled, a worktree
    removed) the line falls back to `ddflow` on PATH, and when that is missing too it
    prints one line and succeeds. A missing tool must not block every commit -- the line
    used to `exec` a dead absolute path, so every `git commit` failed with "not found"
    and nothing noticed (bug B-dangling-precommit-hook). `services.launchers` reads the
    recorded paths back out of the line, for `doctor` and `hooks status`.
    ``extra`` is appended to the arguments in every branch (`"$@"` for a git hook);
    ``refresh`` is the command the one-line notice names.
    """

    run = "exec " if exec_ else ""
    tail = f" {extra}" if extra else ""
    script = shutil.which("ddflow")
    if script and not _running_from_source():
        probe = f'[ -x "{script}" ]'
        recorded = f'{run}"{script}" {args}{tail}'
    else:
        pkg_parent = str(launch_parent())
        probe = f'[ -x "{launch_python()}" ] && [ -f "{pkg_parent}/ddflow/__init__.py" ]'
        # The environment prefix goes BEFORE `exec`: `exec VAR=x cmd` runs a command
        # literally named `VAR=x`.
        recorded = (
            f'PYTHONPATH="{pkg_parent}${{PYTHONPATH:+:$PYTHONPATH}}" '
            f'{run}"{launch_python()}" -m ddflow {args}{tail}'
        )
    note = shlex.quote(
        "ddflow: not found (the launcher recorded in this hook no longer exists and none "
        f"is on PATH), so this check was skipped. Install ddflow, then run: {refresh}"
    )
    miss = f"echo {note} >&2" + ("; exit 0" if exec_ else "")
    return (
        f"if {probe}; then {recorded}; "
        f"elif command -v ddflow >/dev/null 2>&1; then {run}ddflow {args}{tail}; "
        f"else {miss}; fi"
    )


def _running_from_source() -> bool:
    return running_from_source()


def hooks_dir(repo: Path) -> Path:
    """The hooks directory, resolved through worktrees and `core.hooksPath`.

    A linked worktree's `.git` is a FILE pointing into the primary checkout, and a repo
    may relocate hooks entirely. Reading the resolved path from git rather than assuming
    `.git/hooks` is what makes this work inside the worktrees ddflow itself creates.
    """
    r = G.run(repo, "rev-parse", "--git-path", "hooks", timeout=P.TIMEOUTS["probe"])
    if not r.ok:
        raise RuntimeError(f"not a git repository: {repo}")
    path = Path(r.out)
    return path if path.is_absolute() else (Path(repo) / path).resolve()


#: The commit-msg hook. The trailer check lives HERE and not in pre-commit because git
#: writes the message only after pre-commit has run: the pre-commit version read
#: `.git/COMMIT_EDITMSG`, which still held the PREVIOUS commit's message, so a commit
#: WITH `Item: X` was refused and one without it passed after any commit that had one.
_COMMIT_MSG = """#!/bin/sh
{marker}
# Checks the commit MESSAGE: the trailer [enforce].require_item_trailer asks for, and
# none of [enforce].forbidden_trailers. Does nothing while both are unset.
{invocation}
"""


def _msg_invocation() -> str:
    return command_line("hooks check-msg", exec_=True, extra='"$@"')


#: hook name -> (template, invocation builder). ONE table, so install, uninstall and
#: status cannot disagree about which hooks ddflow owns.
def _hooks() -> dict[str, tuple[str, str]]:
    return {
        "pre-commit": (_PRE_COMMIT, _invocation()),
        "commit-msg": (_COMMIT_MSG, _msg_invocation()),
    }


def _over_framework(repo: Path, hook: Path, name: str) -> str:
    """What to say instead of installing over a hook the pre-commit framework generated.

    Never "add this line to it": `pre-commit install` rewrites the file and the edit is
    silently lost (bug B695d7925bf). Where the config already runs ddflow's check there
    is nothing to install; otherwise the remedy is the config's, as `hooks status` says.
    """
    a = armed(repo, name)
    if a.via:
        return f"{name}: {a.detail}; nothing to install"
    # A framework hook ddflow has a check for is never left without a remedy by `armed`.
    remedy = a.remedy
    return (
        f"REFUSED: {hook} is generated by the pre-commit framework, and the next "
        f"`pre-commit install` rewrites it, so a line added there is lost. Here: {a.detail}.\n"
        f"    {remedy} -- `ddflow precommit` proposes the hooks -- then re-run "
        f"`pre-commit install`;\n"
        f"or re-run with --force to replace the generated hook with ddflow's."
    )


def _hook_file(name: str, template: str, invocation: str) -> tuple[str, str]:
    """(the shebang line, the region body) of a hook: the template with its marker line
    kept as the body's first line."""
    shebang, _, body = template.format(marker=HOOK_MARKER, invocation=invocation).partition("\n")
    return shebang, body


def _read_hook(hook: Path) -> tuple[str, bool]:
    """(the hook's text, whether it is exactly what is on disk). A byte that is not UTF-8
    is shown as U+FFFD, so text read that way must never be written back around a
    person's lines: the caller refuses instead."""
    raw = hook.read_bytes()
    try:
        return raw.decode("utf-8"), True
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), False


def _install_one(
    repo: Path, d: Path, name: str, template: str, invocation: str, force: bool
) -> str:
    hook = d / name
    shebang, body = _hook_file(name, template, invocation)
    region = hook_region(name)
    text = shebang + "\n" + region.render(body)
    if hook.exists():
        existing, exact = _read_hook(hook)
        try:
            state = region.state(existing)
            trouble = "" if exact or state == "absent" else "bytes that are not UTF-8"
        except RegionError as exc:
            state, trouble = "broken", f"broken ddflow markers ({exc})"
        if trouble and not force:
            return (
                f"REFUSED: {hook} has {trouble}, so ddflow cannot rewrite its region and keep "
                f"the rest byte for byte; fix it by hand, or re-run with --force to replace it."
            )
        if state not in ("absent", "broken") and not trouble:
            return _refresh(repo, hook, name, region, existing, state, body)
        if not trouble and HOOK_MARKER in existing:  # written before the region: wholly ours
            replace_text(hook, text)
            _chmod_x(hook)
            return f"updated the ddflow {name} hook at {hook}"
        if not trouble and PRECOMMIT_HEADER in existing and not force:
            return _over_framework(repo, hook, name)
        if not trouble and not force:
            # Never clobber someone else's hook. A workflow tool that silently replaces
            # a project's existing checks has done more damage than the discipline it
            # was installing is worth.
            return (
                f"REFUSED: {hook} already exists and is not managed by ddflow. "
                f"Add this line to it yourself:\n"
                f"    {invocation.replace('exec ', '').replace('; exit 0', '')} || exit 1\n"
                f"or re-run with --force to replace it."
            )
    replace_text(hook, text)
    _chmod_x(hook)
    return f"installed the ddflow {name} hook at {hook}"


def _refresh(
    repo: Path, hook: Path, name: str, region: Managed, existing: str, state: str, body: str
) -> str:
    """Rewrite the region of a hook that has one. Whatever sits outside it stays byte for
    byte; a region a NEWER ddflow wrote is not downgraded (D-compat 2); one a person
    edited is copied to `.ddflow/backups/` first, as `adopt --refresh-docs` does."""
    try:
        new = region.splice(existing, body)
    except NewerContent as exc:
        return f"REFUSED: {hook}: {exc}"
    saved = ""
    if state == "edited":
        saved = backup_edited(repo, hook, f"{name} hook")
    replace_text(hook, new)
    _chmod_x(hook)
    return f"updated the ddflow {name} hook at {hook}{saved}"


def backup_edited(repo: Path, path: Path, what: str) -> str:
    """Copy a file whose managed region a person edited to `.ddflow/backups/` before it
    is rewritten; the clause that says where, or one that says it could NOT be saved (the
    rewrite goes on: the refresh is what was asked for)."""
    try:
        where = make_backup(repo, [path], "", "", name=f"{clock.compact_at()}-{path.name}-edited")
    except OSError:
        return f"; its {what} had been edited by hand and could NOT be backed up"
    return f"; its {what} had been edited by hand, the original is in {where}"


def install(repo: Path, *, force: bool = False) -> str:
    """Both hooks. REFUSED if the pre-commit one was, since that is the one that
    enforces leases; a refused commit-msg hook is reported with the line to add."""
    d = hooks_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    hooks = _hooks()
    first = _install_one(repo, d, "pre-commit", *hooks["pre-commit"], force)
    if first.startswith("REFUSED"):
        # Nothing else is written when the enforcing hook is refused: reporting a
        # failure while having installed half of it is the partial-write class `adopt`
        # was fixed for in the same change (roborev 827).
        return first
    rest = [
        _install_one(repo, d, n, t, inv, force)
        for n, (t, inv) in hooks.items()
        if n != "pre-commit"
    ]
    note = redirect_note(command_line("hooks check-commit"))
    return "\n".join([first, *rest, *([note] if note else [])]).replace(
        "REFUSED:", "NOT INSTALLED:"
    )


def redirect_note(line: str | None = None) -> str:
    """What the user must know about where launch lines point, or "".

    When they point at the primary checkout rather than the linked worktree ddflow is
    running from (`infra.paths.launch_parent`): they use the PRIMARY's code, not the code
    running this command, until the branch merges -- and the interpreter, named when it
    was swapped for the primary's, or warned about when it could not follow. When an
    explicit `DDFLOW_LAUNCH_ROOT` holds no ddflow package: every line would fail. Given
    the ``line`` actually written, nothing is said about one that embeds no path (an
    installed `ddflow` script).
    """

    if line is not None and "PYTHONPATH=" not in line:
        return ""
    forced = os.environ.get(LAUNCH_ROOT_ENV)
    if forced and not (Path(forced) / "ddflow" / "__init__.py").is_file():
        return (
            f"WARNING: {LAUNCH_ROOT_ENV}={forced} holds no ddflow package, and the hooks "
            f"and MCP entry written now point there: they will fail until it does."
        )
    tree = redirected_from()
    if tree is None:
        return ""
    python = launch_python()
    note = (
        f"NOTE: ddflow is running from the linked worktree {tree}, which is removed when "
        f"its branch merges; the hooks and MCP entry point at its primary checkout "
        f"{launch_parent()} instead, so they run that checkout's code"
    )
    exe_dir = Path(os.path.abspath(python)).parent.resolve()
    if exe_dir.is_relative_to(tree):
        return note + (
            f". WARNING: the interpreter {python} is still inside the worktree "
            f"(the primary has no .venv): re-run this from the primary checkout."
        )
    if python != sys.executable:
        whose = (
            "the primary's interpreter"
            if Path(python).is_relative_to(launch_parent())
            else ("the interpreter")
        )
        note += f", with {whose} {python}"
    return note + "."


def uninstall(repo: Path) -> str:
    d = hooks_dir(repo)
    out = []
    for name in _hooks():
        hook = d / name
        if not hook.exists():
            continue
        text, exact = _read_hook(hook)
        if HOOK_MARKER not in text:
            out.append(f"REFUSED: {hook} is not managed by ddflow; leaving it alone")
            continue
        try:
            rest = hook_region(name).remove(text)
        except RegionError:  # broken markers: nothing here says what is ours to cut
            out.append(f"REFUSED: {hook} has broken ddflow markers; leaving it alone")
            continue
        if rest != text and not exact and "\ufffd" in rest:
            # A byte OUTSIDE the region that is not UTF-8: writing `rest` back would turn it
            # into U+FFFD. One inside the region goes with the region.
            out.append(
                f"REFUSED: {hook} has bytes that are not UTF-8 beside ddflow's lines; "
                f"remove ddflow's region by hand"
            )
            continue
        if rest == text or rest.strip() in ("", "#!/bin/sh"):  # no region: wholly ddflow's
            hook.unlink()
            out.append(f"removed {hook}")
        else:  # a person's own lines share the file: only the region goes
            replace_text(hook, rest)
            out.append(f"removed the ddflow {name} hook from {hook}; the lines around it stay")
    return "\n".join(out) or "no ddflow hook installed"


def installed(repo: Path, name: str = "pre-commit") -> bool:
    """Whether ddflow's check for this git hook runs, by ddflow's hook or pre-commit's."""
    return bool(armed(repo, name).via)


#: The first comment line of every hook file the pre-commit framework writes.
PRECOMMIT_HEADER = "# File generated by pre-commit: https://pre-commit.com"

#: git hook -> the `ddflow hooks <check>` it must run.
_CHECK_FOR = {"pre-commit": "check-commit", "commit-msg": "check-msg"}

#: The same, as printed: spelled out, so `test_remedy_texts` can resolve each command.
CHECK_COMMAND = {
    "check-commit": "`ddflow hooks check-commit`",
    "check-msg": "`ddflow hooks check-msg`",
}


@dataclass(frozen=True)
class Armed:
    """How ddflow's check for one git hook is armed.

    `via` is "ddflow" (ddflow's own hook), "pre-commit" (a pre-commit-framework hook
    whose config declares ddflow's check at that stage), "pre-commit legacy" (ddflow's
    hook, kept by `pre-commit install` as `<name>.legacy`, which pre-commit runs first),
    or "" when nothing runs it. `detail` says which, or why not. `framework` is True when
    the hook file is pre-commit's: its `remedy` is then about the config, never the
    generated file, which the next `pre-commit install` rewrites.
    """

    via: str
    detail: str = ""
    framework: bool = False
    remedy: str = ""


def armed(repo: Path, name: str = "pre-commit") -> Armed:
    try:
        hook = hooks_dir(repo) / name
    except RuntimeError:
        return Armed("")
    if not hook.is_file():
        return Armed("")
    text = hook.read_text("utf-8", errors="replace")
    if HOOK_MARKER in text:
        return Armed("ddflow")
    if PRECOMMIT_HEADER not in text or name not in _CHECK_FOR:
        return Armed("")
    return _armed_by_precommit(repo, hook, name, text)


def _armed_by_precommit(repo: Path, hook: Path, name: str, text: str) -> Armed:
    if not os.access(hook, os.X_OK):
        return Armed(
            "",
            f"{hook} is pre-commit's but not executable, so git skips it",
            True,
            f"Re-run `pre-commit install`, or `chmod +x {hook}`",
        )
    legacy = hook.with_name(f"{name}.legacy")
    if (
        legacy.is_file()
        and os.access(legacy, os.X_OK)
        and HOOK_MARKER in legacy.read_text("utf-8", errors="replace")
    ):
        return Armed(
            "pre-commit legacy",
            f"ddflow's hook, run by the pre-commit framework as {legacy.name}",
            True,
        )
    config_name, stage = _generated_args(text, name)
    config = Path(config_name)
    if not config.is_absolute():
        config = Path(repo) / config  # pre-commit runs from the top of the working tree
    check, command = _CHECK_FOR[name], CHECK_COMMAND[_CHECK_FOR[name]]
    add = (
        f"Add a `repo: local` hook to {config_name} whose entry runs {command} "
        f"at stage `{stage}`, with `always_run: true`"
    )
    try:
        # Read apart from the parse: a path git or the file system rejects (ValueError
        # for an embedded NUL) is "could not read", and only the READER's refusals are
        # caught below, so a bug in the reader is a crash rather than a quiet verdict.
        body = config.read_text("utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return Armed(
            "",
            f"the pre-commit framework runs it, but could not read {config_name}: {exc}",
            True,
            add,
        )
    try:
        doc = read_precommit_yaml(body)
    except UnreadableYaml as exc:
        return Armed(
            "",
            f"the pre-commit framework runs it, but could not read {config_name}: {exc}",
            True,
            f"Check {config_name}; this reader handles plain YAML without anchors or tags",
        )
    always, broken = precommit_hooks_running(doc, stage, check)
    if always:
        return Armed(
            "pre-commit",
            f"armed through the pre-commit framework: {config_name} hook "
            f"{', '.join(repr(i) for i in always)} runs {command}",
            True,
        )
    if broken:
        hook_id, why, fix = broken[0]
        return Armed(
            "",
            f"the pre-commit framework runs it, but {config_name} hook {hook_id!r}, "
            f"which names {command}, {why}",
            True,
            f"{fix} ({hook_id!r} in {config_name})",
        )
    return Armed(
        "",
        f"the pre-commit framework runs it, and {config_name} declares no hook running "
        f"{command} at the {stage} stage",
        True,
        add,
    )
