"""Writing `.ddflow/config.toml`: validate first, then replace in place.

Lifted out of `surfaces/commands/config.py`, which is where it was written and the wrong
layer for it. Nothing here presents anything — it parses TOML, decides whether a change
is *allowed*, and writes it atomically. That is domain policy, and keeping it in a
surface module meant the `api` layer could not perform a config change without reaching
up through a presentation layer, which is the exact shape B37 exists to remove.

Two of the rules are load-bearing and neither is obvious:

* **A pipeline may not name an undefined gate.** An unknown id is permanent silent
  damage: every item entering that pipeline blocks forever and `gate record` refuses the
  id. `workflow_problems` checks it BEFORE the write.
* **`gate.<id>.human` may not be set through here at all.** A human gate exists to make
  a person sign something; a tool that can flip it to `false` and then record the gate
  itself makes the signature worthless. `_guarded_human_gates` refuses the key rather
  than trusting the caller — the claim that this was "impossible through the MCP
  surface" was checked and turned out to be false.

* **A reviewer is guarded too** (decision D-reviewer-trust). Every write here goes
  through `_write_reviewed`: an agent's `kind = "command"` reviewer is undone and
  refused, and a reviewer whose identity a tool wrote is recorded so its reviews count
  only after a person approves it (`services/reviewer_trust.py`).

The in-place TOML edit is deliberate rather than a round-trip through a parser: the file
is written by hand and carries comments explaining every knob, and `tomllib` cannot
write, so a serialising round-trip would silently delete the documentation that makes
the file editable at all.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from ..config import Config, InvalidValue, parallel_range_problems
from ..infra import tomlcfg as TC
from . import reviewer_trust as RT

#: The git-ignored machine-local layer (decision D-no-own-services-local-dir). Read
#: LAST by `Config.load` and `tomlcfg.config_paths`, so what is written here wins.
LOCAL_DIR = Path(".ddflow") / "local"


def config_file(repo: Path, *, local: bool = False, own: str = "config.toml") -> Path:
    """The file a writer targets: the committed `.ddflow/<own>`, or its local twin.

    One function so every writer agrees on where "local" is. A writer that guessed its
    own path is how a reviewer endpoint ended up in the committed config while the
    reader looked for it under `local/` (bug B-reviewers-write-committed).
    """
    base = Path(repo) / (LOCAL_DIR if local else Path(".ddflow"))
    return base / own


def ensure_local_dir(repo: Path) -> Path:
    """Create `.ddflow/local/` with its own `*` `.gitignore`, and return it.

    The directory ignores itself rather than trusting `.ddflow/.gitignore`: a project
    whose ignore file predates `local/`, or that never ran `init`, must still not commit
    the endpoint, key variable or worker count it is about to receive.
    """
    d = Path(repo) / LOCAL_DIR
    d.mkdir(parents=True, exist_ok=True)
    ignore = d / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", "utf-8")
    return d


def _human_cleared(data: dict) -> list[str]:
    """Every `gate.<id>.human` an appended TOML document sets to anything but `true`.

    Only the clearing direction: declaring a NEW checkpoint by appending
    `human = true` is how a project adds one (tests/test_human_gate.py does exactly
    that), and making a gate stricter takes nothing from the operator.
    """
    gates = data.get("gate") if isinstance(data, dict) else None
    if not isinstance(gates, dict):
        return []
    return [
        f"gate.{gid}.human"
        for gid, spec in gates.items()
        if isinstance(spec, dict) and "human" in spec and spec["human"] is not True
    ]


def _effective(repo: Path, text: str, local: bool):
    """`(data, cfg)` for a candidate TEXT of the file being written.

    A committed write is judged ALONE: it is what every other clone gets, and a local
    layer that happens to mask a problem here masks nothing there. A local write is
    judged MERGED over the committed config, because it only ever takes effect on top
    of it -- a local `task_pipeline` that drops a committed human gate is a deletion
    of the operator's checkpoint even though the local file never names the gate.
    """
    import tomllib

    data = tomllib.loads(text)
    cfg = Config()
    if local:
        committed = config_file(repo)
        if committed.is_file():
            cfg._apply(tomllib.loads(committed.read_text("utf-8")), "file")
        cfg._apply(data, "local")
    else:
        cfg._apply(data, "file")
    return data, cfg


def _range_problems(repo: Path, text: str, local: bool) -> set[tuple[str, str]]:
    """The auto range problems (`config.parallel_range_problems`) a candidate TEXT would
    have, judged as `_effective` judges it. Raises what `_effective` raises."""
    return set(parallel_range_problems(_effective(repo, text, local)[1]))


def _new_range_problems(repo: Path, before: str, after: str, local: bool) -> set:
    """Range problems the edit INTRODUCES. Only new ones are refused, so a project whose
    range was already inconsistent can still make an unrelated edit, or the repair.

    A local write is judged over the committed layer; when that layer cannot be read
    (it does not parse, say -- the load path reports it), the edit is judged on its own
    over the shipped defaults instead, so a broken sibling never switches the guard off;
    and when the text BEFORE the edit does not load, every problem the result has is
    the edit's (a repair leaves none, so it is never refused).
    """
    unreadable = (tomllib.TOMLDecodeError, ValueError, OSError)
    try:
        found = _range_problems(repo, after, local)
    except unreadable:
        found = _range_problems(repo, after, False)  # the sibling layer is broken
        local = False
    try:
        return found - _range_problems(repo, before, local)
    except unreadable:  # the text before the edit does not load: the edit owns them all
        return found


def _range_refusal(problems: set[tuple[str, str]]) -> str:
    said = "; ".join(f"invalid {key}: {why}" for key, why in sorted(problems))
    return (
        f"that edit would make the adaptive range inconsistent: {said}. In auto, "
        "schedule.max_parallel_min <= schedule.max_parallel_tasks <= "
        "schedule.max_parallel_max; change the bound first, or set schedule.parallel fixed."
    )


def _toml_literal(value: str) -> str:
    """A TOML literal for `value`, quoting it unless it already is one."""
    if re.fullmatch(r"(true|false|-?\d+(\.\d+)?|\[.*\]|\{.*\})", value.strip()):
        return value
    return TC.basic_string(value)  # quote + escape as a TOML basic string (Bb11e7a8186)


def _is_header(line: str, header: str) -> bool:
    """Is this line the `[section]` header, allowing a trailing comment?

    `[gates]  # how work is checked` did not match an exact compare, so the upsert
    appended a SECOND `[gates]` -- and TOML forbids declaring a table twice, which means
    every later edit failed with a parse error pointing at a line the operator did not
    write. Commenting your own config should not disable the tool that edits it.
    """
    return line.split("#", 1)[0].strip() == header


#: TOML's two multi-line string delimiters. Built rather than written so this module's
#: own source does not have to escape them.
_TRIPLES = ('"' * 3, "'" * 3)


def _toml_lines(text: str) -> list[tuple[str, bool]]:
    """Every line paired with "is this line INSIDE a multi-line string?".

    The one thing a TOML line editor has to know. A hand-written agent prompt is a
    triple-quoted value, and a line inside one reading `command = <what you ran>`
    matched the key scan -- so `--command` was written INTO the prompt, the real key
    was never set, and `ddflow workflow` reported the result coherent. Exit 0: a
    silently dropped knob, and every future task handed a tampered instruction. A `[`
    in the same prose ended the section scan and inserted the new key mid-sentence.

    Counts delimiters rather than parsing. The alternative is a TOML round-trip, and
    the one thing worse than a line editor here is a writer that silently discards the
    comments this file carries most of its reasoning in.
    """
    out: list[tuple[str, bool]] = []
    delim = ""
    for raw in text.splitlines():
        inside = bool(delim)
        rest = raw
        while rest:
            if delim:
                hit = rest.find(delim)
                if hit < 0:
                    break
                rest = rest[hit + 3 :]
                delim = ""
                continue
            starts = [(rest.find(d), d) for d in _TRIPLES if d in rest]
            if not starts:
                break
            at, d = min(starts)
            if "#" in rest[:at]:
                break  # the opener is inside a comment
            delim = d
            rest = rest[at + 3 :]
        out.append((raw, inside))
    return out


def _outside_quotes(raw: str) -> str:
    """`raw` with quoted strings and the trailing comment removed.

    The bracket counter above must not see a `[` that is part of a VALUE. It used to:
    `body.count("[")` on `command = "sed \'s/\\[//g\'"` started the depth at 1, no
    later line ever brought it back to 0 (a `[section]` header contributes +1 then -1),
    and `_value_span` returned `len(lines)` — so replacing that key replaced everything
    from it to END OF FILE. Sibling keys and whole later sections were deleted, the
    result was still valid TOML, `Config.check` passed, the workflow diff was empty,
    and the write succeeded with exit 0.

    Probed: a `[gate.lint]` with `command = "sed \'s/\\[//g\'"` plus `timeout_s` and
    `title`. Re-setting `command` left `['command']` and dropped the other two, silently.

    Gate commands are arbitrary operator shell strings, so `sed \'s/\\[//g\'`,
    `cut -d\'[\' -f1` and `grep -F \'[\'` are all ordinary things to find in one. A
    comment marker inside a quoted string is the same hazard in the other direction,
    which is why the `#` split happens HERE rather than before the quote scan.
    """
    out, quote, esc = [], "", False
    for ch in raw:
        if quote:
            if esc:
                esc = False
            elif ch == "\\" and quote != "'":
                esc = True  # literal (single-quoted) TOML strings have no escapes
            elif ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == "#":
            break  # a comment, now that we know we are not inside a string
        out.append(ch)
    return "".join(out)


def _value_span(lines: list[tuple[str, bool]], start: int) -> int:
    """The index one past the end of the value beginning at `lines[start]`.

    A value spans lines two ways: a bracketed array written one entry per line, which
    is how a person writes a ten-gate pipeline, and a multi-line string. Replacing only
    the first line left the rest orphaned -- `task_pipeline = ["x"]` followed by a
    stray `]` -- which TOML rejects, so every later edit was refused with a parse error
    blaming the operator for the editor's mistake.
    """
    depth = 0
    for i in range(start, len(lines)):
        raw, inside = lines[i]
        body = "" if inside else _outside_quotes(raw)
        depth += body.count("[") + body.count("{") - body.count("]") - body.count("}")
        open_string = i + 1 < len(lines) and lines[i + 1][1]
        if depth <= 0 and not open_string:
            return i + 1
    return len(lines)


def _toml_upsert(text: str, dotted: str, literal: str) -> str:
    """Set one `<section>.<key>` in TOML text, in place, preserving comments.

    Pure, and takes TEXT rather than a path, so several edits compose into one write.
    Applying them one file-write at a time would leave the config half-updated when the
    third of four is rejected -- and a half-applied workflow change is the state nobody
    can reason about.
    """
    section, _, key = dotted.rpartition(".")
    lines = _toml_lines(text)
    header = f"[{section}]"
    try:
        start = next(
            i for i, (ln, inside) in enumerate(lines) if not inside and _is_header(ln, header)
        )
    except StopIteration:
        body = "\n".join(ln for ln, _ in lines).rstrip()
        return (f"{body}\n\n" if body else "") + f"{header}\n{key} = {literal}\n"
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if not lines[i][1] and lines[i][0].lstrip().startswith("[")
        ),
        len(lines),
    )
    i = start + 1
    while i < end:
        raw, inside = lines[i]
        stripped = raw.lstrip()
        if inside or stripped.startswith(("#", ";")) or "=" not in stripped:
            i += 1
            continue
        if stripped.split("=")[0].strip() == key:
            lines[i : _value_span(lines, i)] = [(f"{key} = {literal}", False)]
            return "\n".join(ln for ln, _ in lines).rstrip() + "\n"
        i = max(i + 1, _value_span(lines, i))
    lines.insert(end, (f"{key} = {literal}", False))
    return "\n".join(ln for ln, _ in lines).rstrip() + "\n"


def _workflow_problems(repo: Path, text: str, *, local: bool = False) -> set[str]:
    """The workflow problems a given config TEXT would have. Never raises.

    Loaded from the text rather than from disk, so the result can be judged before it
    becomes the state. A text that will not even load has no workflow problems to
    report -- `Config.check` is what catches that, and reporting it twice in different
    words is how an operator learns to read neither message.
    """
    from . import workflow as WF
    from .gates import GateDef, load_gates

    try:
        data, cfg = _effective(repo, text, local)
        gates = load_gates(repo, cfg)
        # `load_gates` overlays `[gate.*]` from the FILE, and this text is not on disk
        # yet -- so a gate being defined in the very same call was invisible, and
        # defining `lint` and piping it in one command refused itself for naming an
        # undefined gate. Judge the candidate, not the predecessor.
        for gid, spec in (data.get("gate") or {}).items():
            g = gates.get(gid) or GateDef(id=gid)
            for field, value in (spec or {}).items():
                if hasattr(g, field):
                    setattr(g, field, value)
            gates[gid] = g
        return {f"{f.subject}: {f.detail}" for f in WF.check(cfg, gates) if f.level == WF.PROBLEM}
    except Exception:
        return set()


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


def _parsed(text: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}


def _gate_table_problems(data: dict) -> set[tuple[str, str, str]]:
    """What is wrong with the `[gate.*]` blocks of a parsed config, STRUCTURALLY: `(kind,
    gate, field)` for an id that is not a bare key, a table where a field belongs (a
    dotted id nested one: `[gate.a.b]`), or a field no gate has. Tuples, not messages,
    so a pre-existing `[gate."a.b"]` cannot mask a new nested `[gate.a.b]` that would
    render the same words (roborev on 45abe764)."""
    from .gates import GateDef

    known = set(GateDef.__dataclass_fields__)
    out: set[tuple[str, str, str]] = set()
    for gid, spec in (data.get("gate") or {}).items():
        if gate_id_problem(gid):
            out.add(("id", gid, ""))
            continue
        if not isinstance(spec, dict):
            continue
        for name, value in spec.items():
            if name == "env":
                continue
            if isinstance(value, dict):
                out.add(("nested", gid, name))
            elif name not in known:
                out.add(("unknown", gid, name))
    return out


def _render_gate_problem(kind: str, gid: str, name: str) -> str:
    if kind == "id":
        return gate_id_problem(gid)
    if kind == "nested":
        return gate_id_problem(f"{gid}.{name}")
    return f"[gate.{gid}] has no field {name!r}"


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


def _guarded_human_gates(repo: Path, text: str, *, local: bool = False) -> set[str]:
    """Human gates that a given config TEXT places in a pipeline. Never raises.

    Used to refuse an edit that would REMOVE one. Blocking `gate.<id>.human` was not
    enough: `workflow drop plan_approved` took the checkpoint out of the pipeline
    entirely, exit 0, and `workflow pipeline task <list-without-it>` does the same by
    omission. Whether the operator's approval step exists is the operator's decision, and
    the flag and the pipeline membership are two ways of saying it.
    """
    from .gates import load_gates, pipelined

    try:
        data, cfg = _effective(repo, text, local)
        gates = load_gates(repo, cfg)
        for gid, spec in (data.get("gate") or {}).items():
            if gid in gates and isinstance(spec, dict) and "human" in spec:
                gates[gid].human = bool(spec["human"])
        # Every `gates.*_pipeline` (gates.pipelined), derived rather than listed: naming
        # task and phase missed `promotion_pipeline` -- where a deploy sign-off belongs.
        return {g for g in pipelined(cfg) if g in gates and gates[g].is_human_gate}
    except Exception:
        return set()


def _write_config(
    repo: Path,
    pairs: list[tuple[str, str]],
    *,
    dry_run: bool = False,
    check_workflow: bool = True,
    local: bool = False,
    person: bool = False,
    agent: str = "",
) -> tuple[str, str]:
    """Apply every `(dotted, value)` edit, validate ONCE, write ONCE, under a lock.

    Returns `(error, new_text)`; a non-empty error means nothing was written. The order
    is the whole point -- compose, check the RESULT, then replace the file atomically --
    because a writer that validates the state it is replacing has checked nothing, and
    a truncating write interrupted halfway leaves an empty config that loads as "no
    overrides at all" without saying so.

    Two validations, not one. `Config.check` is the SCHEMA: is every section and knob
    real. `workflow.check` is the MEANING: does the result hang together. Without the
    second, `workflow gate X --required` (with no pipeline) exited 0 having created the
    exact inert requirement that `ddflow workflow` then reports as a problem -- the
    writer manufacturing a defect its own reader diagnoses.

    Only NEW problems are refused. Refusing on any problem at all would mean a config
    already broken could never be repaired by the tool that reports it broken.

    `local=True` writes the git-ignored `.ddflow/local/config.toml` instead: this
    machine's endpoints, hosts and sizing, which must never reach the committed file.
    The same guards apply, judged on the committed config with the local one over it.
    """
    import tomllib

    # `gate.<id>.human` is not editable from here, and this is the one knob that is
    # special. Everything else in this file is a preference; that flag decides whether
    # a checkpoint belongs to the operator, and `ddflow_configure` is on the MCP
    # surface. Two calls -- flip it false, then `ddflow_gate_record` -- cleared a human
    # gate with no shell involved, which made the docstring claim that the ordinary
    # path is closed simply untrue. Declaring the gate in `.ddflow/gates.toml` is the
    # supported way, and that file is not writable from any tool.
    # Normalised, so whitespace and quoting cannot walk past the guard: `_toml_upsert`
    # strips the segments, so `" gate.x.human"` and `gate.x."human"` reach the same TOML
    # key as the bare form and must be refused the same way.
    blocked = [
        k
        for k, _v in pairs
        if (pp := _key_parts(k))
        and len(pp) >= _GATE_KEY_PARTS
        and pp[0] == "gate"
        and pp[-1] == "human"
    ]
    # The key itself first: plain keyboard keys only (D-plain-keys), exit 3 -- so the exit
    # code says "not a plain key" whatever field the key names.
    problem = next((plain_key_problem(k) for k, _v in pairs if plain_key_problem(k)), "")
    problem = problem or _gate_key_problem(pairs)
    if problem:
        return problem, ""
    if blocked:
        return (
            f"refusing to edit {', '.join(blocked)}: whether a gate is a human "
            f"checkpoint is the operator's decision, not a configurable preference. "
            f"Set `human` in .ddflow/gates.toml, which no tool writes.",
            "",
        )

    from . import workflow as WF

    path = config_file(repo, local=local)
    if local:
        # Even for a dry run: the lock below creates the directory anyway, and a
        # `local/` that exists without its own `*` ignore is one `git add` from
        # committing whatever lands there next.
        ensure_local_dir(repo)
    with TC.locked(path):
        text = path.read_text("utf-8") if path.exists() else ""
        text_before = text
        before = _workflow_problems(repo, text, local=local) if check_workflow else set()
        human_before = _guarded_human_gates(repo, text, local=local)
        for dotted, value in pairs:
            if "." not in dotted:
                return f"{dotted!r} is not <section>.<key>, e.g. gate.unit_tests.command", text
            text = _toml_upsert(text, dotted, _toml_literal(value))
        try:
            result = tomllib.loads(text)
            Config.check(result)
        except InvalidValue as exc:  # a known knob, a value it cannot take: refused
            return KeyRefused(f"that edit would break the config: {exc}"), text
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            return f"that edit would break the config: {exc}", text
        if new_range := _new_range_problems(repo, text_before, text, local):
            return KeyRefused(_range_refusal(new_range)), text
        # `gate` is a foreign table to Config.check, so a gate block is judged here, on the
        # parsed result: `gate.a.b.command` (however spelled -- quoted, escaped) wrote
        # `[gate.a.b]`, exit 0, and every later command refused to load it (B72b8adba30).
        # Only NEW problems: an existing one must not stop the edit that repairs it.
        new = _gate_table_problems(result) - _gate_table_problems(_parsed(text_before))
        if new:
            return "; ".join(sorted(_render_gate_problem(*p) for p in new)), text
        # Refusing the FLAG was not enough. `workflow drop <human-gate>` took the
        # checkpoint out of the pipeline, exit 0, and `workflow pipeline task <list
        # without it>` does the same by omission — two ways to delete the operator's
        # approval step without ever touching `human`. Checked on the RESULT, at the
        # choke point, because a guard in one branch is a guard the other branch does
        # not have, which is how the first version of this shipped.
        removed = sorted(human_before - _guarded_human_gates(repo, text, local=local))
        if removed:
            return (
                f"that edit would remove the human-approval gate(s) "
                f"{', '.join(removed)} from the pipeline. Whether the operator's "
                f"approval step exists is not a configurable preference — edit "
                f".ddflow/gates.toml, which no tool writes.",
                text,
            )
        if check_workflow:
            introduced = sorted(_workflow_problems(repo, text, local=local) - before)
            if introduced:
                return (
                    "that edit would leave the workflow incoherent:\n  "
                    + "\n  ".join(introduced)
                    + f"\nNothing was written. ({WF.PROBLEM} findings are refused at "
                    f"the point of writing; `ddflow workflow` reports any that are "
                    f"already there.)",
                    text,
                )
        if not dry_run:
            try:
                _write_reviewed(repo, path, text, person=person, agent=agent)
            except RT.ReviewerRefused as exc:
                return str(exc), text
    return "", text


def _append_config(
    repo: Path, toml_text: str, *, local: bool = False, person: bool = False, agent: str = ""
) -> tuple[str, Path]:
    """Append a TOML block to the committed or the local config. `(error, path)`.

    Validated against the MERGED text before anything is written, and held to the same
    human-gate rule as `_write_config`: the local file is read after the committed
    `gates.toml`, so an appended `[gates] task_pipeline` there could otherwise drop the
    operator's checkpoint on this machine with nothing in any diff to show it.
    """
    import tomllib

    try:
        data = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        return f"not valid TOML: {exc}", Path()
    # Plain keyboard keys only (D-plain-keys), exactly as `--set` judges them: exit 3.
    if problem := appended_key_problem(toml_text):
        return problem, Path()
    if blocked := _human_cleared(data):
        return (
            f"refusing to append {', '.join(blocked)}: whether a gate is a human "
            f"checkpoint is the operator's decision, not a configurable preference. "
            f"Set `human` in .ddflow/gates.toml, which no tool writes.",
            Path(),
        )
    path = config_file(repo, local=local)
    if local:
        ensure_local_dir(repo)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    with TC.locked(path):
        prev = path.read_text("utf-8") if path.exists() else ""
        merged = (prev.rstrip() + "\n\n" if prev.strip() else "") + toml_text.strip() + "\n"
        try:
            result = tomllib.loads(merged)
            Config.check(result)
        except InvalidValue as exc:
            return KeyRefused(f"appending this would break the config: {exc}"), Path()
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            return f"appending this would break the config: {exc}", Path()
        if new_range := _new_range_problems(repo, prev, merged, local):
            return KeyRefused(_range_refusal(new_range)), Path()
        # `[gate.a.b]` is bare in every segment, so the plain-key check passes it -- and
        # it nests a table every later command refuses to load. Judged as `_write_config`
        # judges `--set`: only NEW gate-table problems (roborev on a1c614f4).
        if new := _gate_table_problems(result) - _gate_table_problems(_parsed(prev)):
            said = sorted(_render_gate_problem(*p) for p in new)
            # Every problem at once, as `--set` says them; a key refusal keeps exit 3.
            refused = any(isinstance(m, KeyRefused) for m in said)
            return (KeyRefused if refused else str)("; ".join(said)), Path()
        removed = sorted(
            _guarded_human_gates(repo, prev, local=local)
            - _guarded_human_gates(repo, merged, local=local)
        )
        if removed:
            return (
                f"appending this would remove the human-approval gate(s) "
                f"{', '.join(removed)} from the pipeline. Edit .ddflow/gates.toml, "
                f"which no tool writes.",
                Path(),
            )
        try:
            _write_reviewed(repo, path, merged, person=person, agent=agent)
        except RT.ReviewerRefused as exc:
            return str(exc), Path()
    return "", path


def append_block(
    repo: Path,
    block: str,
    *,
    shared: bool = False,
    own: str = "",
    person: bool = False,
    agent: str = "",
) -> Path:
    """Append a hand-built block (a `[[reviewer]]`) to the local layer, or the committed
    config when `shared`. Returns the path written.

    Local by DEFAULT: what these writers carry -- an endpoint, a model of a private
    deployment, an API-key variable's name -- is one operator's setup, and ddflow
    recommends services but never ships someone's configuration to every clone.
    `own` names the dedicated local file (`reviewers.toml`); the shared target is
    always `.ddflow/config.toml`, the one committed file a project is configured in.
    """
    if shared:
        path = config_file(repo)
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        ensure_local_dir(repo)
        path = config_file(repo, local=True, own=own or "config.toml")
    import tomllib

    with TC.locked(path):
        prev = path.read_text("utf-8") if path.exists() else ""
        merged = prev.rstrip() + "\n" + block.rstrip() + "\n"
        # Parsed before it replaces anything: a block that does not parse would leave
        # a config no later command can load, and the reader's error would blame a file
        # the operator never edited.
        try:
            tomllib.loads(merged)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(
                f"refusing to write {path}: the result is not valid TOML: {exc}"
            ) from exc
        # Raises RT.ReviewerRefused (a ValueError) for an agent's command reviewer.
        _write_reviewed(repo, path, merged, person=person, agent=agent)
    return path


def _write_reviewed(repo: Path, path: Path, text: str, *, person: bool, agent: str) -> None:
    """The one place a tool writes config: under the reviewer-trust guard.

    Every writer here goes through it, so no surface can add a reviewer the guard does
    not see (decision D-reviewer-trust): an agent's `kind = "command"` reviewer is undone
    and refused, and any reviewer whose identity a tool created or changed is recorded
    as `reviewer.configured`, to count only after a person approves it.
    """
    RT.write(repo, path, text, person=person, agent=agent)
