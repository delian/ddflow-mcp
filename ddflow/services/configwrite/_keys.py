"""The plain-keys guard (D-plain-keys): key spelling and id validation.

Split out of ``ddflow/services/configwrite.py``. Nothing writes: these judge a dotted
key or an id BEFORE anything is read, and render the one message that is safe to show
an operator typed from a normal keyboard. A manual `gate record` or a raw ``--append-toml``
reaches here through exactly the same functions as `--set`, so a quoted, spaced or
dotted key that would corrupt the file or silently nest a table is refused the same way
in every surface.
"""

from __future__ import annotations

import re
import tomllib

#: `gate.<id>.human` — three segments minimum. Named so the guard's shape is legible
#: rather than a bare `3` in a boolean chain.
_GATE_KEY_PARTS = 3


def _key_parts(k: str) -> list[str]:
    """A dotted key's segments, normalised: `_toml_upsert` strips them, so whitespace and
    quoting reach the same TOML key as the bare form."""
    return [seg.strip().strip("\"'") for seg in k.split(".")]


class KeyRefused(str):
    """A key refused under D-plain-keys: the caller answers exit 3 (refused), not 1."""


_BARE = re.compile(r"[A-Za-z0-9_-]+")


def plain_key_problem(dotted: str) -> str:
    """Why ``dotted`` is not a key a CLI or MCP surface accepts, or "" (D-plain-keys).

    A key is typed from a normal keyboard: dot-separated TOML bare-key segments, ASCII
    letters, digits, `_` and `-`. A quoted segment, a string escape, whitespace or
    non-ASCII is refused -- with the plain spelling named when the key has one, so no
    user is ever told to type an escape. Hand-written TOML files are still read as TOML.
    """
    if all(_BARE.fullmatch(seg) for seg in dotted.split(".")):
        return ""
    plain = _plain_spelling(dotted)
    return KeyRefused(
        f"refusing {dotted!r}: not a plain key -- each dot-separated part must be ASCII "
        f"letters, digits, `_` or `-` (D-plain-keys)" + (f"; use {plain}" if plain else "")
    )


def _bare_path(dotted: str) -> list[str]:
    """The segments TOML reads a quoted or escaped key as, when every one is bare; else []."""
    try:
        node: object = tomllib.loads(f"{dotted} = 0")
    except tomllib.TOMLDecodeError:
        return []
    path: list[str] = []
    while isinstance(node, dict) and len(node) == 1:
        key = next(iter(node))
        path.append(key)
        node = node[key]
    return path if node == 0 and all(_BARE.fullmatch(k) for k in path) else []


def _plain_spelling(dotted: str) -> str:
    """The bare spelling of a quoted or escaped key, as TOML reads it, or ""."""
    path = _bare_path(dotted)
    plain = ".".join(path)
    if not path:
        return ""
    # Never name a key that would itself be refused: a dotted gate id (`"gate".a.b.command`),
    # a single segment (`"gate"`), which is not <section>.<key> (roborev on babe29ff), or a
    # gate's operator-owned `human` flag (roborev on c077d794).
    human = len(path) >= _GATE_KEY_PARTS and path[0] == "gate" and path[-1] == "human"
    return "" if "." not in plain or human or _gate_key_problem([(plain, "")]) else plain


def _gate_key_problem(pairs: list[tuple[str, str]]) -> str:
    """A plain refusal for a `gate.*` key that names a dotted gate id (B72b8adba30).

    Run after `plain_key_problem`, so every segment is already bare. The id is the
    segment after `gate.`, except that `gate.<id>.env.<VAR>` -- `env` is the one
    table-valued gate field -- is the only four-part shape; any other longer key is a
    dotted id (`gate.x.command.command` names `x.command`) and would nest a table.
    """
    for k, _v in pairs:
        pp = k.split(".")
        if pp[0] != "gate" or len(pp) <= _GATE_KEY_PARTS:
            continue
        if len(pp) == _GATE_KEY_PARTS + 1 and pp[2] == "env":
            continue
        return gate_id_problem(".".join(pp[1:-1]))
    return ""


def gate_id_problem(gid: str) -> str:
    """Why ``gid`` cannot be a gate id, or "" when it can (B72b8adba30).

    A gate id is the `<id>` of the `[gate.<id>]` section, written as an unquoted dotted
    key: anything but a TOML bare key either breaks the file with a raw parse error or,
    for a dot, nests a table -- `gate.a.b.command` wrote `[gate.a.b]`, exit 0, and
    every later command refused to load it.
    """
    return bare_id_problem(gid, "a gate id", "the [gate.<id>] section of the config")


