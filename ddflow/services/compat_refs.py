"""Stale references: the ddflow commands and tools a project's own files name (D-compat).

A rename keeps the old name working until 1.0 (an alias), but files that name it keep
saying the old thing: a settings hook, a git hook, a pre-commit entry, a driver doc, an
AGENTS.md or CLAUDE.md block, a slash command, an ejected prompt, a macro. This module
finds every `ddflow <command>` and `ddflow_<tool>` such a file names and resolves it
against a `Vocabulary` -- the commands and tools this ddflow has, with the old names it
still answers to:

* ``ok``         -- a current name;
* ``deprecated`` -- an alias, with the name that replaces it;
* ``unknown``    -- neither (a typo, or a name from a ddflow that is gone);
* ``unchecked``  -- the process holds no table to judge it by (an MCP server has no command
  parser): said in one note, never read as ``ok``.

The vocabulary is handed IN: the command table and the tool registry belong to the
surfaces, and a service must not import them (``surfaces.vocabulary`` builds it).

A reference is **managed** when it sits inside a region ddflow wrote and a person has not
edited (`infra.fsio.Managed`): `rewrite` replaces the deprecated ones there, re-stamps the
region and backs the file up first. Anywhere else it is the project's own text, which is
never edited silently: `Finding.proposal` says what the change would be.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.fsio import Managed, NewerContent, RegionError, replace_text
from .backups import make_backup
from .enforce import HOOK_MARKER, hooks_dir

OK, DEPRECATED, UNKNOWN, UNCHECKED = "ok", "deprecated", "unknown", "unchecked"
#: The longest command path: `group command`.
MAX_WORDS = 2
#: Words that look like a tool name and are not one (the distribution's module).
NOT_TOOLS = frozenset({"ddflow_mcp"})

#: Global options that take a value, so the word after them is not the command.
_VALUE_FLAGS = frozenset({"--agent", "--repo", "--reason"})
_WORD = re.compile(r"[a-z][a-z0-9-]*")
_CLI = re.compile(r"(?<![\w.$-])(?:[\w./~-]*/)?ddflow[\"']?(?=[ \t])")
#: What may stand before the `ddflow` of a command: a line start, a shell separator or
#: keyword, a YAML key or list dash, `python -m`, a launcher's variable assignments.
_POSITION = re.compile(
    r"(?:^|[;&|(`:$\"'-]|(?<!\w)(?:then|else|do|exec|run|sudo|xargs|command|-m))\s*(?:\w+=\S*\s+)*$"
)
_TOOL = re.compile(r"(?<![\w./-])ddflow_[a-z][a-z0-9_]*(?![\w*]|\.\w)")
#: A shell word: a quoted string is one, so an option's value with spaces is skipped whole.
_TOKEN = re.compile(r"\"[^\"]*\"|'[^']*'|\S+")
#: A shell word ends the command at one of these.
_STOP = re.compile(r"[;&|)`\"'<>]")
_FENCE = re.compile(r"^\s*(```|~~~)")
_BACKTICK = re.compile(r"`([^`\n]+)`")
_BEGIN = re.compile(r"^(?P<open>\s*(?:<!--|#))\s*ddflow:begin (?P<name>\S+)")
_END = re.compile(r"^\s*(?:<!--|#)\s*ddflow:end (?P<name>\S+)")
_LEGACY_BEGIN = "DDFLOW:BEGIN"
_LEGACY_END = "DDFLOW:END"


@dataclass(frozen=True)
class Renamed:
    """An old name that still works, and what replaces it."""

    new: str
    since: str = ""
    removed_in: str = ""


@dataclass(frozen=True)
class Vocabulary:
    """The names this ddflow answers to. ``commands`` are full canonical paths (one or two
    words); an alias is keyed by the OLD path (a renamed group by its one word, a renamed
    subcommand by its group, written either way, and the old word) and its ``new`` is the
    one word or group that replaces it."""

    commands: frozenset[tuple[str, ...]]
    tools: frozenset[str]
    command_aliases: Mapping[tuple[str, ...], Renamed] = field(default_factory=dict)
    tool_aliases: Mapping[str, Renamed] = field(default_factory=dict)
    #: A vocabulary built from the tool table alone (an MCP server) cannot judge a command
    #: word, nor one built from the parser alone a tool name: that half is not checked.
    #: With the half unchecked, its references come back `unchecked`, not `ok`: a report must
    #: never read as an all-clear for names nobody looked at.
    check_commands: bool = True
    check_tools: bool = True

    @property
    def groups(self) -> frozenset[str]:
        return frozenset(p[0] for p in self.commands if len(p) > 1)

    def takes_subcommand(self, word: str) -> bool:
        """Is ``word`` a command group (or an old name of one)?"""
        if word in self.groups:
            return True
        old = self.command_aliases.get((word,))
        return old is not None and old.new in self.groups

    def resolve_command(self, words: tuple[str, ...]) -> tuple[str, str, Renamed | None]:
        """``(status, replacement words, alias)`` for the command words as written.

        One word that is a group is ``ok`` (its subcommand may be elided or a flag); a group
        followed by a word that is none of its subcommands is ``unknown``.
        """
        groups = self.groups
        head, rest = words[0], words[1:]
        old = self.command_aliases.get((head,))
        group = old.new if old is not None and old.new in groups else head
        if group in groups and rest:
            sub = rest[0]
            leaf = self.command_aliases.get((head, sub)) or self.command_aliases.get((group, sub))
            if leaf is not None and (group, leaf.new) in self.commands:
                return DEPRECATED, f"{group} {leaf.new}", leaf
            if (group, sub) in self.commands:
                if old is not None:
                    return DEPRECATED, f"{group} {sub}", old
                return OK, "", None
            return UNKNOWN, "", None
        if (group,) in self.commands or group in groups:
            if old is not None:
                return DEPRECATED, group, old
            return OK, "", None
        if old is not None and (old.new,) in self.commands:
            return DEPRECATED, old.new, old
        return UNKNOWN, "", None

    def resolve_tool(self, name: str) -> tuple[str, str, Renamed | None]:
        if name in self.tools:
            return OK, "", None
        old = self.tool_aliases.get(name)
        if old is not None:
            return DEPRECATED, old.new, old
        return UNKNOWN, "", None


@dataclass(frozen=True)
class Ref:
    """One name found in a line: what was written, where in the line, and what it is."""

    kind: str  #: "command" | "tool"
    text: str  #: as written: ``gate record``, ``ddflow_brief``
    start: int  #: column of ``text`` in the line
    status: str = OK
    replacement: str = ""
    renamed: Renamed | None = None
    #: Where each word of a command starts, so a rewrite replaces the words themselves and
    #: keeps what stands between them (``gate  --force record``).
    cols: tuple[int, ...] = ()


@dataclass(frozen=True)
class Finding:
    """A deprecated, unknown or unchecked reference in one file."""

    path: str  #: repo-relative
    line: int  #: 1-based
    artifact: str  #: what kind of file: ``settings hook``, ``git hook``, ...
    ref: Ref
    managed: bool  #: inside a region ddflow wrote and nobody edited

    @property
    def where(self) -> str:
        return f"{self.path}:{self.line}"

    @property
    def proposal(self) -> str:
        if self.ref.status != DEPRECATED:
            return ""
        return f"replace `{self.ref.text}` with `{self.ref.replacement}`"

    def describe(self) -> str:
        r = self.ref
        what = f"{self.where} ({self.artifact}): "
        if r.status == DEPRECATED:
            since = f" since {r.renamed.since}" if r.renamed and r.renamed.since else ""
            until = (
                f", kept until {r.renamed.removed_in}" if r.renamed and r.renamed.removed_in else ""
            )
            how = (
                "`ddflow upgrade --apply` rewrites it (ddflow's own text)"
                if self.managed
                else f"yours to change: {self.proposal}"
            )
            return f"{what}`{r.text}` is deprecated{since}{until}; use `{r.replacement}` -- {how}"
        kind = "tool" if r.kind == "tool" else "command"
        return f"{what}`{r.text}` is not a ddflow {kind} this version has"


# -- finding names in text ------------------------------------------------------------


def _command_at(line: str, at: int, vocab: Vocabulary) -> Ref | None:
    """The command named after the ``ddflow`` ending at column ``at`` of ``line``: up to two
    words (the second only after a group), global options skipped. None when the next word
    is no command word at all (a placeholder, a path, a sentence)."""
    words: list[tuple[str, int]] = []
    skip = False
    for tok in _TOKEN.finditer(line[at:]):
        raw, col = tok.group(), at + tok.start()
        if skip:  # the value of a global option, quoted or not
            skip = False
            continue
        head = _STOP.split(raw, 1)[0]
        if head.startswith("-"):
            skip = head in _VALUE_FLAGS
        elif head:
            word = head.rstrip(".,:")
            if not _WORD.fullmatch(word):
                break
            words.append((word, col))
            if word != head or not vocab.takes_subcommand(words[0][0]) or len(words) == MAX_WORDS:
                break
        if head != raw:
            break
    if not words:
        return None
    names = tuple(w for w, _ in words)
    if not vocab.check_commands:
        return Ref("command", " ".join(names), words[0][1], UNCHECKED)
    status, replacement, renamed = vocab.resolve_command(names)
    cols = tuple(col for _, col in words)
    return Ref("command", " ".join(names), words[0][1], status, replacement, renamed, cols)


def refs_in(line: str, vocab: Vocabulary, *, code: bool) -> Iterator[Ref]:
    """Every reference in one line. ``code``: the whole line is a command line (its comment
    is not); otherwise (prose) only what sits inside backticks counts for a command, since
    "ddflow is" is a sentence."""
    scan_to = len(line)  # a code line's trailing comment names nothing that runs
    if code:
        if line.lstrip().startswith("#"):
            spans, scan_to = [], 0
        else:
            cut = re.search(r"\s#", line)
            scan_to = cut.start() if cut else len(line)
            spans = [(0, line[:scan_to])]
    else:
        spans = [(m.start(1), m.group(1)) for m in _BACKTICK.finditer(line)]
    for base, chunk in spans:
        for m in _CLI.finditer(chunk):
            if not _POSITION.search(chunk[: m.start()]):
                continue
            ref = _command_at(line, base + m.end(), vocab)
            if ref is not None:
                yield ref
    for m in _TOOL.finditer(line, 0, scan_to):
        if m.group() in NOT_TOOLS:
            continue
        if not vocab.check_tools:
            yield Ref("tool", m.group(), m.start(), UNCHECKED)
            continue
        status, replacement, renamed = vocab.resolve_tool(m.group())
        yield Ref("tool", m.group(), m.start(), status, replacement, renamed)


# -- managed regions ------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """A region of a text: ``name``, its comment opener and its inner lines (0-based, end
    exclusive)."""

    name: str
    open: str
    first: int
    last: int


def spans_of(lines: list[str]) -> list[Span]:
    out: list[Span] = []
    stack: list[tuple[str, str, int]] = []
    legacy: int | None = None
    for i, line in enumerate(lines):
        if m := _BEGIN.match(line):
            stack.append((m["name"], m["open"].strip(), i + 1))
        elif m := _END.match(line):
            for j in range(len(stack) - 1, -1, -1):
                if stack[j][0] == m["name"]:
                    name, opener, first = stack.pop(j)
                    out.append(Span(name, opener, first, i))
                    break
        elif _LEGACY_BEGIN in line and legacy is None:
            legacy = i + 1
        elif _LEGACY_END in line and legacy is not None:
            out.append(Span("legacy", "<!--", legacy, i))
            legacy = None
    return out


def _owned_spans(text: str) -> list[Span]:
    """The regions ddflow wrote AND nobody edited: a hand-edited one is the person's."""
    lines = text.splitlines()
    owned: list[Span] = []
    for sp in spans_of(lines):
        if sp.name == "legacy":
            continue  # unstamped: there is no digest to say it is untouched
        region = Managed(sp.name, open=sp.open, close="-->" if sp.open == "<!--" else "")
        try:
            if region.state(text) in ("current", "older"):
                owned.append(sp)
        except RegionError:
            continue
    return owned


