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

The edit goes through tomlkit (`tomlcfg.upsert`), not through `tomllib` and a serialiser:
the file is written by hand and carries comments explaining every knob, `tomllib` cannot
write, and a plain dump of its data would silently delete the documentation that makes the
file editable at all. tomlkit parses the text with its comments, blank lines and key order,
changes the one value, and writes the rest back as it found it.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..config import (
    CONFIG_FORMAT,
    RENAMED,
    Config,
    InvalidValue,
    parallel_range_problems,
)
from ..infra import fsio
from ..infra import tomlcfg as TC
from . import install_info as II
from . import reviewer_trust as RT
from . import workflow as WF
from .review import Reviewer

#: The git-ignored machine-local layer (decision D-no-own-services-local-dir). Read
#: LAST by `Config.load` and `tomlcfg.config_paths`, so what is written here wins.
LOCAL_DIR = TC.LOCAL_DIR


def config_file(repo: Path, *, local: bool = False, own: str = "config.toml") -> Path:
    """The file a writer targets: the committed `.ddflow/<own>`, or its local twin.

    One function so every writer agrees on where "local" is. A writer that guessed its
    own path is how a reviewer endpoint ended up in the committed config while the
    reader looked for it under `local/` (bug B-reviewers-write-committed).
    """
    return TC.layer_path(repo, "local" if local else "file", own)