def bare_id_problem(ident: str, what: str, names: str) -> str:
    """Why ``ident`` -- an id written as ONE TOML key -- is refused, or "" (D-plain-keys).

    ``what`` says which id ("a gate id"), ``names`` the table it names. A dot nests a
    table, and anything else that is not bare breaks the file or needs quoting."""
    if _BARE.fullmatch(ident):
        return ""
    return KeyRefused(
        f"{ident!r} cannot be {what}: use only ASCII letters, digits, `_` and `-` "
        f"(it names {names}; D-plain-keys)."
    )


#: TOML lexed just far enough to find its KEYS: strings (so a quote or `=` inside one is
#: not mistaken for a key), comments, newlines, punctuation and bare runs.
_TOKEN = re.compile(
    r'(?P<str>"""(?:[^"\\]|\\.|"(?!""))*"{3,5}|\'\'\'(?:[^\']|\'(?!\'\'))*\'{3,5}'
    r'|"(?:[^"\\\n]|\\.)*"|\'[^\'\n]*\')'
    r"|(?P<comment>#[^\n]*)|(?P<nl>\n)|(?P<ws>[ \t\r]+)|(?P<punct>[\[\]{}=,.])"
    r"|(?P<word>[^\s\[\]{}=,.#\"']+)",
    re.DOTALL,
)


def _with_header_spelling(problem: str, header: str, brackets: int) -> str:
    """``problem``, naming the header's plain spelling when `plain_key_problem` named none:
    a single segment (`["flow"]`) is no `<section>.<key>` for `--set`, but as a table
    header it has one (`[flow]`)."""
    # Only the single segment: a longer one `_plain_spelling` declined is a spelling that
    # would itself be refused (a dotted gate id, a gate's `human`).
    if "; use " in problem or len(path := _bare_path(header)) != 1:
        return problem
    return KeyRefused(f"{problem}; use {'[' * brackets}{'.'.join(path)}{']' * brackets}")


def appended_key_problem(toml_text: str) -> str:
    """The D-plain-keys refusal for the first key of raw TOML not spelled plain, or "".

    For text `tomllib` has already accepted. `--append-toml` (and `ddflow_configure
    toml`) take TOML as written, so a quoted (`"tag_prefix"`), escaped or spaced
    (`a . b`) key reached the config that `--set` refuses (B7a1ed66cb9). Every key -- a
    table header, a key/value, an inline-table key -- is judged by `plain_key_problem`
    as its full path, so the refusal names the plain spelling when there is one."""
    found = [m for m in _TOKEN.finditer(toml_text) if m.lastgroup not in ("ws", "comment")]
    toks = [(m.lastgroup, m.group()) for m in found]
    spans = [m.span() for m in found]

    def raw(a: int, b: int) -> str:  # the source text of tokens a..b, spacing included
        return toml_text[spans[a][0] : spans[b][1]]

    header, last_key = "", ""
    opened: list[tuple[str, str]] = []  # (bracket, key path owning it) of the open value
    at_key, i = True, 0
    while i < len(toks):
        kind, text = toks[i]
        if kind == "nl":
            at_key = at_key or not opened
        elif at_key and not opened and text == "[":
            n = 2 if i + 1 < len(toks) and toks[i + 1][1] == "[" else 1  # [[array]]
            k = i + n
            while toks[k][1] != "]":
                k += 1
            header = raw(i + n, k - 1)
            if problem := plain_key_problem(header):
                return _with_header_spelling(problem, header, n)
            i, at_key = k + n, False
            continue
        elif at_key and kind in ("word", "str"):
            k = i
            while toks[k][1] != "=":
                k += 1
            parent = opened[-1][1] if opened else header
            last_key = f"{parent}.{raw(i, k - 1)}" if parent else raw(i, k - 1)
            if problem := plain_key_problem(last_key):
                return problem
            i, at_key = k + 1, False
            continue
        elif kind == "punct" and text in ("[", "{"):
            # An array's elements belong to the key that holds the array.
            owner = opened[-1][1] if opened and opened[-1][0] == "[" else last_key
            opened.append((text, owner))
            at_key = text == "{"
        elif kind == "punct" and text in ("]", "}"):
            opened.pop()
        elif text == "," and opened:
            at_key = opened[-1][0] == "{"
        i += 1
    return ""
