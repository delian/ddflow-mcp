"""The commands an agent is told never to run, refused before they run (DX-forbidden-commands-hook).

Evidence 2026-10-17: three subagents each ran `pkill` once against a brief that said never.
A prompt is a request; a PreToolUse hook is the harness refusing. This module is the decision
only -- `check` says whether a Bash command line breaks `[hooks].forbidden_commands` -- and
`record` writes the refusal down; `api.setup` wires them to the hook.

Matching is by words, not substrings, so `git log --grep pkill` and `git commit -m "git push"`
are not hits:

* the command line is cut into the commands of its pipelines and `;`/`&&`/`||` lists (and the
  string after `sh -c`), heredoc bodies dropped;
* an entry's first word must be the command's own word, after `sudo`/`env`/`VAR=x` wrappers
  (for `git`, its subcommand, after `-C dir` and the other global options);
* the rest of the entry's words are matched in order among the command's arguments, a flag
  anywhere (`-rf` matches `-fr`; `--force` matches `--force=1`), a path by its tail.
An entry that starts with `-` is a flag and matches in any command.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config
from ..infra import git
from ..infra.log import EventLog
from . import sessions as S

SOURCE = "forbidden-command"
#: `DDFLOW_HOOKS_ALLOW=pkill,git stash` lifts those entries for one session; `all` lifts all.
ENV_ALLOW = "DDFLOW_HOOKS_ALLOW"
PRIMARY = "@primary"

_WRAPPERS = frozenset(
    {"sudo", "env", "nohup", "time", "command", "exec", "nice", "xargs", "builtin", "doas"}
)
_SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "|&", "(", ")", ";;"})
#: git's global options that take their value as the NEXT word.
_GIT_VALUED = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})

#: What to do instead, by the entry's first words.
_INSTEAD = {
    "pkill": "kill only a process you started, by its pid (`kill <pid>`), or stop your own background job",
    "killall": "kill only a process you started, by its pid (`kill <pid>`), or stop your own background job",
    "git stash": "the stash stack is shared by every worktree: set work aside in a temporary WIP commit",
    "git push": "do not push: `ddflow merge` lands the work, and the operator pushes",
    "git reset": "never reset the primary checkout: work in your item's worktree, or make a WIP commit",
    "rm": "`.ddflow/` holds the event log, the source of truth: change it through ddflow commands",
}
_FLAG_INSTEAD = (
    "no force flags: fix what the tool refused. The one exception is "
    '`ddflow review <item> --force --reason "<why>"` for an extra review round'
)


@dataclass(frozen=True)
class Refusal:
    entry: str  #: the forbidden_commands entry that matched
    command: str  #: the command (one pipeline element) that matched it
    instead: str  #: the safe alternative

    def message(self) -> str:
        return (
            f"ddflow refused `{self.command}`: it matches [hooks].forbidden_commands "
            f"`{self.entry}`. Instead: {self.instead}. "
            "(The operator can lift it: DDFLOW_HOOKS_ALLOW in the session, or the config list.)"
        )


_HEREDOC = re.compile(r"(?<![<\d(])<<-?\s*['\"]?([^\s;|&<>()'\"]+)")


def _strip_heredocs(text: str) -> str:
    """The command line without heredoc bodies, which are data, not commands. A `<<` that
    opens no body (a here-string, a shift, a quoted `a<<b`) drops nothing: only a terminator
    line that really follows makes it a heredoc."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for m in _HEREDOC.finditer(line):
            end = next((j for j in range(i, len(lines)) if lines[j].strip() == m.group(1)), None)
            if end is not None:
                i = end + 1
                break
    return "\n".join(out)


