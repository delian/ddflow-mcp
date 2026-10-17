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
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config
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
_REVPARSE_LINES = 2
_MAX_DEPTH = 3  #: nested `sh -c` strings looked into
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


def _strip_heredocs(text: str) -> str:
    """The command line without heredoc bodies, which are data, not commands."""
    out: list[str] = []
    ends: list[str] = []
    for line in text.split("\n"):
        if ends:
            if line.strip() == ends[0]:
                ends.pop(0)
            continue
        out.append(line)
        parts = line.split("<<")[1:]
        for p in parts:
            w = p.lstrip("-").strip().split(" ")[0].strip("'\"")
            if w:
                ends.append(w)
    return "\n".join(out)


def _commands(text: str, depth: int = 0) -> list[list[str]]:
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
    if depth < _MAX_DEPTH:
        for c in list(cmds):
            body = _head(c)
            if body and body[0].rsplit("/", 1)[-1] in _SHELLS and "-c" in body:
                i = body.index("-c")
                if i + 1 < len(body):
                    cmds.extend(_commands(body[i + 1], depth + 1))
    return cmds


def _head(words: list[str]) -> list[str]:
    """`words` from the command's own word on: wrappers and VAR=x assignments dropped."""
    i = 0
    while i < len(words):
        w = words[i]
        if w in _WRAPPERS or (
            "=" in w and not w.startswith("-") and w.split("=")[0].isidentifier()
        ):
            i += 1
        elif w.startswith("-") and i and words[i - 1] in _WRAPPERS:
            i += 1  # a wrapper's own option (`sudo -u`, `nice -n`); its value is dropped below
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


def matches(entry: str, words: list[str]) -> bool:
    """Whether the command `words` is one the (primary-marker-free) `entry` describes."""
    want = entry.split()
    if not want:
        return False
    head = _head(words)
    if not head:
        return False
    if want[0].startswith("-"):
        return any(_word(want[0], w) for w in head) and _inorder(want[1:], head)
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
    return _inorder(rest, args)


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
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-dir", "--git-common-dir"],
            cwd=str(cwd) or None,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.split("\n")
        if len(out) < _REVPARSE_LINES:
            return False
        git_dir, common = out[0], out[1]
        return (Path(cwd) / git_dir).resolve() == (Path(cwd) / common).resolve()
    except (OSError, subprocess.SubprocessError):
        return False


def _lifted(env: dict[str, str]) -> tuple[bool, set[str]]:
    raw = env.get(ENV_ALLOW, "").strip()
    items = {s.strip() for s in raw.split(",") if s.strip()}
    return "all" in items, {" ".join(i.split()) for i in items}


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
    for words in _commands(command):
        if any(matches(a, words) for a in cfg.hooks.allowed_commands):
            continue
        for raw in entries:
            only_primary = raw.endswith(PRIMARY)
            entry = " ".join(raw.removesuffix(PRIMARY).split())
            if entry in lifted or not matches(entry, words):
                continue
            if only_primary:
                if primary is None:
                    primary = is_primary(cwd or Path.cwd())
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
