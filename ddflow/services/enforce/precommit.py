"""A YAML reader for `.pre-commit-config.yaml` and the pre-commit-framework hook status."""

from __future__ import annotations

import itertools
import re
import shlex

#: pre-commit < 3.2 stage names, still accepted (clientlib._STAGES in pre-commit 4.6).
_OLD_STAGE = {"commit": "pre-commit", "merge-commit": "pre-merge-commit", "push": "pre-push"}


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
