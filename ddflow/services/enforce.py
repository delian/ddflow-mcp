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
are detectable after the fact (`ddflow doctor` reports commits whose paths no lease
covered), and in a setting where it has to be unbypassable the same check belongs in CI,
where the agent is not the one running it.
"""

from __future__ import annotations

import itertools
import os
import re
import shlex
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..core.model import Lease, fold
from ..core.schedule import globs_overlap, is_shared, shared_globs
from ..infra import proc as P
from ..infra import worktree as W
from ..infra.log import EventLog

HOOK_MARKER = "# DDFLOW-HOOK v1 — managed by `ddflow hooks install`"

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
    return f'{command_line("hooks check-commit", exec_=True)} "$@"'


def command_line(args: str, *, exec_: bool = False) -> str:
    """A shell line running `ddflow <args>` from an environment that has none of ours.

    Shared by the git hook and the Claude Code SessionStart hook: both run with the
    caller's environment rather than the agent's, and both must reach THIS ddflow.
    """
    import shutil

    run = "exec " if exec_ else ""
    script = shutil.which("ddflow")
    if script and not _running_from_source():
        return f'{run}"{script}" {args}'
    from ..infra.paths import launch_parent, launch_python

    pkg_parent = str(launch_parent())
    # The environment prefix goes BEFORE `exec`: `exec VAR=x cmd` runs a command
    # literally named `VAR=x`.
    return (
        f'PYTHONPATH="{pkg_parent}${{PYTHONPATH:+:$PYTHONPATH}}" '
        f'{run}"{launch_python()}" -m ddflow {args}'
    )


def _running_from_source() -> bool:
    here = Path(__file__).resolve()
    return not any(part in ("site-packages", "dist-packages") for part in here.parts)


def hooks_dir(repo: Path) -> Path:
    """The hooks directory, resolved through worktrees and `core.hooksPath`.

    A linked worktree's `.git` is a FILE pointing into the primary checkout, and a repo
    may relocate hooks entirely. Reading the resolved path from git rather than assuming
    `.git/hooks` is what makes this work inside the worktrees ddflow itself creates.
    """
    r = P.run(
        ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"not a git repository: {repo}")
    path = Path(r.stdout.strip())
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
    return f'{command_line("hooks check-msg", exec_=True)} "$@"'


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


def _install_one(
    repo: Path, d: Path, name: str, template: str, invocation: str, force: bool
) -> str:
    hook = d / name
    text = template.format(marker=HOOK_MARKER, invocation=invocation)
    if hook.exists():
        existing = hook.read_text("utf-8", errors="replace")
        if HOOK_MARKER in existing:
            hook.write_text(text, "utf-8")
            _chmod_x(hook)
            return f"updated the ddflow {name} hook at {hook}"
        if PRECOMMIT_HEADER in existing and not force:
            return _over_framework(repo, hook, name)
        if not force:
            # Never clobber someone else's hook. A workflow tool that silently replaces
            # a project's existing checks has done more damage than the discipline it
            # was installing is worth.
            return (
                f"REFUSED: {hook} already exists and is not managed by ddflow. "
                f"Add this line to it yourself:\n"
                f"    {invocation.replace('exec ', '')} || exit 1\n"
                f"or re-run with --force to replace it."
            )
    hook.write_text(text, "utf-8")
    _chmod_x(hook)
    return f"installed the ddflow {name} hook at {hook}"


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
    import os
    import sys

    from ..infra.paths import LAUNCH_ROOT_ENV, launch_parent, launch_python, redirected_from

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
        if HOOK_MARKER not in hook.read_text("utf-8", errors="replace"):
            out.append(f"REFUSED: {hook} is not managed by ddflow; leaving it alone")
            continue
        hook.unlink()
        out.append(f"removed {hook}")
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

#: pre-commit < 3.2 stage names, still accepted (clientlib._STAGES in pre-commit 4.6).
_OLD_STAGE = {"commit": "pre-commit", "merge-commit": "pre-merge-commit", "push": "pre-push"}


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


def _generated_args(text: str, name: str) -> tuple[str, str]:
    """(config path, hook type) from the `ARGS=(hook-impl ...)` line pre-commit bakes in."""
    config, stage = ".pre-commit-config.yaml", name
    m = re.search(r"^ARGS=\((.*)\)\s*$", text, re.M)
    if not m:
        return config, stage
    args = _words(m.group(1))
    for k, a in enumerate(args):
        if a.startswith("--config="):
            config = a.split("=", 1)[1]
        elif a in ("-c", "--config") and k + 1 < len(args):
            config = args[k + 1]
        elif a.startswith("--hook-type="):
            stage = a.split("=", 1)[1]
    return config, stage


def _words(s: str) -> list[str]:
    """`s` split as a shell would, or on whitespace when its quoting is unbalanced."""
    try:
        return shlex.split(s)
    except ValueError:
        return s.split()


def _as_list(v) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, list) else [v]


def _false(v) -> bool:
    # pre-commit reads its config with PyYAML (YAML 1.1), where no/off are false too.
    return isinstance(v, str) and v.strip().lower() in ("false", "no", "off")


def _true(v) -> bool:
    return isinstance(v, str) and v.strip().lower() in ("true", "yes", "on")


#: pre-commit's default for each file filter (clientlib.py, 4.6): at these values, or
#: absent, a filter lets every staged file through -- and the commit-msg file too.
_NO_FILTER = {
    "files": "",
    "exclude": "^$",
    "types": ["file"],
    "types_or": [],
    "exclude_types": [],
}


def _filters(d: dict, keys) -> bool:
    """Whether `d` narrows the files a hook runs on with any of `keys`."""
    return any(d.get(k) not in (None, _NO_FILTER[k]) for k in keys)


def _runs_check(hook: dict, check: str) -> bool:
    """Whether this configured hook's command (`entry` + `args`) is `... hooks <check>`."""
    if not isinstance(hook.get("entry"), str) or hook.get("language") in ("fail", "pygrep"):
        # `fail` prints its entry and `pygrep` greps for it -- neither runs it.
        return False
    cmd = " ".join([hook["entry"], *(str(a) for a in _as_list(hook.get("args")))])
    if "ddflow" not in cmd.lower():
        # `echo hooks check-commit` is not ddflow. A wrapper not named for ddflow is
        # missed -- reported NOT installed, the safe direction for a status to err in.
        return False
    words = _words(cmd)
    return any(words[k : k + 2] == ["hooks", check] for k in range(len(words)))


def _broken(hook: dict, check: str, top_filters: bool) -> tuple[str, str] | None:
    """(why, fix) when a hook that names ddflow's check does not actually run it on every
    commit, or None when it does."""
    passes_files = not _false(hook.get("pass_filenames"))
    if check == "check-commit" and passes_files:
        return (
            "passes the staged file names as arguments, which `ddflow hooks check-commit` "
            "refuses (a usage error, exit 2, on every commit)",
            "Set `pass_filenames: false` on it",
        )
    if check == "check-msg" and not passes_files:
        # check-msg reads the message FILE pre-commit passes as its argument. Without
        # file names pre-commit skips the hook ("no files to check") -- unless it is
        # `always_run`, when check-msg runs without its required argument and exits 2.
        why = (
            "runs check-msg with no message file, a usage error (exit 2) that refuses every commit"
            if _true(hook.get("always_run"))
            else "passes no message file, so pre-commit skips it and nothing checks the message"
        )
        return f"has `pass_filenames: false`: it {why}", "Remove `pass_filenames: false` from it"
    if (top_filters or _filters(hook, tuple(_NO_FILTER))) and not _true(hook.get("always_run")):
        return (
            "runs only when a file matches its filters (files/exclude/types), and is "
            "skipped otherwise",
            "Set `always_run: true` on it",
        )
    return None