# -- the artifacts a project holds ----------------------------------------------------

#: (glob, artifact label, is every line a command line?)
_TEXT_ARTIFACTS: tuple[tuple[str, str, bool], ...] = (
    (".pre-commit-config.yaml", "pre-commit config", True),
    ("AGENTS.md", "AGENTS.md", False),
    ("CLAUDE.md", "CLAUDE.md", False),
    ("GEMINI.md", "GEMINI.md", False),
    (".github/copilot-instructions.md", "copilot instructions", False),
    ("docs/ddflow/drivers/**/*.md", "driver doc", False),
    (".claude/commands/**/*.md", "slash command", False),
    (".gemini/commands/**/*.toml", "slash command", False),
    (".ddflow/prompts/**/*.md", "ejected prompt", False),
    (".ddflow/macros.toml", "macro", False),
    (".ddflow/local/macros.toml", "macro", False),
    (".ddflow/config.toml", "config macro", False),
    (".ddflow/local/config.toml", "config macro", False),
)
_HOOKS_SUFFIX = " (git hooks)"
_SETTINGS = (".claude/settings.json", ".gemini/settings.json")


def _git_hooks(repo: Path) -> list[Path]:
    try:
        d = hooks_dir(repo)
    except (RuntimeError, OSError):
        return []
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and not p.name.endswith(".sample"))


