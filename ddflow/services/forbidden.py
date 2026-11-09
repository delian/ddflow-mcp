"""The commands an agent is told never to run, refused before they run (DX-forbidden-commands-hook).

Evidence 2026-10-17: three subagents each ran `pkill` once against a brief that said never.
A prompt is a request; a PreToolUse hook is the harness refusing. This module is the decision
only -- `check` says whether a Bash command line breaks `[hooks].forbidden_commands` -- and
`record` writes the refusal down; `api.setup` wires them to the hook.

Matching is by words, not substrings, so `git log --grep pkill` and `git commit -m "git push"`
are not hits:

* the command line is cut into the commands of its pipelines and `;`/`&&`/`||` lists (and the
  string after `sh -c`), heredoc bodies dropped;
* an entry's first word must be the command's own word, after `sudo`/`env`/`timeout N`/`VAR=x`
  wrappers (for `git`, its subcommand, after `-C dir` and the other global options); a
  `python -m MOD` command also counts as `MOD`;
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
    {
        "sudo", "env", "nohup", "time", "command", "exec", "nice", "xargs", "builtin", "doas",
        "timeout",
    }
)  # fmt: skip
_SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "|&", "(", ")", ";;"})
#: What may come right before a `#` that begins a comment: a blank, or an operator
#: (`)`, `(`, `<`, `>`, `;`, `&`, `|`) -- bash starts a comment after any of these, not
#: only after a blank. `{` and `}` are reserved words that need a blank, not operators:
#: a `#` glued to them is a literal `#` inside a word. `\r` is not a blank to the shell:
#: a `#` glued to it stays inside the word, so a `;` after it is a separator, not a comment
#: (B-forbidden-guard-multiline-quote-bypass critic #1). The one exception: a `)` that
#: closes a `$(` / `<(` / `>(` substitution is NOT a boundary (bash glues the `#` to the
#: substitution's value), decided in `_comment` against the recorded closing parens.
_COMMENT_BOUNDARY = " \t\n;|&()<>"
#: git's global options that take their value as the NEXT word.
_GIT_VALUED = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})

#: `git stash` is forbidden because the stash stack is shared by every worktree, but these
#: subcommands only READ it; the entry refuses the writing verbs alone (B-hook-blocks-read-only-stash-list).
_STASH_READONLY = frozenset({"list", "show"})

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
    '`ddflow review <item> --force --reason "<why>"` for an extra review round '
    "(--gate and the other options may sit anywhere, --reason is required, and a "
    "timeout or python -m launcher is fine)"
)
#: A `--force` on ddflow's own command is an override only the operator takes. The route that
#: needs no config edit: the operator runs the exact line, which the hook does not guard.
_OPERATOR_FORCE = (
    "report the exact command line to them; they run it from their own terminal (the hook "
    "guards agents' Bash only). For `complete` and `claim` there is a chat route that needs no "
    "--force: re-run with `--approval request`, the operator answers in chat, then `--approval <id>`"
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


def _strip_continuations(text: str) -> str:
    """`text` with a backslash-newline removed (a line continuation joins a `$` / backtick over
    the break, so it is still a command substitution). A single quote keeps its backslash; `'`
    inside a `"..."` is literal."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if _ansi_at(text, n, i):
            j = _ansi_c_end(text, n, i)
            out.append(text[i:j])
            i = j
        elif c == "'":
            j = text.find("'", i + 1)
            out.append(text[i:] if j < 0 else text[i : j + 1])
            i = n if j < 0 else j + 1
        elif c == '"':
            out.append('"')
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n and text[j + 1] == "\n":
                    j += 2  # a continuation inside the double quote: dropped
                elif text[j] == "\\":
                    out.append(text[j : j + 2])
                    j += 2
                else:  # includes a `'`, which is literal inside a double quote
                    out.append(text[j])
                    j += 1
            if j < n:
                out.append('"')
                j += 1
            i = j
        elif c == "\\" and i + 1 < n and text[i + 1] == "\n":
            i += 2
        elif c == "\\" and i + 1 < n:  # an escape: the quoted char is not a quote opener
            out.append(text[i : i + 2])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _ansi_c_end(text: str, n: int, k: int) -> int:
    """Index after the `$'...'` that opened at `k`; a `\\'` inside is a literal quote, not the
    close, so `_find_body` must not take it for one (bash ANSI-C quoting)."""
    i = k + 2
    while i < n:
        if text[i] == "\\" and i + 1 < n:
            i += 2
        elif text[i] == "'":
            return i + 1
        else:
            i += 1
    return n