def precommit_hooks_running(
    doc, stage: str, check: str
) -> tuple[list[str], list[tuple[str, str, str]]]:
    """(armed, broken): ids of the `repo: local` hooks in a parsed
    `.pre-commit-config.yaml` that run `ddflow hooks <check>` on every commit when the git
    hook of type `stage` fires; and `(id, why, fix)` for those that name it but do not.

    Only `repo: local` hooks: a remote hook's `language` (and so whether its entry runs
    at all) is in its repository's manifest, which is not read here.

    pre-commit's own rules (repository.py, clientlib.py, commands/run.py in 4.6): a hook
    runs at a stage listed in its `stages`; empty or absent, those default to the
    top-level `default_stages`, which default to EVERY stage. The pre-3.2 names
    `commit`, `push` and `merge-commit` still mean `pre-commit`, `pre-push` and
    `pre-merge-commit`. A hook whose `files`/`exclude`/`types*` (or the top-level
    `files`/`exclude`) match nothing is SKIPPED -- "(no files to check)", which for
    commit-msg means the message file -- unless it has `always_run: true`. And
    `pass_filenames` must fit the check: check-commit takes no arguments, check-msg
    needs the message file.
    """
    if not isinstance(doc, dict):
        return [], []
    default = _as_list(doc["default_stages"]) if "default_stages" in doc else None
    top_filters = _filters(doc, ("files", "exclude"))
    armed: list[str] = []
    broken: list[tuple[str, str, str]] = []
    for repo in _as_list(doc.get("repos")):
        if not isinstance(repo, dict) or repo.get("repo") != "local":
            continue
        for hook in _as_list(repo.get("hooks")):
            if not isinstance(hook, dict) or not _runs_check(hook, check):
                continue
            stages = _as_list(hook.get("stages")) or default
            if stages is not None and stage not in {_OLD_STAGE.get(str(s), str(s)) for s in stages}:
                continue
            hook_id = str(hook.get("id", "?"))
            wrong = _broken(hook, check, top_filters)
            if wrong:
                broken.append((hook_id, *wrong))
            else:
                armed.append(hook_id)
    return armed, broken


# -- a YAML reader for .pre-commit-config.yaml ------------------------------------------
#
# PyYAML is not a dependency and should not become one to read one file (pyproject.toml
# says why the dependency list is one line; `adopt._add_aider_read` edits YAML as text for
# the same reason). This reads the subset pre-commit configs are written in: block
# mappings and sequences (indented or compact), flow `[..]`/`{..}`, plain, single- and
# double-quoted scalars, `|`/`>` block scalars, comments. Every scalar stays a string.
# Anchors, aliases, tags and anything else it does not know raise UnreadableYaml, which the
# caller reports as "could not read" -- never guessed into a pass.


class UnreadableYaml(ValueError):
    """The text is outside the YAML this reader understands. Its own class, so a bug in
    the reader is a crash and not a quiet "could not read"."""


def read_precommit_yaml(text: str):
    try:
        return _YamlReader(text).document()
    except RecursionError:
        raise UnreadableYaml("nested too deeply") from None


class _YamlReader:
    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()
        self.i = 0
        self.started = False

    def document(self):
        node = self.node(0)
        if self._peek() is not None:
            raise UnreadableYaml(f"line {self.i + 1}: unexpected indentation")
        return node

    def _peek(self) -> tuple[int, str] | None:
        """(indent, content without comment) of the next meaningful line, or None."""
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            s = _strip_comment(raw.strip())
            if self.started and s in ("---", "..."):
                raise UnreadableYaml(f"line {self.i + 1}: more than one YAML document")
            if s and s not in ("---", "...") and not s.startswith("%"):
                self.started = True
                lead = raw[: len(raw) - len(raw.lstrip())]
                if "\t" in lead:
                    raise UnreadableYaml(f"line {self.i + 1}: tab in indentation")
                return len(lead), s
            self.i += 1
        return None

    def node(self, indent: int):
        p = self._peek()
        if p is None or p[0] < indent:
            return None
        ind, s = p
        if _is_item(s):
            return self.seq(ind)
        if _yaml_key(s) is not None:
            return self.mapping(ind)
        self.i += 1
        return self.value(s, ind - 1)

    def seq(self, ind: int) -> list:
        items = []
        while (p := self._peek()) and p[0] == ind and _is_item(p[1]):
            body = p[1][1:].lstrip(" ")
            if body:
                # `- key: v` opens a node whose first line sits where `key` does.
                self.lines[self.i] = " " * (ind + len(p[1]) - len(body)) + body
                items.append(self.node(ind + 1))
                continue
            self.i += 1
            nxt = self._peek()
            items.append(self.node(nxt[0]) if nxt and nxt[0] > ind else None)
        return items

    def mapping(self, ind: int) -> dict:
        out: dict = {}
        while (p := self._peek()) and p[0] == ind and not _is_item(p[1]):
            kv = _yaml_key(p[1])
            if kv is None:
                raise UnreadableYaml(f"line {self.i + 1}: expected `key: value`")
            key, rest = kv
            self.i += 1
            if rest:
                out[key] = self.value(rest, ind)
                continue
            nxt = self._peek()
            if nxt and nxt[0] > ind:
                out[key] = self.node(nxt[0])
            elif nxt and nxt[0] == ind and _is_item(nxt[1]):
                out[key] = self.seq(ind)  # compact: `key:` then `- x` level with the key
            else:
                out[key] = None
        return out

    def value(self, s: str, ind: int):
        """The value `s` from the line just consumed; a continuation is any following
        line indented deeper than `ind`."""
        if s[0] in "&*!":
            raise UnreadableYaml(f"line {self.i}: anchors, aliases and tags are not supported")
        if s[0] in "@`%" or s[:2] in ("- ", "? ", ": ") or s in ("-", "?", ":"):
            raise UnreadableYaml(f"line {self.i}: a plain scalar cannot start with {s[0]!r}")
        if s[0] in "|>":
            return self._block(s, ind)
        if s[0] in "[{":
            while _unbalanced(s) and self.i < len(self.lines):
                s += " " + _strip_comment(self.lines[self.i].strip())
                self.i += 1
            return _whole(s, _flow)
        if s[0] in "'\"":
            while _unbalanced(s, _quoted) and self.i < len(self.lines):
                s += " " + self.lines[self.i].strip()
                self.i += 1
            return _whole(s, _quoted)
        parts = [s]
        while (p := self._peek()) and p[0] > ind:  # a plain scalar folded over lines
            parts.append(p[1])
            self.i += 1
        return _plain(" ".join(parts))

    def _block(self, header: str, ind: int) -> str:
        m = re.fullmatch(r"[|>](?:([1-9])?([+-])?|([+-])([1-9]))", header)
        if not m:
            raise UnreadableYaml(f"line {self.i}: unsupported block scalar header {header!r}")
        explicit, chomp = m.group(1) or m.group(4), m.group(2) or m.group(3) or ""
        width = ind + int(explicit) if explicit else None
        body: list[str] = []
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            if raw.strip():
                lead = len(raw) - len(raw.lstrip(" "))
                width = width if width is not None else lead
                if lead <= ind or lead < width:
                    break
                body.append(raw[width:])
            else:
                body.append("")
            self.i += 1
        while body and not body[-1] and chomp != "+":
            body.pop()
        text = "\n".join(body) if header[0] == "|" else _fold(body)
        return text if chomp == "-" or not body else text + "\n"


def _fold(lines: list[str]) -> str:
    """A `>` block scalar's lines, folded: a single break becomes a space, a blank line
    a break, and lines around a more-indented one keep their breaks."""
    out = lines[0] if lines else ""
    for prev, line in itertools.pairwise(lines):
        if not line:
            out += "\n"
        elif not prev or line[0] == " " or prev[0] == " ":
            out += ("\n" if prev else "") + line
        else:
            out += " " + line
    return out


def _is_item(s: str) -> bool:
    return s == "-" or s.startswith("- ")


def _yaml_key(s: str) -> tuple[str, str] | None:
    """(key, rest) for a `key: rest` line, or None when it is not one."""
    split = _key_split(s)
    return None if split is None else (split[0], s[split[1] :].strip())