def _read(path: Path) -> str | None:
    try:
        return path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _text_findings(
    rel: str, text: str, artifact: str, vocab: Vocabulary, *, code: bool, whole: bool = False
) -> list[Finding]:
    lines = text.splitlines()
    owned = _owned_spans(text)
    fenced = False
    out: list[Finding] = []
    for i, line in enumerate(lines):
        if not code and _FENCE.match(line):
            fenced = not fenced
            continue
        managed = whole or any(sp.first <= i < sp.last for sp in owned)
        for ref in refs_in(line, vocab, code=code or fenced):
            if ref.status != OK:
                out.append(Finding(rel, i + 1, artifact, ref, managed))
    return out


def _settings_findings(repo: Path, rel: str, vocab: Vocabulary) -> list[Finding]:
    path = repo / rel
    text = _read(path)
    if text is None:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out: list[Finding] = []
    seen: dict[str, int] = {}  # the same command twice in a file: each at its own line
    for command in _hook_commands(data):
        line, seen[command] = _line_of(text, command, seen.get(command, 0))
        found = _text_findings(rel, command, "settings hook", vocab, code=True)
        # a hook ddflow wrote carries its stamped region; any other hook is the person's
        owned = bool(_owned_spans(command))
        for f in found:
            out.append(Finding(rel, line, f.artifact, f.ref, f.managed and owned))
    return out