def _ansi_at(text: str, n: int, k: int) -> bool:
    """Does `text[k]` open a `$'...'` (bash ANSI-C quote)?"""
    return text[k] == "$" and k + 1 < n and text[k + 1] == "'"


def _sub_open_at(text: str, n: int, k: int) -> bool:
    """Does `text[k]` open a `$(` / `<(` / `>(` substitution or a `$'...'` quote?"""
    return (text[k] in "$<>" and k + 1 < n and text[k + 1] == "(") or _ansi_at(text, n, k)


def _bare_word(text: str, n: int, k: int, word: str) -> bool:
    """`text[k:]` starts the bare keyword `word` -- a word boundary on each side."""
    if not text.startswith(word, k):
        return False
    if k > 0 and text[k - 1] not in " \t\r\n;|&(":
        return False
    end = k + len(word)
    return end >= n or not (text[end].isalnum() or text[end] == "_")


def _case_end(text: str, n: int, k: int, bodies: list[str], sub_close: set[int]) -> int:
    """Index after the `esac` that closes the `case` at `k`; a quoted, commented or escaped
    span is text, so an `esac` inside it is not the keyword that ends the case (a double
    quote, `$(` body or backtick body a case arm holds is skipped too, so an `esac` a quote
    swallows does not drop the depth -- B-forbidden-guard-multiline-quote-bypass critic #2)."""
    depth = 1
    k += 4
    while k < n and depth:
        if text[k] == "#" and _comment(text, k, -1, sub_close):
            k2 = text.find("\n", k)
            k = k2 if k2 >= 0 else n
        elif text[k] == "'":
            k2 = text.find("'", k + 1)
            k = k + 1 if k2 < 0 else k2 + 1
        elif _ansi_at(text, n, k):
            k = _ansi_c_end(text, n, k)
        elif text[k] == '"':
            k = _dquote_end(text, n, bodies, sub_close, k)
        elif text[k] == "`" or (text[k] in "$<>" and k + 1 < n and text[k + 1] == "("):
            k = _sub_body(text, n, bodies, sub_close, k)
        elif text[k] == "\\":
            k += 2 if k + 1 < n else 1
        elif _bare_word(text, n, k, "case"):
            depth += 1
            k += 4
        elif _bare_word(text, n, k, "esac"):
            depth -= 1
            k += 4
        else:
            k += 1
    return k


def _comment(text: str, k: int, j: int, sub_close: set[int]) -> bool:
    """`text[k]` is the `#` of a comment that starts right there: the body that opened at `j`
    begins with it, or the char before it is in `_COMMENT_BOUNDARY` -- but a `)` that closed a
    `$(` / `<(` / `>(` substitution is NOT one: bash keeps the `#` literal, glued to the
    substitution's value, so a `;` after it still separates a command
    (B-forbidden-guard-multiline-quote-bypass residual: `echo $(echo hi)#; git push` runs
    `git push`)."""
    if text[k] != "#":
        return False
    if k == j:
        return True
    prev = text[k - 1]
    if prev == ")":
        return k - 1 not in sub_close
    return prev in _COMMENT_BOUNDARY


def _has_esac(text: str, n: int, k: int) -> bool:
    """A `case` at `k` opens a case STATEMENT, whose `)` separators are pattern terminators,
    only if a bare `esac` closes it somewhere ahead; a bare `case` WORD (`echo case`) has none
    and is just a word (B-forbidden-guard-multiline-quote-bypass bare-case)."""
    j = text.find("esac", k + 4)
    while j >= 0:
        if _bare_word(text, n, j, "esac"):
            return True
        j = text.find("esac", j + 1)
    return False