def ensure_local_dir(repo: Path) -> Path:
    """Create `.ddflow/local/` with its own `*` `.gitignore`, and return it.

    The directory ignores itself rather than trusting `.ddflow/.gitignore`: a project
    whose ignore file predates `local/`, or that never ran `init`, must still not commit
    the endpoint, key variable or worker count it is about to receive.
    """
    return fsio.ensure_ignored_dir(Path(repo) / LOCAL_DIR)


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
    and when the text BEFORE the edit does not load, only the problems the result has
    beyond those the committed layer brings are the edit's (a repair is never refused).
    """
    unreadable = (tomllib.TOMLDecodeError, ValueError, OSError)

    def judged(text: str, over_committed: bool) -> set | None:
        try:
            return _range_problems(repo, text, over_committed)
        except unreadable:
            return None

    found = judged(after, local)
    if found is None:  # the sibling layer is broken: judge the edit over the defaults
        local = False
        found = judged(after, False) or set()
    had = judged(before, local)
    if had is None:  # the text before the edit does not load: what it inherited stays
        had = judged("", local) or set()
    return found - had


def _range_refusal(problems: set[tuple[str, str]]) -> str:
    said = "; ".join(f"invalid {key}: {why}" for key, why in sorted(problems))
    return (
        f"that edit would make the adaptive range inconsistent: {said}. In auto, "
        "schedule.max_parallel_min <= schedule.max_parallel_tasks <= "
        "schedule.max_parallel_max; change the bound first, or set schedule.parallel fixed."
    )


def _workflow_problems(repo: Path, text: str, *, local: bool = False) -> set[str]:
    """The workflow problems a given config TEXT would have. Never raises.

    Loaded from the text rather than from disk, so the result can be judged before it
    becomes the state. A text that will not even load has no workflow problems to
    report -- `Config.check` is what catches that, and reporting it twice in different
    words is how an operator learns to read neither message.
    """
    from .gates import load_gates

    try:
        data, cfg = _effective(repo, text, local)
        gates = load_gates(repo, cfg)
        # `load_gates` overlays `[gate.*]` from the FILE, and this text is not on disk
        # yet -- so a gate being defined in the very same call was invisible, and
        # defining `lint` and piping it in one command refused itself for naming an
        # undefined gate. Judge the candidate, not the predecessor.
        WF.apply_gate_table(gates, data.get("gate") or {})
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
        WF.apply_gate_table(gates, data.get("gate") or {}, only=("human",))
        # Every `gates.*_pipeline` (gates.pipelined), derived rather than listed: naming
        # task and phase missed `promotion_pipeline` -- where a deploy sign-off belongs.
        return {g for g in pipelined(cfg) if g in gates and gates[g].is_human_gate}
    except Exception:
        return set()


def _written_keys(pairs: list[tuple[str, str]]) -> set[str]:
    """The dotted keys an edit writes, each under its CURRENT spelling: what `Config.check`
    judges strictly, so a key the file already holds that this version does not know is left
    alone (D-compat 2)."""
    return {".".join(_key_parts(RENAMED[k][0] if k in RENAMED else k)) for k, _v in pairs}


def _appended_keys(data: dict) -> set[str]:
    """The dotted keys an appended TOML document writes (a table's keys, one level down; an
    `[export.<doc>]` table's keys, two), as `Config.check` takes `written`."""
    out: set[str] = set()
    for sec, values in data.items():
        out.add(sec)
        if not isinstance(values, dict):
            continue
        for knob, v in values.items():
            out.add(f"{sec}.{knob}")
            if isinstance(v, dict):
                out |= {f"{sec}.{knob}.{k}" for k in v}
    return out


def format_problem(text: str, path: Path) -> str:
    """Why this file may not be WRITTEN by this ddflow, or "": its `format = N` is newer than
    the config format this code understands (`CONFIG_FORMAT`), so an edit could rewrite a
    section's newer shape (D-compat 2: refused only on a direct conflict). Reading is never
    refused. Exit 3, with advice fitted to how this ddflow is installed."""
    try:
        fmt = tomllib.loads(text).get("format", 1) if text.strip() else 1
    except tomllib.TOMLDecodeError:
        # A file that does not parse is not a licence to write it: read the declaration
        # off the top of the text, before the first table.
        top = re.split(r"(?m)^\s*\[", text, maxsplit=1)[0]
        m = re.search(r"(?m)^\s*format\s*=\s*(\d+)\s*(?:#.*)?$", top)
        fmt = int(m.group(1)) if m else 1
    if isinstance(fmt, bool) or not isinstance(fmt, int) or fmt <= CONFIG_FORMAT:
        return ""
    advice = II.upgrade_advice()
    return KeyRefused(
        f"refusing to write {path}: it is config format {fmt}, and this ddflow understands "
        f"format {CONFIG_FORMAT}; an edit could lose what a newer ddflow wrote. "
        f"{advice[:1].upper()}{advice[1:]}"
    )


class Spelled(str):
    """A value as a PERSON typed it (`config --set key 5`): written as the TOML literal it
    spells when it is one (a number, a boolean, an array, an inline table), else quoted.
    Any other value is typed: a `str` is always a string (`--command 5` is the text "5"),
    and a list, bool or number is written as such (`tomlcfg.value`)."""


def _literal(value: object) -> str:
    return TC.literal(value) if isinstance(value, Spelled) else TC.value(value)


@dataclass(frozen=True)
class SetPairs:
    """Set each `<section>.<key>` in turn, in place: comments and layout stay."""

    pairs: tuple[tuple[str, object], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "pairs", tuple((k, v) for k, v in self.pairs))


@dataclass(frozen=True)
class AppendText:
    """Append a TOML document to the layer's `config.toml`."""

    text: str


@dataclass(frozen=True)
class Block:
    """Append a hand-built block (a `[[reviewer]]`). In the local layer it goes to ``own``,
    the dedicated machine-local file (`reviewers.toml`); the committed layer's target is
    always `.ddflow/config.toml`, the one committed file a project is configured in."""

    text: str
    own: str = ""


Edit = SetPairs | AppendText | Block


@dataclass(frozen=True)
class Guards:
    """Which optional judgements an edit is held to. The key, human-gate, format, schema,
    range and gate-table guards are not optional."""

    #: Refuse an edit that leaves the workflow incoherent (`workflow.check`).
    workflow: bool = True


DEFAULT_GUARDS = Guards()


@dataclass
class EditResult:
    """What `apply_edit` did. ``error`` is empty on success and then nothing was refused;
    a `KeyRefused` is a refusal (exit 3), a `ReviewerRefusal` an agent's refused reviewer (the
    caller picks its exit), any other string a failure. ``text`` is the file's text (as it would be, on a dry run);
    ``path`` the file written."""

    error: str = ""
    text: str = ""
    path: Path = field(default_factory=Path)
    #: `merge=union` lines added to `.gitattributes` by a committed write.
    attributes_added: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return not self.error


class ReviewerRefusal(str):
    """A refusal by the reviewer-trust guard (D-reviewer-trust): an agent's command reviewer."""


def _apply_edits(text: str, pairs: tuple[tuple[str, object], ...]) -> tuple[str, str]:
    """``text`` with every ``(dotted, value)`` set in turn, and what stopped it, if
    anything: (new text, "") or (text so far, the refusal)."""
    for dotted, value in pairs:
        if "." not in dotted:
            return text, f"{dotted!r} is not <section>.<key>, e.g. gate.unit_tests.command"
        try:
            # A renamed knob is written under its CURRENT key (D-compat); the old spelling
            # still works as an argument.
            key = RENAMED[dotted][0] if dotted in RENAMED else dotted
            text = TC.upsert(text, key, _literal(value))
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            return text, f"that edit would break the config: {exc}"
    # The next write moves what the file still holds under an old key (D-compat).
    try:
        text, _ = TC.move_keys(
            text, {old: new for old, (new, _s, _r) in RENAMED.items()}, live=Config()._sections()
        )
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        return text, f"that edit would break the config: {exc}"
    return text, ""


def _key_refusal(pairs: tuple[tuple[str, object], ...]) -> str:
    """Why these `--set` keys are refused before anything is read, or "".

    `gate.<id>.human` is not editable from here, and this is the one knob that is
    special. Everything else in the file is a preference; that flag decides whether a
    checkpoint belongs to the operator, and `ddflow_configure` is on the MCP surface. Two
    calls -- flip it false, then `ddflow_gate_record` -- cleared a human gate with no
    shell involved. Declaring the gate in `.ddflow/gates.toml` is the supported way, and
    no tool writes that file. Normalised, so whitespace and quoting cannot walk past the
    guard: `TC.upsert` strips the segments, so `" gate.x.human"` and `gate.x."human"`
    reach the same TOML key as the bare form and are refused the same way.
    """
    keyed = [(k, str(v)) for k, v in pairs]
    # The key itself first: plain keyboard keys only (D-plain-keys), exit 3 -- so the exit
    # code says "not a plain key" whatever field the key names.
    problem = next((plain_key_problem(k) for k, _v in keyed if plain_key_problem(k)), "")
    problem = problem or _gate_key_problem(keyed)
    if problem:
        return problem
    blocked = [
        k
        for k, _v in keyed
        if (pp := _key_parts(k))
        and len(pp) >= _GATE_KEY_PARTS
        and pp[0] == "gate"
        and pp[-1] == "human"
    ]
    if blocked:
        return _human_refusal("edit", blocked)
    return ""


def _human_refusal(verb: str, blocked: list[str]) -> str:
    return (
        f"refusing to {verb} {', '.join(blocked)}: whether a gate is a human "
        f"checkpoint is the operator's decision, not a configurable preference. "
        f"Set `human` in .ddflow/gates.toml, which no tool writes."
    )


def _text_refusal(text: str) -> str:
    """Why TOML text to append is refused before anything is read, or ""."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return f"not valid TOML: {exc}"
    # Plain keyboard keys only (D-plain-keys), exactly as `--set` judges them: exit 3.
    if problem := appended_key_problem(text):
        return problem
    if blocked := _human_cleared(data):
        return _human_refusal("append", blocked)
    return ""


def _check_reviewer_fields(data: dict, path: Path) -> None:
    """ValueError for a `[[reviewer]]` entry in ``data`` with a field `Reviewer` lacks.

    Strict whichever layer it goes to: `Config.check` passes over `[[reviewer]]` (another
    module's table), and read back an unknown field is skipped with a warning in the local
    layer and fails every load in a committed file of ddflow's own tree -- either way not
    what was meant (B96fd182086). A writer knows every field it may write.
    """
    blocks = data.get("reviewer")
    if blocks is None:
        return
    # `[reviewer]` (one bracket) or `reviewer = "x"` is read as no reviewer at all.
    if not isinstance(blocks, list) or not all(isinstance(b, dict) for b in blocks):
        raise ValueError(f"`reviewer` in {path} must be `[[reviewer]]` blocks (two brackets)")
    for n, raw in enumerate(blocks, 1):
        TC._check(raw, set(Reviewer.__dataclass_fields__), f"[[reviewer]] #{n} of {path}")


def _compose(prev: str, edit: Edit) -> tuple[str, str]:
    """`(the file's text with ``edit`` applied, the refusal or "")`."""
    if isinstance(edit, SetPairs):
        return _apply_edits(prev, edit.pairs)
    if isinstance(edit, Block):
        return fsio.APPEND.splice(prev, edit.text.rstrip() + "\n"), ""
    return (prev.rstrip() + "\n\n" if prev.strip() else "") + edit.text.strip() + "\n", ""


def _schema_problem(edit: Edit, text: str, path: Path) -> str:
    """Why the RESULT is not a loadable config (`Config.check`, strict for what the edit
    writes), or "". A `KeyRefused` for a known knob with a value it cannot take."""
    said = "that edit" if isinstance(edit, SetPairs) else "appending this"
    try:
        result = tomllib.loads(text)
        if isinstance(edit, SetPairs):
            Config.check(result, written=_written_keys(edit.pairs))
        else:
            data = tomllib.loads(edit.text)
            Config.check(result, written=_appended_keys(data))
            _check_reviewer_fields(data, path)
    except InvalidValue as exc:  # a known knob, a value it cannot take: refused
        return KeyRefused(f"{said} would break the config: {exc}")
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        return f"{said} would break the config: {exc}"
    return ""


def _human_removed(repo: Path, edit: Edit, prev: str, text: str, layer: str) -> str:
    """The refusal for an edit that REMOVES a human-approval gate from a pipeline, or "".

    Refusing the FLAG was not enough. `workflow drop <human-gate>` took the checkpoint out
    of the pipeline, exit 0, and `workflow pipeline task <list without it>` does the same
    by omission, and the local file is read after the committed `gates.toml`, so an
    appended `[gates] task_pipeline` there could drop it on this machine with nothing in
    any diff to show it. Checked on the RESULT, for every kind of edit, at the choke point.
    """
    local = layer == "local"
    removed = sorted(
        _guarded_human_gates(repo, prev, local=local)
        - _guarded_human_gates(repo, text, local=local)
    )
    if not removed:
        return ""
    if isinstance(edit, SetPairs):
        return (
            f"that edit would remove the human-approval gate(s) "
            f"{', '.join(removed)} from the pipeline. Whether the operator's "
            f"approval step exists is not a configurable preference — edit "
            f".ddflow/gates.toml, which no tool writes."
        )
    return (
        f"appending this would remove the human-approval gate(s) "
        f"{', '.join(removed)} from the pipeline. Edit .ddflow/gates.toml, "
        f"which no tool writes."
    )


def _gate_problem(prev: str, text: str) -> str:
    """The NEW `[gate.*]` table problems of a result, or "". `gate` is a foreign table to
    `Config.check`, so a gate block is judged here, on the parsed result: `gate.a.b.command`
    (however spelled) wrote `[gate.a.b]`, exit 0, and every later command refused to load
    it (B72b8adba30). Only NEW problems: an existing one must not stop the edit that
    repairs it."""
    new = _gate_table_problems(_parsed(text)) - _gate_table_problems(_parsed(prev))
    if not new:
        return ""
    said = sorted(_render_gate_problem(*p) for p in new)
    # Every problem at once; a key refusal keeps exit 3.
    return (KeyRefused if any(isinstance(m, KeyRefused) for m in said) else str)("; ".join(said))


def _workflow_refusal(repo: Path, before: set[str], text: str, layer: str) -> str:
    """The refusal for problems an edit INTRODUCES into the workflow (`workflow.check`), or "".

    `workflow.check` is the MEANING; `Config.check` the SCHEMA. Without it
    `workflow gate X --required` (with no pipeline) exited 0 having created the exact inert
    requirement `ddflow workflow` then reports as a problem. Only NEW problems are
    refused, so a config already broken can still be repaired by the tool that reports it."""

    introduced = sorted(_workflow_problems(repo, text, local=layer == "local") - before)
    if not introduced:
        return ""
    return (
        "that edit would leave the workflow incoherent:\n  "
        + "\n  ".join(introduced)
        + f"\nNothing was written. ({WF.PROBLEM} findings are refused at "
        f"the point of writing; `ddflow workflow` reports any that are "
        f"already there.)"
    )


def _result_problem(
    repo: Path, edit: Edit, layer: str, path: Path, prev: str, text: str, guards: Guards
) -> str:
    """Why the composed ``text`` may not replace ``prev`` in ``path``, or "".

    The order is the whole point: judge the RESULT, never the state being replaced -- a
    writer that validates what is on disk has checked nothing. The same judgements for
    every kind of edit; a dedicated file (`reviewers.toml`) carries no config, so only its
    syntax and its reviewer blocks are judged.
    """
    if path.name != "config.toml":
        try:
            tomllib.loads(text)
            _check_reviewer_fields(_parsed(edit.text) if isinstance(edit, Block) else {}, path)
        except tomllib.TOMLDecodeError as exc:
            return f"refusing to write {path}: the result is not valid TOML: {exc}"
        except ValueError as exc:
            return str(exc)
        return ""
    local = layer == "local"
    before = _workflow_problems(repo, prev, local=local) if guards.workflow else set()
    if problem := _schema_problem(edit, text, path):
        return problem
    if new_range := _new_range_problems(repo, prev, text, local):
        return KeyRefused(_range_refusal(new_range))
    return (
        _gate_problem(prev, text)
        or _human_removed(repo, edit, prev, text, layer)
        or (_workflow_refusal(repo, before, text, layer) if guards.workflow else "")
    )


def apply_edit(
    repo: Path,
    edit: Edit,
    *,
    layer: str = "file",
    guards: Guards = DEFAULT_GUARDS,
    dry_run: bool = False,
    person: bool = False,
    agent: str = "",
) -> EditResult:
    """The one way ddflow changes a config file: ``edit`` applied to ``layer``'s file.

    ``layer`` is `"file"` (the committed `.ddflow/config.toml`, which every clone gets) or
    `"local"` (the git-ignored `.ddflow/local/config.toml`: this machine's endpoints, hosts
    and sizing, which must never reach the committed file). The same guards apply to
    both, judged on the committed config with the local one over it.

    Compose every edit, judge the RESULT once, then replace the file atomically under a
    lock; a truncating write interrupted halfway would leave an empty config that loads as
    "no overrides at all" without saying so. Nothing is written when anything is refused
    (``EditResult.error``) or on ``dry_run``. A committed write also brings
    `.gitattributes` up to date with `[lease] append_only_globs`.

    Whether the writer is ``person`` or an ``agent`` decides what the reviewer-trust guard
    allows (decision D-reviewer-trust).
    """
    repo = Path(repo)
    refusal = _key_refusal(edit.pairs) if isinstance(edit, SetPairs) else _text_refusal(edit.text)
    if refusal:
        return EditResult(refusal)
    own = edit.own if isinstance(edit, Block) and layer == "local" and edit.own else "config.toml"
    path = TC.layer_path(repo, layer, own)
    if layer == "local":
        # Even for a dry run: the lock below creates the directory anyway, and a `local/`
        # that exists without its own `*` ignore is one `git add` from committing whatever
        # lands there next.
        ensure_local_dir(repo)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    with TC.locked(path):
        prev = path.read_text("utf-8") if path.exists() else ""
        if refused := format_problem(prev, path):
            return EditResult(refused, prev)
        text, failure = _compose(prev, edit)
        failure = failure or _result_problem(repo, edit, layer, path, prev, text, guards)
        if failure:
            return EditResult(failure, text)
        if not dry_run:
            try:
                # The one place a tool writes config: under the reviewer-trust guard. An
                # agent's `kind = "command"` reviewer is undone and refused, and any
                # reviewer whose identity a tool created or changed is recorded, to count
                # only after a person approves it.
                RT.write(repo, path, text, person=person, agent=agent)
            except RT.ReviewerRefused as exc:
                return EditResult(ReviewerRefusal(str(exc)), text)
    added: list[str] = []
    if layer == "file" and own == "config.toml" and not dry_run:
        from . import shared_files as SF

        added = SF.sync_attributes(repo)
    return EditResult("", text, path, added)