def _hook_commands(data: object) -> Iterator[str]:
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return
    for groups in hooks.values():
        for g in groups if isinstance(groups, list) else ():
            for h in g.get("hooks", ()) if isinstance(g, dict) else ():
                if isinstance(h, dict) and isinstance(h.get("command"), str):
                    yield h["command"]


def _line_of(text: str, command: str, after: int = 0) -> tuple[int, int]:
    """``(line, where the match ends)`` of a hook command in its settings file, searching
    from ``after``: the whole string when it is found as written, else its first line
    (which another hook's may begin with)."""
    for ascii_only in (True, False):
        quoted = json.dumps(command, ensure_ascii=ascii_only)
        at = text.find(quoted, after)
        if at >= 0:
            return text.count("\n", 0, at) + 1, at + len(quoted)
    key = json.dumps(command.splitlines()[0] if command else "")[1:-1][:60]
    at = text.find(key, after) if key else -1
    return (text.count("\n", 0, at) + 1, at + len(key)) if at >= 0 else (1, after)


def scan(repo: Path, vocab: Vocabulary) -> list[Finding]:
    """Every deprecated, unknown or unchecked reference in the project's adopted artifacts, in a stable
    order (path, line)."""
    repo = Path(repo)
    out: list[Finding] = []
    for rel in _SETTINGS:
        out += _settings_findings(repo, rel, vocab)
    for hook in _git_hooks(repo):
        text = _read(hook)
        if text is None:
            continue
        # an old hook ddflow wrote before the region existed is wholly ddflow's
        spans = _owned_spans(text)
        legacy = HOOK_MARKER in text and not spans and "ddflow:begin" not in text
        out += _text_findings(
            f"{hook.name}{_HOOKS_SUFFIX}", text, "git hook", vocab, code=True, whole=legacy
        )
    for pattern, artifact, code in _TEXT_ARTIFACTS:
        for path in sorted(repo.glob(pattern)):
            if not path.is_file():
                continue
            text = _read(path)
            if text is None:
                continue
            out += _text_findings(
                path.relative_to(repo).as_posix(), text, artifact, vocab, code=code
            )
    return sorted(out, key=lambda f: (f.path, f.line, f.ref.start))