def _param_end(text: str, n: int, bodies: list[str], sub_close: set[int], k: int) -> int:
    """Index just past the `}` that closes the `${` parameter expansion at `k`. The braces nest
    (``${x:-${y}}``), and a `}` / `)` inside a quoted span, an escape or a `$(` / backtick body
    in between is part of the expansion, never the closing `}` -- so a `)` inside the expansion
    is not the close of an enclosing `$(` body, and a `$(` a ``${x:-...}`` holds still records
    its body (B-forbidden-guard-multiline-quote-bypass critic #2)."""
    depth = 1
    i = k + 2
    while i < n and depth:
        if text[i] == "$" and i + 1 < n and text[i + 1] == "{":
            depth += 1
            i += 2
        elif text[i] == "}":
            depth -= 1
            i += 1
        elif text[i] == "\\" and i + 1 < n:
            i += 2
        elif text[i] == "'":
            j = text.find("'", i + 1)
            i = i + 1 if j < 0 else j + 1
        elif text[i] == '"':
            i = _dquote_end(text, n, bodies, sub_close, i)
        elif text[i] == "`" or text[i : i + 2] in ("$(", "<(", ">("):
            i = _sub_body(text, n, bodies, sub_close, i)
        else:
            i += 1
    return i


def _find_body(
    text: str,
    n: int,
    bodies: list[str],
    sub_close: set[int],
    j: int,
    closer: str,
    case_ok: bool = True,
) -> int:
    """The index of `closer` for the `$(` / backtick opened before `j`; nested quotes,
    substitutions and a nested `( ... )` subshell inside are navigated (so the first plain `)`
    is not taken for this body's close) but mark no newline. An escaped closer is not the end.
    ``case_ok`` is False inside a `$((` arithmetic body, whose `case`/`esac` are arithmetic
    tokens, not a shell case statement (B-forbidden-guard-multiline-quote-bypass critic: a
    post-arithmetic `esac` must not swallow a later command)."""
    k = j
    while k < n:
        if text[k] == closer:
            return k
        k = _skip_span(text, n, bodies, sub_close, k, j, closer, case_ok)
    return k


def _skip_span(
    text: str,
    n: int,
    bodies: list[str],
    sub_close: set[int],
    k: int,
    j: int,
    closer: str,
    case_ok: bool = True,
) -> int:
    """Index after the span that starts at `k` when it is data, not `closer`: a comment (its
    `)` is text), a nested `( ... )` subshell, a `case` statement, a quote, an escape, a
    `${...}` parameter expansion, or a nested substitution. A plain char is not a span (one
    past it)."""
    if _comment(text, k, j, sub_close):
        k2 = text.find("\n", k)
        return k2 if k2 >= 0 else n
    c = text[k]
    if closer == ")" and c == "(":
        # a nested ( ... ) subshell: its plain `)` (and a `#` after it) is not this body's
        # close, so skip to one past it (groq critic: `echo $( (echo hi) )#; git push`).
        return _find_body(text, n, bodies, sub_close, k + 1, ")", case_ok) + 1
    if case_ok and closer == ")" and _bare_word(text, n, k, "case") and _has_esac(text, n, k):
        return _case_end(
            text, n, k, bodies, sub_close
        )  # a case's `)` separators end patterns, not this `$(` body
    if c == "'":
        k2 = text.find("'", k + 1)
        return k + 1 if k2 < 0 else k2 + 1
    if _ansi_at(text, n, k):
        return _ansi_c_end(text, n, k)
    if c == '"':
        return _dquote_end(text, n, bodies, sub_close, k)
    if c == "\\":  # an escaped closer (\) / \`) is not the end of the body
        return k + 2 if k + 1 < n else k + 1
    if text[k : k + 2] == "${":
        return _param_end(
            text, n, bodies, sub_close, k
        )  # a `)` inside ${...} is not this body's close
    if c == "`" or text[k : k + 2] in ("$(", "<(", ">("):
        return _sub_body(text, n, bodies, sub_close, k)
    return k + 1


def _dquote_end(text: str, n: int, bodies: list[str], sub_close: set[int], j: int) -> int:
    """The index after the `"` that opened at `j`, recursing into the `$(` / backtick bodies a
    double-quoted span holds. `<(` / `>(` are NOT special inside double quotes: bash prints
    them literally and runs no command, so they stay text
    (B-forbidden-guard-multiline-quote-bypass critic #5, dquote process substitution)."""
    k = j + 1
    while k < n:
        cc = text[k]
        if cc == '"':
            return k + 1
        if cc == "\\":
            k += 2 if k + 1 < n else 1
        elif cc == "`" or (cc == "$" and k + 1 < n and text[k + 1] == "("):
            k = _sub_body(text, n, bodies, sub_close, k)
        else:
            k += 1
    return k