def _commands(text: str, *, nested: bool = False) -> list[list[str]]:
    """Each command of `text` as its words."""
    cmds: list[list[str]] = []
    for line in _strip_heredocs(text).split("\n"):
        lex = shlex.shlex(line, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        try:
            words = list(lex)
        except ValueError:  # an unterminated quote: judge the words as written
            words = line.split()
        cur: list[str] = []
        for w in [*words, ";"]:
            if w in _SEPARATORS or (w and set(w) <= set(";&|()")):
                if cur:
                    cmds.append(cur)
                cur = []
            else:
                cur.append(w)
    todo = cmds
    for _ in range(3):  # three nested `sh -c` levels are looked into
        found: list[list[str]] = []
        for c in todo:
            body = _head(c)
            if body and body[0].rsplit("/", 1)[-1] in _SHELLS and "-c" in body:
                i = body.index("-c")
                if i + 1 < len(body):
                    found.extend(_commands(body[i + 1], nested=True))
        cmds.extend(found)
        todo = found
    return cmds


#: Each wrapper's options that take their value as the next word (`sudo -u root`, `nice -n 5`);
#: its other options (`sudo -n`, `time -p`, `xargs -t`) stand alone.
_VALUED = {
    "sudo": frozenset({"-u", "-g", "-C", "-p", "-h", "-r", "-t", "-U", "-D", "-R", "-T"}),
    "doas": frozenset({"-u", "-C"}),
    "nice": frozenset({"-n"}),
    "env": frozenset({"-u", "-C", "-S"}),
    "xargs": frozenset({"-n", "-I", "-L", "-P", "-s", "-a", "-d", "-E", "-l", "-i"}),
}


def _head(words: list[str]) -> list[str]:
    """`words` from the command's own word on: wrappers, their options and values, and
    VAR=x assignments dropped."""
    i = 0
    wrapper = ""
    while i < len(words):
        w = words[i]
        if w in _WRAPPERS:
            wrapper = w
            i += 1
        elif "=" in w and not w.startswith("-") and w.split("=")[0].isidentifier():
            i += 1
        elif wrapper and w.startswith("-"):
            i += 2 if w in _VALUED.get(wrapper, ()) else 1
        else:
            break
    return words[i:]


def _args(head: list[str]) -> list[str]:
    """The words after the command word; for git, after its subcommand too (kept as word 0)."""
    if not head:
        return []
    cmd = head[0].rsplit("/", 1)[-1]
    rest = head[1:]
    if cmd != "git":
        return rest
    i = 0
    while i < len(rest) and rest[i].startswith("-"):
        i += 2 if rest[i] in _GIT_VALUED else 1
    return rest[i:]


def _word(entry: str, seen: str) -> bool:
    if entry.startswith("--"):
        return seen == entry or seen.startswith(entry + "=")
    if entry.startswith("-"):
        return (
            seen.startswith("-") and not seen.startswith("--") and set(entry[1:]) <= set(seen[1:])
        )
    # a path: the entry as a whole run of its components (`.ddflow` in `./.ddflow/events`)
    return entry == seen or f"/{entry}/" in f"/{seen.strip('/')}/"


def matches(entry: str, words: list[str], *, ordered: bool = True) -> bool:
    """Whether the command `words` is one the (primary-marker-free) `entry` describes."""
    want = entry.split()
    if not want:
        return False
    head = _head(words)
    if not head:
        return False
    if want[0].startswith("-"):
        return any(_word(want[0], w) for w in head) and (
            _inorder(want[1:], head)
            if ordered
            else all(any(_word(x, a) for a in head) for x in want[1:])
        )
    cmd = head[0].rsplit("/", 1)[-1]
    if cmd != want[0]:
        return False
    args = _args(head)
    rest = want[1:]
    if cmd == "git" and rest and not rest[0].startswith("-"):
        # git's subcommand is its first word after the global options, not any argument
        if not args or args[0] != rest[0]:
            return False
        args, rest = args[1:], rest[1:]
    return _inorder(rest, args) if ordered else all(any(_word(w, a) for a in args) for w in rest)


def _inorder(want: list[str], seen: list[str]) -> bool:
    i = 0
    for w in want:
        while i < len(seen) and not _word(w, seen[i]):
            i += 1
        if i == len(seen):
            return False
        i += 1
    return True


def is_primary(cwd: str | Path) -> bool:
    """Whether `cwd` is in the primary checkout (not an item's worktree)."""
    r = git.run(cwd or Path.cwd(), "rev-parse", "--git-dir", "--git-common-dir")
    try:
        git_dir, common = r.out.split("\n")[:2]
    except ValueError:
        return False
    if not r.ok:
        return False
    return (Path(cwd) / git_dir).resolve() == (Path(cwd) / common).resolve()


def _elsewhere(cmds: list[list[str]]) -> bool:
    """Whether the command line may act on another checkout than the cwd's (`git -C dir`,
    `--git-dir=...`, `GIT_DIR=... git`, `cd dir && ...`): then the cwd cannot say which one,
    so it is judged as the primary. Only git's own options count, not `make -C`."""
    for c in cmds:
        if c[0] in ("cd", "pushd"):
            return True
        if any(w.startswith(("GIT_DIR=", "GIT_WORK_TREE=")) for w in c):
            return True
        head = _head(c)
        if head and head[0].rsplit("/", 1)[-1] == "git":
            for w in head[1:]:
                if not w.startswith("-"):
                    break  # the subcommand: git's own options are over
                if w.startswith(("-C", "--git-dir", "--work-tree")):
                    return True
    return False


def _lifted(env: dict[str, str]) -> tuple[bool, set[str]]:
    raw = env.get(ENV_ALLOW, "").strip()
    items = {s.strip() for s in raw.split(",") if s.strip()}
    return "all" in items, {" ".join(i.removesuffix(PRIMARY).split()) for i in items}


def _instead(entry: str) -> str:
    if entry.startswith("-"):
        return _FLAG_INSTEAD
    words = entry.split()
    return (
        _INSTEAD.get(" ".join(words[:2]))
        or _INSTEAD.get(words[0])
        or "ask the operator, who can lift it"
    )


def check(
    command: str,
    cfg: Config,
    *,
    cwd: str | Path = "",
    env: dict[str, str] | None = None,
) -> Refusal | None:
    """The first forbidden command in `command`, or None. `cwd` decides `@primary` entries."""
    entries = [e for e in cfg.hooks.forbidden_commands if e.strip()]
    if not entries or not command.strip():
        return None
    everything, lifted = _lifted(os.environ if env is None else env)
    if everything:
        return None
    primary: bool | None = None
    cmds = _commands(command)
    for words in cmds:
        if any(matches(a, words, ordered=False) for a in cfg.hooks.allowed_commands):
            continue
        for raw in entries:
            only_primary = raw.endswith(PRIMARY)
            entry = " ".join(raw.removesuffix(PRIMARY).split())
            if entry in lifted or not matches(entry, words):
                continue
            if only_primary:
                if primary is None:
                    primary = _elsewhere(cmds) or is_primary(cwd or Path.cwd())
                if not primary:
                    continue
            return Refusal(entry, " ".join(words)[:200], _instead(entry))
    return None


def record(log: EventLog, cfg: Config, refusal: Refusal, session_id: str, harness: str) -> None:
    """The refusal as a session note, so `ddflow report` can list it. Best effort."""
    sid = S.harness_session_id(session_id) or S.resolve(log)[0]
    if not any(e.kind == "session.started" and e.subject == sid for e in log.read_all()):
        log.append("session.started", sid, {"tool": "hook", "cwd": str(Path.cwd())})
    text, _ = S.redact(f"Refused `{refusal.command}` (forbidden: {refusal.entry})", cfg)
    data: dict[str, Any] = {"text": text, "item": "", "source": f"{SOURCE}:{harness}"}
    log.append("session.note", sid, data)