def report(findings: Iterable[Finding]) -> tuple[list[str], list[str]]:
    """``(problems, notes)`` for doctor. A reference ddflow itself wrote that names nothing
    is a problem (the command it runs will fail); a deprecated one, or one in the
    project's own text, is a note. References nobody could check are one note that says so."""
    problems: list[str] = []
    notes: list[str] = []
    unchecked: list[Finding] = []
    for f in findings:
        if f.ref.status == UNCHECKED:
            unchecked.append(f)
        elif f.ref.status == UNKNOWN and f.managed:
            problems.append(f.describe())
        else:
            notes.append(f.describe())
    if unchecked:
        kinds = sorted({f.ref.kind for f in unchecked})
        files = sorted({f.path for f in unchecked})
        shown = ", ".join(files[:3]) + (" ..." if len(files) > len(files[:3]) else "")
        missing = "command table" if "command" in kinds else "tool table"
        hint = " (the CLI's `ddflow doctor` checks command words)" if "command" in kinds else ""
        notes.append(
            f"{len(unchecked)} {'/'.join(kinds)} reference(s) in {shown} were not checked: "
            f"this process has no {missing} loaded{hint}"
        )
    return problems, notes


# -- rewriting ------------------------------------------------------------------------


def _apply_line(line: str, refs: list[Finding]) -> str:
    """``line`` with each reference's deprecated words replaced, rightmost first so the
    columns of the others stay true. Words keep what stood between them."""
    for f in sorted(refs, key=lambda f: -f.ref.start):
        r = f.ref
        old, new = r.text.split(), r.replacement.split()
        if r.kind == "command" and len(old) == len(new) == len(r.cols):
            for col, was, now in reversed(list(zip(r.cols, old, new, strict=True))):
                if was != now and line[col : col + len(was)] == was:
                    line = line[:col] + now + line[col + len(was) :]
        elif line[r.start : r.start + len(r.text)] == r.text:
            line = line[: r.start] + r.replacement + line[r.start + len(r.text) :]
    return line