def _key_split(s: str) -> tuple[str, int] | None:
    """(key, index just past its `:`) when `s` opens with a mapping key, else None."""
    if not s or s[0] in "[{#":
        return None
    if s[0] in "'\"":
        try:
            k, end = _quoted(s, 0)
        except UnreadableYaml:
            return None
        colon = _skip_spaces(s, end)
        if s[colon : colon + 1] != ":" or s[colon + 1 : colon + 2] not in ("", " "):
            return None
        return k, colon + 1
    for k, ch in enumerate(s):
        if ch == "#" and s[k - 1] in " \t":
            return None  # a comment before any `: `
        if ch == ":" and s[k + 1 : k + 2] in ("", " "):
            return s[:k].rstrip(), k + 1
    return None


def _strip_comment(s: str) -> str:
    """`s` without a trailing `# comment`.

    Only where the VALUE starts decides what a quote means: a value that opens with a
    quote is a quoted scalar, and inside a plain one (`bash -c 'echo #x'`) a quote is a
    character like any other and ` #` begins a comment -- as PyYAML reads it.
    """
    k = 0
    while k < len(s):  # past the `- ` and `key: ` prefixes, to where the value starts
        if s[k] == "-" and s[k + 1 : k + 2] in ("", " "):
            k = _skip_spaces(s, k + 1)
            continue
        split = _key_split(s[k:])
        if split is None:
            break
        k = _skip_spaces(s, k + split[1])
    value, end = s[k:], 0
    if value[:1] in ("'", '"'):
        try:
            end = _quoted(value, 0)[1]
        except UnreadableYaml:
            return s  # a quoted scalar that continues on the next line
    elif value[:1] in ("[", "{"):
        end = _flow_end(value)
    for j in range(end, len(value)):
        if value[j] == "#" and (j == 0 or value[j - 1] in " \t"):
            return (s[:k] + value[:j]).rstrip()
    return s


def _flow_end(v: str) -> int:
    """Where the quoted scalars of a flow collection stop hiding `#`: a quote opens one
    only after `[`, `{`, `,`, `:` or a space."""
    quote, k = "", 0
    while k < len(v):
        ch = v[k]
        if quote == '"' and ch == "\\":
            k += 2
            continue
        if quote and ch == quote:
            if quote == "'" and v[k + 1 : k + 2] == "'":  # '' is an escaped quote
                k += 2
                continue
            quote = ""
        elif not quote and ch in "'\"" and v[k - 1] in " [{,:":
            quote = ch
        elif not quote and ch == "#" and v[k - 1] in " \t":
            return k
        k += 1
    return k


def _unbalanced(s: str, parse=None) -> bool:
    try:
        (parse or _flow)(s, 0)
    except UnreadableYaml:
        return True
    return False


def _whole(s: str, parse):
    v, end = parse(s, 0)
    if s[end:].strip():
        raise UnreadableYaml(f"unexpected text after the value: {s[end:].strip()!r}")
    return v


_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"', "/": "/", "0": "\0", " ": " "}


def _quoted(s: str, k: int) -> tuple[str, int]:
    """A quoted scalar starting at s[k]; (value, index after the closing quote)."""
    q, out, k = s[k], [], k + 1
    while k < len(s):
        ch = s[k]
        if ch == q and q == "'" and s[k + 1 : k + 2] == "'":
            out.append("'")
            k += 2
        elif ch == q:
            return "".join(out), k + 1
        elif q == '"' and ch == "\\":
            nxt = s[k + 1 : k + 2]
            if nxt not in _ESCAPES:
                raise UnreadableYaml(f"unsupported escape \\{nxt} in a quoted scalar")
            out.append(_ESCAPES[nxt])
            k += 2
        else:
            out.append(ch)
            k += 1
    raise UnreadableYaml("unterminated quoted scalar")


def _skip_spaces(s: str, k: int) -> int:
    while k < len(s) and s[k] == " ":
        k += 1
    return k


def _flow(s: str, k: int) -> tuple[object, int]:
    """A flow collection or scalar starting at s[k]; (value, index after it)."""
    k = _skip_spaces(s, k)
    if k >= len(s):
        raise UnreadableYaml("unexpected end of a flow collection")
    if s[k] in "'\"":
        return _quoted(s, k)
    if s[k] in "&*!":
        raise UnreadableYaml("anchors, aliases and tags are not supported")
    if s[k] not in "[{":
        end = k
        while end < len(s) and s[end] not in ",]}" and s[end : end + 2] not in (": ", ":"):
            end += 1
        return _plain(s[k:end]), end
    close, k = ("]" if s[k] == "[" else "}"), k + 1
    items: list = []
    pairs: dict = {}
    while True:
        k = _skip_spaces(s, k)
        if k >= len(s):
            raise UnreadableYaml("unterminated flow collection")
        if s[k] == close:
            return (items if close == "]" else pairs), k + 1
        v, k = _flow(s, k)
        k = _skip_spaces(s, k)
        if close == "}":
            if not isinstance(v, str) or s[k : k + 1] != ":":
                raise UnreadableYaml("expected `key:` in a flow mapping")
            pairs[v], k = _flow(s, k + 1)
            k = _skip_spaces(s, k)
        else:
            items.append(v)
        if s[k : k + 1] == ",":
            k += 1
        elif s[k : k + 1] != close:
            raise UnreadableYaml("expected `,` in a flow collection")


def _plain(s: str):
    s = s.strip()
    return None if s in ("", "~", "null", "Null", "NULL") else s