def _sub_body(text: str, n: int, bodies: list[str], sub_close: set[int], k: int) -> int:
    """Index just past the substitution that opened at `k`: a backtick (closer `` ` ``), a `$(` or
    a `<(` / `>(` process substitution (closer `)`); its body is recorded (a nested
    substitution's commands run as commands). A `$((` arithmetic expansion runs no commands,
    but its closing `))` still keeps a following `#` literal, so its OUTER `)` is recorded."""
    is_arith = text[k] == "$" and k + 2 < n and text[k + 1] == "(" and text[k + 2] == "("
    closer = "`" if text[k] == "`" else ")"
    start = k + 3 if is_arith else (k + 2 if text[k] != "`" else k + 1)
    k2 = _find_body(text, n, bodies, sub_close, start, closer, case_ok=not is_arith)
    if is_arith:
        sub_close.add(k2 + 1)  # the outer `)` of `$((...))`: bash glues a following `#` to it
        return k2 + 2
    bodies.append(text[start:k2])
    if closer == ")":
        sub_close.add(k2)
    return k2 + 1


def _scan(text: str) -> tuple[list[str], list[str]]:
    """Split `text` at the newlines that ARE command separators, and note the interiors of the
    `$(` ... `)` / backquoted command substitutions (whose commands run as commands). A newline
    is data only inside a quote's literal body; a single-quoted `$(` or backtick is data. A
    comment (`#` to end of line) is data too: its characters are dropped from the lines, so a
    `;` or reserved word inside one is not a command (a `` `# ... ; git push` `` body is a
    comment, not a command -- critic #1, backtick comment)."""
    n = len(text)
    cut = [False] * n
    drop = [False] * n
    bodies: list[str] = []
    sub_close: set[int] = set()

    i = 0
    while i < n:
        c = text[i]
        if c == "\n":
            cut[i] = True
            i += 1
        elif c == "#" and _comment(text, i, 0, sub_close):
            k2 = text.find("\n", i)  # a comment: its quotes are data to the end of the line
            e = k2 if k2 >= 0 else n
            drop[i:e] = [True] * (e - i)
            i = e
        elif c == "'":
            k2 = text.find("'", i + 1)
            i = i + 1 if k2 < 0 else k2 + 1
        elif c == '"':
            i = _dquote_end(text, n, bodies, sub_close, i)
        elif c == "\\":
            i += 2 if i + 1 < n else 1
        elif c == "`":
            k2 = _find_body(text, n, bodies, sub_close, i + 1, "`")
            bodies.append(text[i + 1 : k2])
            drop[i : min(k2 + 1, n)] = [True] * (min(k2 + 1, n) - i)
            i = k2 + 1
        elif _sub_open_at(text, n, i):
            if _ansi_at(text, n, i):
                i = _ansi_c_end(text, n, i)  # `$'...'` is data, kept inline
            else:
                end = min(_sub_body(text, n, bodies, sub_close, i), n)
                # an unclosed `$(` / `<(` / `>(` does not grow the line masks
                drop[i:end] = [True] * (end - i)  # the body is captured in `bodies`, not inline
                i = end
        else:
            i += 1

    lines: list[str] = []
    cur: list[str] = []
    for i, ch in enumerate(text):
        if ch == "\n" and cut[i]:
            lines.append("".join(cur))
            cur = []
        elif not drop[i]:
            cur.append(ch)
    if cur:
        lines.append("".join(cur))
    return lines, bodies


def _logical_lines(text: str) -> list[str]:
    """The command-position spans of `text`: `_scan` at its newlines, continuations stripped."""
    return _scan(_strip_continuations(text))[0]


def _substitutions(text: str) -> list[str]:
    """The interiors of the `$(` ... `)` / backquoted command substitutions in `text`."""
    return _scan(_strip_continuations(text))[1]


def _split(text: str) -> list[list[str]]:
    """The commands of `text` as their words (no `sh -c` string looked into)."""
    cmds: list[list[str]] = []
    for line in _logical_lines(_strip_heredocs(text)):
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
    return cmds