def _rewrite_text(text: str, wanted: list[Finding]) -> str:
    """``text`` with the deprecated names in managed regions replaced and each touched region
    re-stamped. Everything outside a region is untouched, byte for byte."""
    by_line: dict[int, list[Finding]] = {}
    for f in wanted:
        by_line.setdefault(f.line, []).append(f)
    lines = text.splitlines(keepends=True)
    for n, refs in by_line.items():
        if 0 < n <= len(lines):
            body = lines[n - 1]
            lines[n - 1] = _apply_line(body.rstrip("\n"), refs) + (
                "\n" if body.endswith("\n") else ""
            )
    out = "".join(lines)
    for sp in _owned_spans(text):
        if not any(sp.first < f.line <= sp.last for f in wanted):
            continue
        region = Managed(sp.name, open=sp.open, close="-->" if sp.open == "<!--" else "")
        at = region._region().find(out)
        if at is None:
            continue
        out = region.splice(out, out[at[1] : at[2]])
    return out


def rewrite(
    repo: Path, vocab: Vocabulary, findings: Iterable[Finding], *, backup: bool = True
) -> list[str]:
    """Replace the deprecated names in the files' ddflow-managed regions; returns one line
    per file written. A file is backed up first (`.ddflow/backups/`); nothing is written
    when that fails. Unknown names, and anything in the project's own text, are left."""
    repo = Path(repo)
    todo: dict[str, list[Finding]] = {}
    for f in findings:
        if f.managed and f.ref.status == DEPRECATED and f.ref.replacement:
            todo.setdefault(f.path, []).append(f)
    done: list[str] = []
    for rel, wanted in sorted(todo.items()):
        path = _resolve(repo, rel)
        text = _read(path) if path else None
        if path is None or text is None:
            continue
        try:
            new = (
                _rewrite_settings(text, vocab) if rel in _SETTINGS else _rewrite_text(text, wanted)
            )
        except (NewerContent, RegionError) as exc:
            done.append(f"kept {rel}: {exc}")
            continue
        if new == text:
            continue
        if backup:
            try:
                make_backup(repo, [path], "", "", name=f"refs-{path.name}")
            except OSError as exc:
                done.append(f"kept {rel}: could not back it up ({exc})")
                continue
        replace_text(path, new)
        done.append(f"rewrote {len(wanted)} reference(s) in {rel}")
    return done


def _resolve(repo: Path, rel: str) -> Path | None:
    if rel.endswith(_HOOKS_SUFFIX):
        name = rel.removesuffix(_HOOKS_SUFFIX)
        return next((p for p in _git_hooks(repo) if p.name == name), None)
    return repo / rel


def _rewrite_settings(text: str, vocab: Vocabulary) -> str:
    """The settings file with each ddflow-written hook command rewritten as a text of its
    own (its stamped region is the command's); a hook of the person's is not touched, and
    neither is any byte outside the rewritten command strings (the file is not reformatted).
    A command string that cannot be found exactly once in the file is left."""
    data = json.loads(text)
    for groups in (data.get("hooks") or {}).values():
        for g in groups if isinstance(groups, list) else ():
            for h in g.get("hooks", ()) if isinstance(g, dict) else ():
                cmd = h.get("command") if isinstance(h, dict) else None
                if not isinstance(cmd, str) or not _owned_spans(cmd):
                    continue
                found = [
                    Finding("", f.line, f.artifact, f.ref, True)
                    for f in _text_findings("", cmd, "settings hook", vocab, code=True)
                    if f.managed and f.ref.status == DEPRECATED
                ]
                if found:
                    text = _swap_json_string(text, cmd, _rewrite_text(cmd, found))
    return text


def _swap_json_string(text: str, old: str, new: str) -> str:
    """``text`` with every JSON string ``old`` replaced by ``new``, spelled as the file spells
    it (escaped or not): the same command listed under two events is the same stale text.
    Unchanged when ``old`` is not there."""
    for ascii_only in (True, False):
        was, now = (
            json.dumps(old, ensure_ascii=ascii_only),
            json.dumps(new, ensure_ascii=ascii_only),
        )
        if was in text:
            return text.replace(was, now)
    return text