def _chmod_x(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _this_worktree(repo: Path) -> Path | None:
    """The git working tree this hook is running in, or None.

    `--show-toplevel` from the cwd, because the hook's cwd IS the tree being committed
    — which is exactly the information needed to decide which lease applies.
    """
    r = P.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return Path(r.stdout.strip()).resolve()


def _committing_tree(repo: Path) -> Path | None:
    """The tree the commit being checked is made in: `_this_worktree`, but only when it
    is a checkout of ``repo``'s own repository; None otherwise.

    ``repo`` is the PRIMARY checkout (the CLI resolves it so), which is the wrong place
    to ask what a commit in a linked worktree stages. A cwd outside git, or in some
    other repository, says nothing about this commit (bug Bba366d9893).
    """
    here = _this_worktree(repo)
    if here is None:
        return None
    if here == Path(repo).resolve():
        return here
    try:
        same = W.repo_root(here).resolve() == W.repo_root(Path(repo)).resolve()
    except W.GitError:
        return None
    return here if same else None


def _index_tree(repo: Path) -> Path:
    """Where to run a read of THIS commit's index: the committing tree, else ``repo``.

    `git -C <primary> diff --cached` inside a linked worktree compared the index git
    hands the hook (`GIT_INDEX_FILE`, the worktree's) with the PRIMARY's HEAD, so every
    file main changed since the branch point, and every change the branch had already
    committed, read as staged (bug Bba366d9893). Run in the committing tree, the read
    gets that tree's HEAD, and its own index -- or, when git set one, the temporary
    index of a `commit -a` or `commit <paths>`, which is inherited, never dropped.
    Falling back to ``repo`` keeps the old behaviour where no tree of this repository
    is in sight.
    """
    return _committing_tree(repo) or repo


def staged_paths(repo: Path, *, tree: Path | None = None) -> list[str] | None:
    """Paths this commit will write.

    `--diff-filter=ACMR` over the INDEX, plus `--cached`, because a file the agent just
    created is not in HEAD and a diff against HEAD alone would not see it. Read in the
    committing tree (`_index_tree`, or ``tree`` when the caller already resolved it),
    against its own HEAD. Through
    `W.git_paths`, so a non-ASCII or non-UTF-8 name is neither C-quoted past the lease
    and view checks nor a crash of every commit.

    None when git could not say. It used to collapse to `[]`, and "nothing staged" lets
    every check pass: a damaged index (git exits 128, "index file smaller than expected")
    turned the lease and view checks into clean passes (roborev on 18cae1a). Callers
    refuse on None. NOT a held `index.lock`: these reads take no lock and succeed under
    one (verified), so naming it would send an operator hunting for the wrong cause.
    """
    return W.git_paths(
        tree or _index_tree(repo), "diff", "--cached", "--name-only", "--diff-filter=ACMR"
    )


#: The refusal when the staged set itself is unknowable. Never a pass: "could not tell"
#: is not "nothing to check".
_UNKNOWN_STAGED = (
    "ddflow: git could not report which paths this commit stages, so it cannot be "
    "checked.\nRefusing rather than guessing. Check `git status`: a damaged or "
    "truncated `.git/index` is the usual cause."
)


#: Paths ddflow's own bookkeeping writes. Requiring a lease for these would make it
#: impossible to commit the event log that records the lease.
SELF_MANAGED = (".ddflow/", "docs/ddflow/", "AGENTS.md", "CLAUDE.md", ".cursor/rules/")


def _counts_as_mine(repo: Path, lease: Lease, me: str, here: Path | None) -> bool:
    """A lease is "mine" if I hold it, OR if it created the very tree this commit is
    happening in. The second test is the robust one: the hook runs inside a worktree,
    and the lease that produced that worktree is the relevant claim no matter which
    process id or identity string made it."""
    if lease.holder == me:
        return True
    leased_tree = W.load_path(repo, lease.worktree) if lease.worktree else None
    return bool(here and leased_tree and leased_tree.resolve() == here)


def _lapsed_lines(item_id: str, lease: Lease, now: float, me: str) -> list[str]:
    """What to say about a lapsed lease on this work: when it lapsed, and how to renew
    it -- as its holder, since a heartbeat from the tree will not revive an expired one.

    Matched by the tree alone (the holder is not the identity this hook resolved), the
    committer is USUALLY the holder under a derived name, but may be someone `recover`
    sent to take the abandoned work over. Both readings are offered rather than telling
    a newcomer to resurrect a claim its holder abandoned.
    """
    idle = int((now - lease.renewed_at) // 60)
    ago = max(0, int((now - lease.renewed_at - lease.ttl_s) // 60))
    whose = "Your lease" if lease.holder == me else "The lease"
    lines = [
        f"{whose} on {item_id} (held by {lease.holder}) LAPSED {ago} min ago: no",
        f"heartbeat for {idle} min, past its {lease.ttl_s // 60} min TTL, and nobody has taken",
        "it over.",
    ]
    if lease.holder == me:
        lines += ["Renew it, then commit again:"]
    else:
        lines += [f"This is that item's tree. If you are {lease.holder}, renew it, then commit:"]
    lines.append(f"    ddflow --agent {lease.holder} heartbeat {item_id}")
    if lease.holder != me:
        # The path `claim`'s own refusal names (`_claim_blocker`): a bare claim of an
        # expired lease is refused, since a crashed agent's tree often holds finished
        # work, and `--force` would also waive the dependency and overlap checks.
        lines += [
            f"If you are not {lease.holder}, the work was abandoned; take it over by:",
            f"    ddflow recover --item {item_id}    # what its tree holds; salvage it",
            f"    ddflow release {item_id} --note salvaged",
            f"    ddflow claim {item_id}",
        ]
    return [*lines, ""]


def _derived_identity_lines(me: str, held_by: dict[str, Lease]) -> list[str]:
    """Nobody said who is committing, so the identity was derived from this tree -- and
    "ANOTHER agent" may be the committer itself, working outside its item's tree under
    a name it declared only to its claim (an MCP `as_agent`, a `--agent`). The hook
    cannot tell (B9aeb141b9a); it says how the name was found and what the holder
    should do, rather than blaming it for its own lease.
    """
    lines = [
        f"Nothing declared who is committing, so this hook named you {me}, from this",
        "tree. If you ARE one of the holders above, you are outside its item's tree:",
    ]
    for item_id, lease in held_by.items():
        where = lease.worktree or "(no tree recorded)"
        lines += [
            f"  {lease.holder} on {item_id}: commit in {where},",
            f"    or declare yourself:  DDFLOW_AGENT={lease.holder} git commit ...",
        ]
    return [*lines, ""]


def _merged_in(tree: Path) -> str:
    """The one commit being merged into ``tree``'s HEAD, or "" when not exactly one.

    MERGE_HEAD where `git commit` concludes a merge, and at `git merge`'s commit-msg
    stage. At its pre-merge-commit stage MERGE_HEAD is not written yet, and git exports
    the merged commit as ``GITHEAD_<sha>`` instead (probed, git 2.43). An octopus merge
    names several, and gets no answer. A squash has neither: `ddflow merge` names the
    squashed commit in ``DDFLOW_SQUASH_OF`` for the commit it makes.

    All three are the committer's to set, like `--no-verify` is: this is a check on
    agents following the workflow, not a barrier (module docstring). Forging one buys
    nothing `--no-verify` does not, and the exactness test below still has to hold.
    """
    r = W.git(tree, "rev-parse", "--git-path", "MERGE_HEAD")
    if r.ok and r.out:
        f = Path(r.out) if Path(r.out).is_absolute() else tree / r.out
        try:
            heads = f.read_text().split()
        except OSError:
            heads = []
        if heads:
            return heads[0] if len(heads) == 1 else ""
    env = [k[len("GITHEAD_") :] for k in os.environ if k.startswith("GITHEAD_")]
    if env:
        return env[0] if len(env) == 1 else ""
    return os.environ.get(W.SQUASH_OF, "").strip()


def clean_merge_conclusion(tree: Path) -> bool:
    """Is the commit being made exactly the automatic merge of HEAD and one other commit?

    Then it adds nothing relative to its parents: every path it stages is already
    committed on one of them, and a lease check of the merge itself has nothing of its
    own to judge. (Whether the merged commits were made under a lease is their own
    commits' check -- or `--no-verify`'s, which this cannot see either.) Judging it
    against the committer's leases only ever went wrong: `ddflow merge` commits in the
    PRIMARY, whose derived identity holds nothing (B9b57176aac), from an event log the
    pre-commit framework may have stashed (Bc50bc7fb58), and the refusal left the
    primary mid-merge.

    Exact: the index must equal `git merge-tree --write-tree HEAD <it>`. A hand-resolved
    conflict, or anything added on top, differs and is checked as usual. A git too old
    for `merge-tree --write-tree` (< 2.38) answers False.
    """
    other = _merged_in(tree)
    if not other:
        return False
    auto = W.git(tree, "merge-tree", "--write-tree", "HEAD", other)
    if not auto.ok or not auto.out:
        return False  # conflicts (exit 1), or no --write-tree
    index = W.git(tree, "write-tree")
    return index.ok and index.out == auto.out.splitlines()[0]


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

    staged = staged_paths(repo)
    if staged is None:
        return (
            (0, _UNKNOWN_STAGED + "\n\n(warning only)") if mode == "warn" else (1, _UNKNOWN_STAGED)
        )
    paths = [p for p in staged if not any(p.startswith(prefix) for prefix in SELF_MANAGED)]
    # A clean merge commit stages only what its parents already committed.
    if not paths or clean_merge_conclusion(_index_tree(repo)):
        return 0, ""

    log = EventLog(repo, agent or cfg.agent.id or "", log_cfg=cfg.log)
    state = fold(log.read_all(), strict=False)
    now = time.time()
    me = log.agent_id
    here = _committing_tree(repo)
    mine: list[str] = []
    others: dict[str, str] = {}
    holds_any = False
    for item_id, lease in state.active_leases(now, cfg.lease.grace_s).items():
        if _counts_as_mine(repo, lease, me, here):
            holds_any = True
            mine.extend(lease.globs)
        else:
            for g in lease.globs:
                others[g] = f"{item_id} ({lease.holder})"

    # A shared file (`[lease] shared_globs` / `append_only_globs`) is every live holder's
    # to edit (D-shared-globs): the changelog line each item adds is not a trespass.
    shared = shared_globs(cfg) if holds_any else []
    uncovered = [
        p for p in paths if not is_shared(p, shared) and not any(globs_overlap(p, g) for g in mine)
    ]
    if not uncovered:
        return 0, ""

    # A path another agent holds is the dangerous case and gets named separately: the
    # remedy is not "claim it", it is "stop".
    stolen = {p: owner for p in uncovered for g, owner in others.items() if globs_overlap(p, g)}

    # A lease that WOULD have been mine, by the same two tests, but has lapsed and was
    # not taken over. Invisible above, so the message said "(no live lease)" and "claim
    # the work" to the holder committing in its own tree, and the lapse was misread as
    # an identity bug (B3e050cb66a, B5fde61b8a9). Still not a pass: named, not counted.
    # Only one no other agent's live lease overlaps ANYWHERE: a heartbeat revives all its
    # globs, and reviving them over someone's live paths is the very race the STOP
    # text warns against.
    lapsed = [
        (item_id, lease)
        for item_id, lease in state.expired_leases(now, cfg.lease.grace_s).items()
        if _counts_as_mine(repo, lease, me, here)
        and any(globs_overlap(p, g) for p in uncovered for g in lease.globs)
        and not any(globs_overlap(g, o) for g in lease.globs for o in others)
    ]

    lines = [
        f"ddflow: {len(uncovered)} staged path(s) are not covered by a lease you hold.",
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
        # `_load` writes the derived name back into cfg.agent.id; its source says so.
        derived = not cfg.agent.id or cfg.sources.get("agent.id") == "derived"
        if not agent and derived:
            held_by = {
                item_id: lease
                for item_id, lease in state.active_leases(now, cfg.lease.grace_s).items()
                if any(globs_overlap(p, g) for p in stolen for g in lease.globs)
            }
            lines += _derived_identity_lines(me, held_by)
    for item_id, lease in lapsed:
        lines += _lapsed_lines(item_id, lease, now, me)
    held = ", ".join(mine) if mine else "(no live lease)"
    if lapsed and not mine:
        held = "nothing live (the lease above lapsed)"
    lines += [
        f"You hold: {held}",
        "",
        "Otherwise, claim the work or widen the claim you already hold:"
        if lapsed
        else "Fix by claiming the work, or by widening the claim you already hold:",
        "    ddflow next                     # what may be started",
        "    ddflow claim <ID> --globs '...' # lease it and get a worktree",
        "    ddflow update <ID> --globs '...'# widen an existing claim",
        "",
        f'Policy is [enforce].commit_without_lease = "{mode}" in .ddflow/config.toml.',
    ]
    msg = "\n".join(lines)
    if mode == "warn":
        return 0, msg + '\n\n(warning only; set the policy to "block" to refuse)'
    return 1, msg


def staged_bytes(repo: Path, path: str, *, tree: Path | None = None) -> bytes | None:
    """The INDEX copy of ``path`` -- what the commit will actually write.

    Not the working copy: a file fixed on disk but not re-staged would pass a check of
    the disk while the commit carried the broken bytes. Read in the committing tree, the
    same index `staged_paths` listed ``path`` from.
    """
    r = P.run(
        ["git", "-C", str(tree or _index_tree(repo)), "show", f":{path}"],
        capture_output=True,
        timeout=60,
    )
    return r.stdout if r.returncode == 0 else None


def check_views(repo: Path, cfg: Config | None = None, *, agent: str = "") -> tuple[int, str]:
    """(exit_code, message) for B18: a staged generated view must be byte-identical to
    what the log regenerates now.

    `docs/ddflow/` is SELF_MANAGED, so the lease check waves views through -- rightly,
    since rendering needs no claim -- and until this nothing checked them at all. A view
    that disagrees with the log is worse than none: it is the page people read INSTEAD
    of the log. It goes wrong two ways, both caught here: hand-edited, or stale because
    the queue moved after `ddflow render`.

    A file counts as a view when its name is in `VIEWS` AND its staged content carries
    the GENERATED marker -- so `render --out elsewhere` is still checked, while a
    project's own `QUEUE.md`, or a document that merely quotes the marker, is not.
    Fires only when a view is STAGED -- or when git cannot report the staged set at all,
    since "could not tell whether a view is staged" is not "no view is staged". A commit
    that stages no view is never blocked because the queue moved, which would teach
    everyone to bypass the hook.
    """
    from ..views.markdown import GENERATED, VIEWS, render_views

    cfg = cfg or Config.load(repo)
    mode = cfg.enforce.generated_views
    if mode == "off":
        return 0, ""
    staged: dict[str, bytes] = {}
    names = {name for name, _ in VIEWS}
    # Resolved once: the same tree for the listing, each staged view's bytes and the log.
    tree = _index_tree(repo)
    listed = staged_paths(repo, tree=tree)
    if listed is None:
        return _verdict(mode, [_UNKNOWN_STAGED])
    for p in listed:
        if Path(p).name not in names:
            continue
        data = staged_bytes(repo, p, tree=tree)
        if data is not None and data.startswith(GENERATED.encode("utf-8")):
            staged[p] = data
    if not staged:
        return 0, ""

    log = EventLog(repo, agent or cfg.agent.id or "", log_cfg=cfg.log)
    # The view is committed WITH a log, and must agree with THAT log -- not with the
    # one on disk. Staging a view rendered from events that are not themselves staged
    # would commit a page describing work its own commit does not record (roborev on
    # 7216f5e, reproduced). Rather than fold shards out of the index, require the log to
    # be fully staged: then the log on disk IS the committed log, and the comparison
    # below is exact.
    probe = _unstaged_under(repo, log.dir, tree)
    if probe.failed:
        return _verdict(
            mode,
            [
                "ddflow: a generated view is staged, but git could not report the state of "
                f"the event log ({_rel(repo, log.dir)}) it must agree with.",
                "",
                "Refusing rather than guessing: an unreadable log state is not a clean one.",
                "Check `git status`; a locked or damaged index is the usual cause.",
            ],
        )
    if probe.paths:
        # Each case gets the command that CLEARS it. A plain `git add` stages nothing for
        # an ignored file and exits 0, so the one remedy for every case left an ignored
        # shard refused forever with identical output (roborev on 8b167e9, reproduced).
        remedy = []
        if probe.ignored:
            remedy += [
                "These are GITIGNORED, so a plain `git add` skips them. Force them in, or",
                "stop ignoring shards (a committed log with some shards ignored cannot",
                "match a view rendered from all of them):",
                "    git add -f " + " ".join(shlex.quote(p) for p in probe.ignored),
            ]
        if set(probe.paths) - set(probe.ignored):
            remedy += [f"    git add {shlex.quote(_rel(repo, log.dir))}"]
        return _verdict(
            mode,
            [
                f"ddflow: a generated view is staged, but the event log it is rendered from "
                f"has {len(probe.paths)} change(s) the commit does not record:",
                "",
                *(
                    f"  {p}" + ("   (gitignored)" if p in probe.ignored else "")
                    for p in probe.paths[:MAX_LISTED_PATHS]
                ),
                "",
                "A committed view must agree with the log committed beside it. Stage both:",
                *remedy,
                "    ddflow render" + _out_hint(sorted(staged)),
                "    git add " + " ".join(shlex.quote(p) for p in sorted(staged)),
            ],
        )
    # Config from the FILES, as `ddflow render` writes with: env overrides belong to
    # whoever typed `git commit`, not to the view.
    want = render_views(fold(log.read_all(), strict=False), Config.load(repo, env={}))
    wrong = sorted(
        p for p, data in staged.items() if _lf(data) != want[Path(p).name].encode("utf-8")
    )
    if not wrong:
        return 0, ""
    return _verdict(
        mode,
        [
            f"ddflow: {len(wrong)} staged generated view(s) differ from what the event log "
            "regenerates now:",
            "",
            *(f"  {p}" for p in wrong),
            "",
            "A view is regenerated from the log, never edited: either it was changed by hand,",
            "or the queue moved after it was rendered. Regenerate it and stage the result:",
            "    ddflow render" + _out_hint(wrong),
            "    git add " + " ".join(shlex.quote(p) for p in wrong),
        ],
    )


def _verdict(mode: str, lines: list[str], knob: str = "generated_views") -> tuple[int, str]:
    msg = "\n".join([*lines, "", f'Policy is [enforce].{knob} = "{mode}" in .ddflow/config.toml.'])
    if mode == "warn":
        return 0, msg + '\n\n(warning only; set the policy to "block" to refuse)'
    return 1, msg


def check_docs(repo: Path, cfg: Config | None = None) -> tuple[int, str]:
    """(exit_code, message) for B17: a commit that removes or renames an identifier, a
    file or a default must not leave a doc line naming the old one.

    The detection is `services/docsync.py`; this is only the policy around it, the same
    shape as `check_views`. "Git could not tell" is never a pass: it reports, and under
    'block' refuses, exactly like an unreadable staged set.
    """
    from . import docsync

    cfg = cfg or Config.load(repo)
    mode = cfg.enforce.stale_docs
    if mode == "off":
        return 0, ""
    # The committing tree: the diff is this commit's, against that tree's own HEAD.
    hits = docsync.stale_mentions(_index_tree(repo), cfg.enforce.doc_globs, cfg.enforce.doc_exclude)
    if hits is None:
        return _verdict(
            mode,
            [
                "ddflow: git could not report this commit's diff, or search the docs it may",
                "have left stale, so the doc-sync check could not run.",
                "",
                "An unchecked commit is not a clean one: under 'block' this refuses, and",
                "under 'warn' it is reported here rather than passed in silence.",
            ],
            "stale_docs",
        )
    if not hits:
        return 0, ""
    names = sorted({h.name for h in hits})
    return _verdict(
        mode,
        [
            f"ddflow: this commit removes {len(names)} name(s) that {len(hits)} doc line(s) "
            "still mention:",
            "",
            *(f"  {h.path}:{h.line}  {h.name}" for h in hits[:MAX_LISTED_PATHS]),
            *(
                [f"  ... and {len(hits) - MAX_LISTED_PATHS} more"]
                if len(hits) > MAX_LISTED_PATHS
                else []
            ),
            "",
            "A page naming a removed identifier or an old default is read and trusted.",
            "Update those lines in this commit. A page whose job is to remember old names",
            "(a changelog, a backlog) belongs in [enforce].doc_exclude.",
        ],
        "stale_docs",
    )


# -- B23: drift from the base branch ------------------------------------------------------


def rulebooks() -> tuple[str, ...]:
    """The files whose change on the base branch means an agent is working to old rules.

    A session loads these ONCE, at start; an edit that lands on the base afterwards is
    silently ignored by every branch forked before it, and the drift compounds -- in the
    source project a branch 98 commits behind had to be hand-ported. The native rules
    files are DERIVED from `adopt.NATIVE_RULES`, never retyped: a second hand-kept list
    is the duplicate-then-drift class, and an agent added there would go unwatched here.
    The driver directory is `adopt`'s default `--docs` location, which the rules point at.
    """
    from .adopt import NATIVE_RULES

    fixed = ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", ".ddflow/config.toml")
    native = tuple(r.path for r in NATIVE_RULES.values())
    return tuple(dict.fromkeys((*fixed, "docs/ddflow/drivers/", *native)))


@dataclass(frozen=True)
class Drift:
    """How far a tree is behind the branch its work merges into. ONE computation, read by
    the session hook (which informs) and the commit gate (which blocks)."""

    base: str
    #: commits on `base` that HEAD lacks; None = could not tell, which is NEVER "0".
    behind: int | None
    #: rulebooks changed ON `base` since this branch forked (the three-dot diff), so a
    #: rulebook edited on this branch itself is not staleness.
    rules: tuple[str, ...] = ()
    #: why `behind` is None; empty otherwise.
    detail: str = ""


def _item_base(repo: Path, here: Path, cfg: Config | None) -> str:
    """The recorded `Item.base` of the item whose worktree IS ``here``, or "".

    Found by where the item's worktree resolves -- the `same_tree` test `check_commit`
    uses for leases: the tree is the robust key, whichever identity string made it. A
    live item wins over a finished one whose tree still happens to sit at the path. A
    log that cannot be read is "" -- the caller then measures against the default
    branch, which is still a check, not a pass.
    """
    from ..core.model import ABANDONED, DONE

    try:
        cfg = cfg or Config.load(repo)
        state = fold(EventLog(repo, cfg.agent.id or "", log_cfg=cfg.log).read_all(), strict=False)
    except Exception:
        return ""
    here = here.resolve()
    found = [
        it
        for it in state.items.values()
        if it.worktree and it.base and not it.removed
        if W.load_path(repo, it.worktree).resolve() == here
    ]
    found.sort(key=lambda it: it.state in (DONE, ABANDONED))
    return found[0].base if found else ""


def drift_base(repo: Path, here: Path, cfg: Config | None = None) -> str:
    """The branch ``here``'s work merges into: its item's recorded base, else the
    repository's default branch.

    Not simply the default branch: under stacking or gitflow a task forks from its
    dependency's branch or from a release line, and measuring it against `main` would
    report everything that line has not taken yet as drift -- and tell the agent to
    merge a branch its work must not carry. `Item.base` is what the merge itself checks
    against. A recorded base that no longer resolves (a stacked dependency's branch
    deleted after it landed) falls back to the default branch rather than leaving the
    tree at "could not tell" forever.
    """
    base = _item_base(repo, here, cfg)
    if base and W.git(here, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").ok:
        return base
    return W.default_branch(repo)


def drift(repo: Path, here: Path, base: str = "", cfg: Config | None = None) -> Drift:
    """How far ``here``'s HEAD is behind ``base`` (default: `drift_base`), and which
    rulebooks changed there since the fork.

    Any checkout, the primary included: a primary left on a stale branch works to old
    rules just the same, and the primary ON the base branch is simply 0 behind.
    """
    base = base or drift_base(repo, here, cfg)
    n = W.git(here, "rev-list", "--count", f"HEAD..{base}")
    if not n.ok or not n.out.isdigit():
        # "Could not tell" is not "not behind" (roborev 826): an unborn HEAD, an
        # unrelated history or an unresolvable base all land here.
        return Drift(base, None, detail=n.err or n.out or "git rev-list failed")
    if int(n.out) == 0:
        return Drift(base, 0)
    # Three dots: what changed on `base` since the merge base -- NOT this branch's own
    # edits, which are the new rules rather than stale ones.
    changed = W.git_paths(here, "diff", "--name-only", f"HEAD...{base}", "--", *rulebooks())
    if changed is None:
        return Drift(base, None, detail=f"git could not diff HEAD...{base}")
    if changed:
        # ...AND still different here. "Changed on base since the fork" alone refused a
        # branch that had already taken the change by `cherry-pick -x` or by hand, its
        # rulebook byte-identical to base's, and told it to `git merge` for nothing
        # (reviewer, reproduced). Content is the question: a rulebook this branch ALSO
        # changed, differently, still differs and still must merge. Only the paths
        # already flagged are compared, literally -- they are names, not globs.
        differ = W.git_paths(
            here, "--literal-pathspecs", "diff", "--name-only", "HEAD", base, "--", *changed
        )
        if differ is None:
            return Drift(base, None, detail=f"git could not diff HEAD {base}")
        changed = [p for p in changed if p in set(differ)]
    return Drift(base, int(n.out), tuple(changed))


def _concludes_merge_of(here: Path, base: str) -> bool:
    """Is the commit being made the conclusion of `git merge <base>`?

    MERGE_HEAD exists and the base tip is contained in it (or is it). That commit IS the
    remedy this gate asks for: refusing it would make the refusal impossible to clear,
    and a conflicting merge is exactly the case that needs a commit. HEAD still reads as
    behind until the commit lands, so this is asked before the drift is judged. A stale
    MERGE_HEAD opens no hole: if it contains the base tip and HEAD contains it, HEAD is
    not behind in the first place.
    """
    head = W.git(here, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    if not head.ok or not head.out:
        return False
    return W.git(here, "merge-base", "--is-ancestor", base, head.out).ok


def check_drift(
    repo: Path, cfg: Config | None = None, *, here: Path | None = None
) -> tuple[int, str]:
    """(exit_code, message) for B23: refuse a commit on a branch whose base changed the
    rules since it forked, and warn when it has fallen far behind.

    The asymmetry is the source project's, settled after a 98-commits-behind branch had
    to be hand-ported: the session hook INFORMS (it cannot unload rules a session has
    already read), the commit gate BLOCKS (the one point where the agent must stop and
    merge). Rulebook staleness blocks by default because it is precise and the fix is one
    command; a bare behind-count only warns, because being behind is not by itself wrong.
    "Could not tell" warns with the reason and never blocks: a gate that refuses on
    missing evidence teaches `--no-verify`, and one that passes in silence lies.
    """
    cfg = cfg or Config.load(repo)
    e = cfg.enforce
    if e.stale_rules == "off" and e.behind == "off":
        return 0, ""
    if e.behind != "off" and (not isinstance(e.max_behind, int) or e.max_behind < 1):
        # Refused, not read as "off": a 0 that quietly disabled the check would be the
        # silent-knob-drop class. Turning the check off is the policy knob's job. The
        # loader refuses it too; this catches a Config built in code.
        return 1, (
            f"ddflow: [enforce].max_behind = {e.max_behind!r} is invalid: it must be >= 1.\n"
            'To stop the behind-count check, set [enforce].behind = "off" instead.'
        )
    here = here or _committing_tree(repo)
    if here is None:
        return 0, (
            "ddflow: could not tell which working tree this commit is in, so its drift "
            "from the base branch was not checked.\n\n(warning only)"
        )
    d = drift(repo, here, cfg=cfg)
    if d.behind is None:
        return 0, (
            f"ddflow: could not tell whether this branch is behind `{d.base}`: {d.detail}\n"
            "Its rulebooks were not compared, so this is NOT a pass.\n\n(warning only)"
        )
    if d.behind == 0 or _concludes_merge_of(here, d.base):
        return 0, ""
    code, parts = 0, []
    if d.rules and e.stale_rules != "off":
        c, m = _verdict(
            e.stale_rules,
            [
                f"ddflow: `{d.base}` changed the project's rules since this branch forked, "
                "and this branch has not merged them:",
                "",
                *(f"  {p}" for p in d.rules[:MAX_LISTED_PATHS]),
                *(
                    [f"  ... and {len(d.rules) - MAX_LISTED_PATHS} more"]
                    if len(d.rules) > MAX_LISTED_PATHS
                    else []
                ),
                "",
                "A session loads its rules once; edits landing on the base afterwards are",
                "silently ignored here. Merge the base, then RE-READ the files named above:",
                f"    git merge {shlex.quote(d.base)}",
                "(the commit concluding that merge is never refused)",
            ],
            "stale_rules",
        )
        code, parts = max(code, c), [*parts, m]
    if d.behind > e.max_behind and e.behind != "off":
        c, m = _verdict(
            e.behind,
            [
                f"ddflow: this branch is {d.behind} commits behind `{d.base}` (more than "
                f"[enforce].max_behind = {e.max_behind}).",
                "Drift compounds: merging now is cheaper than porting later.",
                f"    git merge {shlex.quote(d.base)}",
            ],
            "behind",
        )
        code, parts = max(code, c), [*parts, m]
    return code, "\n\n".join(parts)


def _rel(repo: Path, path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(repo).resolve()))
    except ValueError:
        return str(path)


@dataclass(frozen=True)
class LogProbe:
    """What the commit would NOT record under the log dir, and why."""

    paths: list[str]  #: not in the index, or modified since staged
    ignored: list[str]  #: the subset a plain `git add` would skip
    failed: bool = False  #: git could not say -- never read as "clean"


def _unstaged_under(repo: Path, d: Path, tree: Path | None = None) -> LogProbe:
    """Paths under ``d`` whose working copy is not what the commit will record:
    modified-but-not-staged, or not in the index at all.

    Two cases, decided by whether ANY shard is tracked. If none is, the project does not
    commit its log, no view can be checked against a committed one, and nothing is
    reported -- the check compares against the working log as before. If one is, EVERY
    file there that is not in the index counts, ignored or not: a project ignoring only
    some shards would otherwise render a view from an ignored shard, commit it beside a
    log that lacks it, and pass (roborev on 43c2034).

    A git failure is `failed`, never an empty list: an empty stdout from a command that
    failed is not evidence of a clean log, and reading it as one would silently reopen
    the exact false pass this exists to close (roborev on 43c2034).

    The files are ``repo``'s and the index is the COMMITTING tree's, deliberately mixed.
    The view is rendered from `EventLog(repo)`, the primary's log on disk; the commit
    records the committing tree's index. Those two are what must agree -- the worktree's
    own working copy of the log is read by neither, so an unstaged edit to it cannot
    make the view wrong. A real hook in a linked worktree already got this pairing: git
    exports the worktree's `GIT_DIR`, and `-C <primary>` then made the primary the work
    tree. It is spelled out here so a check with no git environment (run by hand from
    the worktree) compares the same two things rather than the primary's own index (bug
    Bba366d9893).
    """
    rel = _rel(repo, d)
    via: list[str] = []
    tree = tree or _index_tree(repo)
    if tree.resolve() != Path(repo).resolve():
        gitdir = W.git(tree, "rev-parse", "--absolute-git-dir", timeout=30)
        if not gitdir.ok or not gitdir.out:
            return LogProbe([], [], failed=True)
        via = [f"--git-dir={gitdir.out}", f"--work-tree={Path(repo).resolve()}"]

    def git(*argv: str) -> list[str] | None:
        # `-z`: without it git C-quotes a non-ASCII path (`"caf\303\251.jsonl"`), and a
        # remedy built from that string names a file that does not exist -- `git add -f`
        # fails and the refusal never clears (roborev on 40950c9, reproduced).
        return W.git_paths(repo, *via, *argv, "--", rel)

    tracked = git("ls-files")
    modified = git("diff", "--name-only")
    untracked = git("ls-files", "--others")  # ignored ones included, deliberately
    ignored = git("ls-files", "--others", "--ignored", "--exclude-standard")
    if tracked is None or modified is None or untracked is None or ignored is None:
        return LogProbe([], [], failed=True)
    if not tracked:
        return LogProbe([], [])
    return LogProbe(sorted(set(modified + untracked)), sorted(ignored))


def _lf(data: bytes) -> bytes:
    """Line endings are git's and the platform's business, not the view's content.
    `write_text` writes CRLF on Windows and `core.autocrlf=false` stages it as is."""
    return data.replace(b"\r\n", b"\n")


def _out_hint(paths: list[str]) -> str:
    """` --out DIR` when every wrong view lives outside the default directory together."""
    dirs = {str(Path(p).parent) for p in paths}
    if len(dirs) == 1 and (d := dirs.pop()) != "docs/ddflow":
        return f" --out {shlex.quote(d)}"
    return ""


def _key(line: str) -> str:
    """The `<key>` of a `<key>: value` line, past indentation and comment hashes."""
    return line.split(":", 1)[0].strip().lstrip("#").strip()


def check_forbidden_trailers(message: str, keys: list[str]) -> tuple[int, str]:
    """Refuse a message carrying any of `keys` as a `<key>:` line. `[enforce].forbidden_trailers`.

    A LINE scan, deliberately stricter than `check_item_trailer`'s use of git's parser:
    git reads trailers only from the final paragraph, while a forge credits a co-author
    from such a line wherever it sits, and the preference being enforced is about the
    line. Case-insensitive, because git and forges treat trailer keys so. Prose that
    merely names the key (`a Co-author line`) is not a `<key>:` line and passes.
    """
    wanted = {k.strip().lower() for k in keys if k.strip()}
    if not wanted:
        return 0, ""
    # A leading `#` does not make it a comment that git drops: `git commit -F` cleans up
    # with `whitespace`, which KEEPS `#` lines, so `#<key>: ...` landed in history
    # verbatim (rubber-duck on B-forbid-trailers). Refusing it in the editor route too,
    # where git would strip it, costs one deleted line.
    found = sorted(
        {_key(ln) for ln in message.splitlines() if ":" in ln and _key(ln).lower() in wanted}
    )
    if not found:
        return 0, ""
    return 1, (
        f"ddflow: this commit message carries {', '.join(f'`{k}:`' for k in found)}, which "
        f"[enforce].forbidden_trailers refuses.\n\n"
        f"Remove the line and commit again. Git runs this check for every agent and for "
        f"`git commit -F`, the editor and merges alike."
    )


class QueueUnreadable(Exception):
    """The queue could not be read, so a trailer naming an item could not be checked."""


def queue_ids(repo: Path, cfg: Config) -> set[str]:
    """The id of every item in the queue -- phase or task, any state but removed.

    The queue the pre-commit lease check reads: `repo` is the primary checkout (the CLI
    resolves a linked worktree to it) and the log is its `.ddflow/events`. Answered from
    `.ddflow/index.db` when the index is current -- one query, where a fold re-parses
    every event (~120 ms on a 5k-event log, on every commit) -- and from the log itself
    when it is not; `Store.rebuild` projects exactly the non-removed items, so the two
    answers are the same set (`tests/test_trailer_names_item.py::
    test_the_fresh_index_and_the_fold_agree` holds a removed id refused on both paths).
    A damaged index is a cache miss, not an error.

    Raises `QueueUnreadable` when there is no log to read or reading it fails: an
    unverifiable trailer is "could not run", never a pass.
    """
    import sqlite3
    from contextlib import closing

    from ..infra.store import Store

    log = EventLog(repo, "", log_cfg=cfg.log)
    if not log.dir.is_dir():
        raise QueueUnreadable(f"there is no event log at {log.dir}")
    try:
        store = Store(repo, cfg)
        try:
            if not store.stale(log):
                uri = f"{store.path.resolve().as_uri()}?mode=ro"
                with closing(sqlite3.connect(uri, uri=True, timeout=5)) as con:
                    return {row[0] for row in con.execute("select id from items")}
        except sqlite3.Error:
            pass
        state = fold(log.read_all(), strict=False)
    except OSError as exc:
        raise QueueUnreadable(f"reading {log.dir} failed: {exc}") from exc
    return {it.id for it in state.items.values() if not it.removed}


def _near_ids(value: str, ids: set[str], limit: int = 3) -> list[str]:
    """Cheap guesses at the id `value` meant: the same id in another case, ids that
    extend it (`160.D` -> `160.D.4`), the longest id it extends, then a close spelling."""
    import difflib

    low = value.lower()
    same = sorted(i for i in ids if i.lower() == low)
    if same:
        return same[:limit]
    longer = sorted(i for i in ids if i.lower().startswith(low))
    shorter = sorted((i for i in ids if low.startswith(i.lower())), key=len)
    near = longer[:limit] + shorter[-1:]
    return near[:limit] or difflib.get_close_matches(value, sorted(ids), n=limit, cutoff=0.8)


def _trailers(message: str) -> list[tuple[str, str]] | None:
    """`(key, value)` for every trailer git reads in `message`; None if git could not
    say -- which is "could not check", never "no trailer" (the caller exits 2).

    Git's OWN trailer parser, not a line scan. Git reads trailers only from the final
    paragraph, so `Item: X` in the body passed a line scan while
    `git log --format='%(trailers:key=Item)'` -- the reconciliation this exists for --
    found nothing (roborev 827). A check that disagrees with the query it serves
    certifies commits the audit will miss.
    """
    try:
        parsed = P.run(
            ["git", "interpret-trailers", "--parse"],
            input=message,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, P.SubprocessError):
        return None
    if parsed.returncode != 0:
        return None
    out = []
    for ln in parsed.stdout.splitlines():
        key, sep, value = ln.partition(":")
        if sep:
            out.append((key.strip(), value.strip()))
    return out


def check_item_trailer(
    message: str,
    keys: list[str],
    *,
    ids: Callable[[], set[str]],
    waivers: dict[str, list[str]] | None = None,
    merging: bool = False,
) -> tuple[int, str]:
    """Require one of `keys` as a trailer naming a queue item (`Item: P1.T3`) in the
    commit MESSAGE. `(exit, message)`: 0 passes, 1 refuses, 2 could not check.

    Enabled by ``[enforce].require_item_trailer``; the accepted keys are
    ``[enforce].item_trailer_keys`` (a project that has written `Phase: <id>` for months
    keeps writing it). The trailer is what lets an audit reconcile shipped commits
    against the queue with ``git log --format='%(trailers:key=Item,valueonly)'`` instead
    of parsing prose -- so its value must be the id of an item in the queue, from
    `ids()` (`queue_ids`), called at most once and only when a trailer names an item.
    Any non-empty value used to pass, and `Phase: NOPE.99` reached history where the
    audit could never match it (bug B438d336b24).

    A key in `waivers` (``[enforce].trailer_waivers``) marks a commit that ships NO
    item: it is accepted whether or not `keys` lists it, and takes only its declared
    words (`Phase-ships: none`). Every accepted
    trailer present must be valid: one invalid trailer is refused beside a valid one,
    because the audit reads them all. Keys match case-insensitively, as git's
    `%(trailers:key=...)` does.

    Called by the commit-msg hook with the message being committed -- never with
    `COMMIT_EDITMSG` from pre-commit, which is the previous commit's. A merge commit is
    exempt: it carries the trailers of the commits it merges. A `#Item:` comment line
    does not count, because its key is `#Item`; nor does one anywhere but the final
    paragraph, because git does not read it as a trailer there.
    """
    if merging:
        return 0, ""
    canon = {k.lower(): k for k in keys}
    words = {k.lower(): list(v) for k, v in (waivers or {}).items()}
    trailers = _trailers(message)
    if trailers is None:
        return 2, (
            "ddflow: `git interpret-trailers --parse` failed, so this commit's item trailer "
            "could not be checked. This is not a pass; check that `git` runs here."
        )
    # A key in `waivers` is accepted as well: the waiver map is what DECLARES a key that
    # marks a commit shipping no item, so `Phase-ships: none` satisfies the requirement
    # whether or not the key is also listed in item_trailer_keys. An earlier version
    # required both, and a waiver declared alone sat inert (cross-family reviewers,
    # three rounds).
    spelled = {k.lower(): k for k in (waivers or {})}
    accepted = [*keys, *(k for k in (waivers or {}) if k.lower() not in canon)] or ["Item"]
    checked = [
        (canon.get(k.lower()) or spelled[k.lower()], v)
        for k, v in trailers
        if k.lower() in canon or k.lower() in words
    ]
    if not checked:
        shown = " or ".join(
            f"`{k}: {'|'.join(words[k.lower()]) if k.lower() in words else '<id>'}`"
            for k in accepted
        )
        return 1, (
            f"ddflow: this commit has no {shown} trailer, and "
            f"[enforce].require_item_trailer is on.\n\n"
            f"Add a final line to the commit message, e.g.:\n"
            f"    {accepted[0]}: {words.get(accepted[0].lower(), ['P1.T3'])[0]}\n\n"
            f"It is what lets an audit match commits to queue items mechanically."
        )
    bad: list[str] = []
    known: set[str] | None = None
    unreadable = ""
    unknown = False
    for key, value in checked:
        allowed = words.get(key.lower())
        if allowed is not None:
            if value not in allowed:
                bad.append(
                    f"    {key}: {value}\n"
                    f"        `{key}` marks a commit that ships no item and takes only: "
                    f"{', '.join(allowed)}"
                )
            continue
        if not value:
            bad.append(f"    {key}:\n        empty -- it must name an item in the queue")
            continue
        if known is None and not unreadable:
            try:
                known = ids()
            except QueueUnreadable as exc:
                unreadable = str(exc) or type(exc).__name__
        if known is None:
            continue
        if value not in known:
            unknown = True
            near = _near_ids(value, known)
            bad.append(
                f"    {key}: {value}\n        no item in the queue has this id"
                + (f" -- did you mean {' or '.join(near)}?" if near else "")
            )
    if unreadable:
        why = (
            f"ddflow: could not read the queue, so the item id(s) in this commit's trailers "
            f"could not be checked: {unreadable}. This is not a pass; `ddflow doctor` "
            f"diagnoses the log."
        )
        if not bad:
            return 2, why
        bad.append("\n" + why)
    if not bad:
        return 0, ""
    return 1, (
        "ddflow: this commit carries an item trailer that is not valid, and "
        "[enforce].require_item_trailer is on:\n\n"
        + "\n".join(bad)
        + "\n\nThe trailer is what lets an audit match commits to queue items, so it must "
        "carry the id of a phase or task that has not been removed (`ddflow show <id>` "
        "checks one)."
        + (
            " A key that marks a commit shipping no item takes words instead of ids: "
            "declare them in [enforce].trailer_waivers."
            if unknown
            else ""
        )
        + "\nFix the trailer and commit again; to fix one on a commit already made, "
        "`git commit --amend`."
    )