def _commands(text: str) -> list[list[str]]:
    """Each command of `text` as its words, of the strings given to `sh -c`, and of the
    `$(` ... `)` / backquoted command substitutions. The descent runs until no new command
    string is left, keyed by the string content so a chain is descended once through whatever
    mix of `sh -c` payloads and substitution bodies holds it (no fixed level cap to skip one
    past -- B-forbidden-guard-multiline-quote-bypass 16-iteration cap, critic #3)."""
    cmds = _split(text)
    todo = cmds
    bodies = _substitutions(text)
    seen: set[str] = set()
    while todo or bodies:
        found: list[list[str]] = []
        new_bodies: list[str] = []
        for c in todo:
            head = _head(c)
            if head and head[0].rsplit("/", 1)[-1] in _SHELLS:
                i = _sh_c(head) if len(head) > 1 else -1
                if i >= 0:
                    s = head[i]  # _sh_c returns the command-string word's index directly
                    if s not in seen:
                        seen.add(s)
                        found.extend(_split(s))
                        new_bodies.extend(_substitutions(s))
        for b in bodies:
            if b not in seen:
                seen.add(b)
                found.extend(_split(b))
                new_bodies.extend(_substitutions(b))
        cmds.extend(found)
        todo = found
        bodies = new_bodies
    return cmds


#: Bash long options that take their value as the next word (`bash --rcfile FILE -c 'x'`).
_VALUED_LONG = frozenset({"--rcfile", "--init-file"})


def _sh_c(head: list[str]) -> int:
    """The index of the word bash runs as the `-c` command string, or -1. Bash reads its
    option words first (`-x`, `+x`, `-o NAME`, `--rcfile FILE`), and `-c` makes the FIRST
    non-option argument the command string; option words AFTER `-c` do not stop it, so
    `bash -c -x 'git push'`, `bash -c -- 'git push'`, `bash -c - 'git push'` and
    `bash -co pipefail 'git push'` all RUN `git push` (B-forbidden-guard-multiline-quote-bypass
    critic #6). A `c` inside a cluster is `-c` (`sh -xc 'pkill'`); a `+c` reads the next
    non-option as the run string too (bash treats `-c` and `+c` alike); an optname that CLOSES a
    cluster (`bash -xo pipefail -c 'pkill'`) and a valued long option (`--rcfile FILE`) consume
    their value; the LAST `-c`/`+c` before the operand wins (`bash -co pipefail -c 'pkill'`
    runs the `-c`). Returns the command-string word's index, or -1 (no `-c` still set)."""
    c_set = False
    i = 1
    while i < len(head):
        w = head[i]
        if w in {"--", "-"}:
            # `--` and a lone `-` end the option words: the command string is the NEXT word.
            return i + 1 if c_set and i + 1 < len(head) else -1
        if not w.startswith("-") and not w.startswith("+"):
            return i if c_set else -1  # the first non-option: the command string, or a script
        if w.startswith("--"):
            i += 2 if w in _VALUED_LONG else 1  # a valued long option consumes its word
            continue
        body = w[1:]
        j = 0
        while j < len(body):
            ch = body[j]
            if ch == "c":
                c_set = True  # `-c`/`+c` both read the next non-option as the run string
            if ch in "oO":
                # `-o`/`-O`/`+o`/`+O` optname takes the rest of THIS token, or the NEXT word
                # when `o`/`O` CLOSES it (`-xo pipefail`): both are the option's value.
                if j == len(body) - 1:
                    i += 1
                break
            j += 1
        i += 1
    return -1


#: Each wrapper's options that take their value as the next word (`sudo -u root`, `nice -n 5`);
#: its other options (`sudo -n`, `time -p`, `xargs -t`) stand alone.
_VALUED = {
    "sudo": frozenset({"-u", "-g", "-C", "-p", "-h", "-r", "-t", "-U", "-D", "-R", "-T"}),
    "doas": frozenset({"-u", "-C"}),
    "nice": frozenset({"-n"}),
    "env": frozenset({"-u", "-C", "-S"}),
    "xargs": frozenset({"-n", "-I", "-L", "-P", "-s", "-a", "-d", "-E", "-l", "-i"}),
    "timeout": frozenset({"-k", "-s", "--kill-after", "--signal"}),
}
_PYTHON = re.compile(r"(?:python|pypy)\d*(?:\.\d+)*[a-z]?\Z")
_DURATION = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)[smhd]?")


def _head(words: list[str]) -> list[str]:
    """`words` from the command's own word on: wrappers, their options and values, and
    VAR=x assignments dropped (`timeout 5 cmd` runs `cmd`)."""
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
        elif wrapper == "timeout" and _DURATION.fullmatch(w):
            i += 1  # the duration is the wrapper's own word...
            wrapper = "timeout-run"  # ...and only the one: the next word is the command
        else:
            break
    return words[i:]


def _python_module(head: list[str]) -> list[str]:
    """`python [-P ...] -m mod args` as `mod args`: the module is the command that runs."""
    if not head or not _PYTHON.match(head[0].rsplit("/", 1)[-1]):
        return head
    i = 1
    while i < len(head) and head[i].startswith("-") and head[i] != "-m":
        if head[i] in ("-c", "-", "--"):
            return head  # an inline program, stdin or a script after `--`, not a module
        if head[i].startswith("-m"):
            return [head[i][2:], *head[i + 1 :]]  # `-mddflow`
        i += 2 if head[i] in ("-W", "-X") else 1
    return head[i + 1 :] if i + 1 < len(head) and head[i] == "-m" else head


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
    """Whether the command `words` is one the (primary-marker-free) `entry` describes. A
    `python -m MOD args` command is both itself and `MOD args`: an entry may name either
    (`python -m http.server` keeps matching; `ddflow review ...` is matched when launched as
    `python -m ddflow review ...`)."""
    want = entry.split()
    if not want:
        return False
    head = _head(words)
    if not head:
        return False
    heads = [head, _python_module(head)]
    return any(_matches_head(want, h, ordered) for h in heads if h)


def _git_subcommand_matches(want: list[str], args: list[str]) -> bool:
    """Does the `git` entry `want` (its words after `git`) name what `args` (the command's
    words after `git` and its global options) runs? The subcommand is the first word; the
    GENERIC `git stash` entry matches only stash's WRITING verbs, so the read-only
    `list`/`show` do not (B-hook-blocks-read-only-stash-list). An entry that itself names
    `stash list`/`stash show` still forbids them: only `want == ["stash"]` is exempt."""
    if not want or want[0].startswith("-") or not args or args[0] != want[0]:
        return False
    if len(want) == 1 and want[0] == "stash" and args[1:2] and args[1] in _STASH_READONLY:
        return False
    return True


def _matches_head(want: list[str], head: list[str], ordered: bool) -> bool:
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
        if not _git_subcommand_matches(rest, args):
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
        head = _head(c)
        lead = c[: len(c) - len(head)]  # the assignments in front of the command word
        if any(w.startswith(("GIT_DIR=", "GIT_WORK_TREE=")) for w in lead):
            return True
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


def _runs_ddflow(words: list[str]) -> bool:
    """Whether the command is ddflow itself, launched directly or as `python -m ddflow`."""
    head = _head(words)
    return any(h and h[0].rsplit("/", 1)[-1] == "ddflow" for h in (head, _python_module(head)))


def _instead(entry: str, words: list[str] | None = None) -> str:
    if entry.startswith("-"):
        return (
            f"{_FLAG_INSTEAD}. If the operator authorised this override: {_OPERATOR_FORCE}"
            if words and _runs_ddflow(words)
            else _FLAG_INSTEAD
        )
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
            return Refusal(entry, " ".join(words)[:200], _instead(entry, words))
    return None


def record(log: EventLog, cfg: Config, refusal: Refusal, session_id: str, harness: str) -> None:
    """The refusal as a session note, so `ddflow report` can list it. Best effort."""
    sid = S.harness_session_id(session_id) or S.resolve(log)[0]
    if not any(e.kind == "session.started" and e.subject == sid for e in log.read_all()):
        log.append("session.started", sid, {"tool": "hook", "cwd": str(Path.cwd())})
    text, _ = S.redact(f"Refused `{refusal.command}` (forbidden: {refusal.entry})", cfg)
    data: dict[str, Any] = {"text": text, "item": "", "source": f"{SOURCE}:{harness}"}
    log.append("session.note", sid, data)
