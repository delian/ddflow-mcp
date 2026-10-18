"""A document type written as data: the TOML descriptor, parsed, merged and checked.

One file is one ``[doctype]`` table: scalars, ``[[doctype.section]]`` per section, optional
``[doctype.validate]`` and ``[doctype.review]``. A type is a single document (``path``) or a
collection (``pattern`` with ``{seq}`` ``{slug}`` ``{date}``). Types resolve through layers,
shipped -> project -> config; a later layer replaces a type of the same name, or extends it
(``extends = "<name>"``; a type extending its own name extends the earlier layer's). An
extending type overrides scalars and merges sections by id; a child drops one with
``remove = true``.

Validate rules are data too: ``[[doctype.validate.rules]]`` with a ``kind`` that `RULE_KINDS`
knows; a later task adds a kind by registering it. Unknown keys are refused with file:line and
every error names the file.

Pure: stdlib `tomllib`; `read_layer` is the one function that touches the file system.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from string import Formatter
from typing import Any

from ...core.registry import Registry

GENRES = (
    "tutorial", "howto", "reference", "explanation", "policy", "log", "changelog", "plan",
    "decision", "other",
)  # fmt: skip
CONTRACTS = ("evergreen", "point_in_time", "generated")
OWNERSHIPS = ("human", "generated", "shared")
MATCHES = ("literal", "regex")
PLACEHOLDERS = ("seq", "slug", "date")
CURRENT_SCHEMA = 1

_TOP = {
    "name", "title", "path", "pattern", "multi", "genre", "owner", "skeleton", "triggers",
    "validate", "schema_version", "extends", "contract", "review", "section",
}  # fmt: skip
_SECTION = {
    "id", "heading", "match", "level", "order", "required", "ownership", "source", "prompt",
    "guidance", "rules", "depends_on", "remove", "template",
}  # fmt: skip
_REVIEW = {"review_requires", "full_review_every_days", "stamp"}
_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


class DocTypeError(ValueError):
    """A descriptor that is not a document type; the message names the file."""


@dataclass(frozen=True)
class RuleKind:
    """A validate rule kind: its name and the keys a rule of it may carry."""

    name: str
    keys: frozenset[str] = frozenset()


RULE_KINDS: Registry[RuleKind] = Registry("validate rule kind", key=lambda k: k.name)
for _k in (
    RuleKind("required_sections"),
    RuleKind("heading_order"),
    RuleKind("max_bytes", frozenset({"limit"})),
    RuleKind("pattern", frozenset({"regex", "message"})),
):
    RULE_KINDS.register(_k)


@dataclass(frozen=True)
class Section:
    id: str
    heading: str = ""
    match: str = "literal"
    level: int = 2
    order: int = 0
    required: bool = False
    ownership: str = "human"
    source: str = ""
    template: str = ""  # inline Jinja text, or ``file:<repo path>``
    prompt: str = ""
    guidance: str = ""
    rules: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocType:
    name: str
    title: str
    path: str = ""
    pattern: str = ""
    multi: bool = False
    genre: str = "other"
    owner: str = ""  # empty: taken from CODEOWNERS by the caller
    skeleton: str = ""
    sections: tuple[Section, ...] = ()
    triggers: tuple[str, ...] = ()
    validate: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = CURRENT_SCHEMA
    extends: str = ""
    contract: str = "evergreen"
    review: Mapping[str, Any] = field(default_factory=dict)
    source: str = ""

    def expand(self, *, seq: int = 0, slug: str = "", date: str = "") -> str:
        """The path of one document of this type; a collection fills its pattern."""
        if not self.multi:
            return self.path
        return self.pattern.format(seq=seq, slug=slug, date=date)

    def to_mapping(self) -> dict[str, Any]:
        """The ``[doctype]`` table of a type, for the round trip tests and ``ddflow doc types``."""
        out: dict[str, Any] = {}
        for k in (
            "name",
            "title",
            "path",
            "pattern",
            "genre",
            "owner",
            "skeleton",
            "extends",
            "contract",
        ):
            if getattr(self, k):
                out[k] = getattr(self, k)
        out["multi"], out["schema_version"] = self.multi, self.schema_version
        if self.triggers:
            out["triggers"] = list(self.triggers)
        for k in ("validate", "review"):
            if getattr(self, k):
                out[k] = dict(getattr(self, k))
        out["section"] = [
            {k: (list(v) if isinstance(v, tuple) else v) for k, v in s.__dict__.items()}
            for s in self.sections
        ]
        return out

    def section(self, sid: str) -> Section:
        for s in self.sections:
            if s.id == sid:
                return s
        raise KeyError(sid)


def _line(text: str, key: str, start: int = 0) -> int:
    """The 1-based line at or after ``start`` (0-based) where ``key`` is set or tabled."""
    pat = re.compile(rf"^\s*(?:{re.escape(key)}\s*=|\[+[\w.]*\b{re.escape(key)}\b)")
    for i, ln in enumerate(text.splitlines()[start:], start):
        if pat.match(ln):
            return i + 1
    return 0


class _Src:
    """A file's name and text, for messages with file:line."""

    def __init__(self, source: str, text: str) -> None:
        self.source, self.text = source, text
        self.at = 0  # 0-based line the current section starts on; keys are searched from here

    def err(self, msg: str, key: str = "", start: int = 0) -> DocTypeError:
        line = _line(self.text, key, start or self.at) if key else 0
        return DocTypeError(f"{self.source}{f':{line}' if line else ''}: {msg}")

    def unknown(
        self, tab: Mapping[str, Any], allowed: set[str], where: str, start: int = 0
    ) -> None:
        if bad := sorted(set(tab) - allowed):
            raise self.err(f"unknown key {bad[0]!r} in {where}", bad[0], start)


def _strs(src: _Src, where: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise src.err(f"{where} must be a list of strings", where.rsplit(".", 1)[-1])
    return tuple(value)


def _typed(
    src: _Src, tab: Mapping[str, Any], key: str, kind: type, where: str, default: Any
) -> Any:
    v = tab.get(key, default)
    if isinstance(v, bool) != (kind is bool) or not isinstance(v, kind):
        raise src.err(f"{where}: {key} must be {kind.__name__}", key)
    return v


def _choice(
    src: _Src, tab: Mapping[str, Any], key: str, allowed: Sequence[str], where: str, default: str
) -> str:
    v = _typed(src, tab, key, str, where, default)
    if v not in allowed:
        raise src.err(f"{where}: {key} {v!r} is not one of {', '.join(allowed)}", key)
    return v


def _validate(src: _Src, tab: Any) -> dict[str, Any]:
    if not isinstance(tab, dict):
        raise src.err("validate must be a table", "validate")
    rules = tab.get("rules", [])
    if bad := sorted(set(tab) - {"rules"}):
        raise src.err(f"unknown key {bad[0]!r} in validate", bad[0])
    if not isinstance(rules, list):
        raise src.err("validate.rules must be an array of tables", "rules")
    for r in rules:
        if not isinstance(r, dict) or not isinstance(r.get("kind"), str):
            raise src.err("each validate rule is a table with a kind", "rules")
        if r["kind"] not in RULE_KINDS:
            raise src.err(
                f"unknown validate rule kind {r['kind']!r}. Known: {', '.join(RULE_KINDS)}", "kind"
            )
        if bad := sorted(set(r) - {"kind"} - RULE_KINDS[r["kind"]].keys):
            raise src.err(f"unknown key {bad[0]!r} in a {r['kind']} rule", bad[0])
    return {"rules": [dict(r) for r in rules]} if rules else {}


def _review(src: _Src, tab: Any) -> dict[str, Any]:
    if not isinstance(tab, dict):
        raise src.err("review must be a table", "review")
    src.unknown(tab, _REVIEW, "review")
    out = dict(tab)
    if "full_review_every_days" in out:
        _typed(src, out, "full_review_every_days", int, "review", 0)
    return out


def _section(src: _Src, raw: Any, after: int) -> tuple[Section, int]:
    if not isinstance(raw, dict):
        raise src.err("a section must be a table", "section", after)
    start = _line(src.text, "section", after)
    sid = raw.get("id")
    if not isinstance(sid, str) or not _NAME.match(sid):
        raise src.err("a section needs an id (lowercase letters, digits, - _)", "section", after)
    where = f"section {sid!r}"
    src.at = max(start - 1, 0)
    src.unknown(raw, _SECTION, where, max(start - 1, 0))
    out = {
        "id": sid,
        "heading": _typed(src, raw, "heading", str, where, ""),
        "match": _choice(src, raw, "match", MATCHES, where, "literal"),
        "level": _typed(src, raw, "level", int, where, 2),
        "order": _typed(src, raw, "order", int, where, 0),
        "required": _typed(src, raw, "required", bool, where, False),
        "ownership": _choice(src, raw, "ownership", OWNERSHIPS, where, "human"),
        "source": _typed(src, raw, "source", str, where, ""),
        "template": _typed(src, raw, "template", str, where, ""),
        "prompt": _typed(src, raw, "prompt", str, where, ""),
        "guidance": _typed(src, raw, "guidance", str, where, ""),
        "rules": _strs(src, f"{where}.rules", raw.get("rules", [])),
        "depends_on": _strs(src, f"{where}.depends_on", raw.get("depends_on", [])),
    }
    if out["match"] == "regex":
        try:
            re.compile(out["heading"])
        except re.error as exc:
            raise src.err(f"{where}: heading is not a regex: {exc}", "heading", start) from None
    src.at = 0
    _check_source(src, where, out["source"], start)
    return Section(**out), start


def _check_source(src: _Src, where: str, source: str, start: int) -> None:
    from .sources import SourceError, check  # lazy: sources imports this module

    try:
        check(source)
    except SourceError as exc:
        raise src.err(f"{where}: {exc}", "source", max(start - 1, 0)) from None


def read_raw(text: str, source: str) -> tuple[dict[str, Any], _Src]:
    """The ``[doctype]`` table of ``text``, with unknown keys refused (file:line)."""
    src = _Src(source, text)
    try:
        top = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise DocTypeError(f"{source}: not TOML: {exc}") from None
    if set(top) != {"doctype"} or not isinstance(top["doctype"], dict):
        raise src.err("the file is one [doctype] table", "doctype")
    raw = top["doctype"]
    src.unknown(raw, _TOP, "doctype")
    for k in ("validate", "review"):
        if k in raw and not isinstance(raw[k], dict):
            raise src.err(f"{k} must be a table", k)
    if not isinstance(raw.get("section", []), list):
        raise src.err("section must be an array of tables ([[doctype.section]])", "section")
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        raise src.err("name is required (lowercase letters, digits, - _)", "name")
    return raw, src


def _merge(parent: dict[str, Any], child: dict[str, Any], src: _Src) -> dict[str, Any]:
    out = {
        **parent,
        **{k: v for k, v in child.items() if k not in ("section", "validate", "review")},
    }
    for k in ("validate", "review"):
        if k in child:
            out[k] = {**parent.get(k, {}), **child[k]}
    secs = {
        s["id"]: dict(s) for s in parent.get("section", []) if isinstance(s, dict) and "id" in s
    }
    seen: set[str] = set()
    for s in child.get("section", []):
        if not isinstance(s, dict) or "id" not in s:
            raise src.err("a section needs an id", "section")
        if s["id"] in seen:
            raise src.err(f"duplicate section id {s['id']!r}", "section")
        seen.add(s["id"])
        if s.get("remove") is True:
            secs.pop(s["id"], None)
        elif s["id"] in secs:
            secs[s["id"]] = {**secs[s["id"]], **s}
        else:
            secs[s["id"]] = dict(s)
    out["section"] = list(secs.values())
    return out


def _check_pattern(src: _Src, where: str, pattern: str) -> None:
    try:
        fields = [f for _, f, _, _ in Formatter().parse(pattern) if f is not None]
        bad = [f for f in fields if f not in PLACEHOLDERS]
        if pattern and not bad:
            pattern.format(seq=1, slug="s", date="d")
    except (ValueError, KeyError, IndexError):
        bad = ["(malformed)"]
    if bad:
        raise src.err(
            f"{where}: pattern placeholder {{{bad[0]}}} is not one of {', '.join(PLACEHOLDERS)}",
            "pattern",
        )


def _sections(src: _Src, where: str, raw: dict[str, Any]) -> list[Section]:
    sections, seen = [], {}
    cursor = 0
    for rs in raw.get("section", []):
        sec, cursor = _section(src, rs, cursor)
        if sec.id in seen:
            raise src.err(
                f"{where}: duplicate section id {sec.id!r} (first at line {seen[sec.id]})",
                "section",
                cursor - 1,
            )
        seen[sec.id] = cursor
        sections.append(sec)
    for sec in sections:
        if missing := [d for d in sec.depends_on if d not in seen]:
            raise src.err(
                f"{where}: section {sec.id!r} depends_on unknown {missing[0]!r}", "depends_on"
            )
    sections.sort(key=lambda s: s.order)  # stable: file order breaks ties
    return sections


def build(raw: dict[str, Any], src: _Src) -> DocType:
    """The checked `DocType` of a merged table."""
    where = f"doctype {raw['name']!r}"
    path = _typed(src, raw, "path", str, where, "")
    pattern = _typed(src, raw, "pattern", str, where, "")
    multi = _typed(src, raw, "multi", bool, where, bool(pattern))
    if multi and not pattern:
        raise src.err(f"{where}: a collection (multi) needs a pattern", "multi")
    if not multi and (pattern or not path):
        raise src.err(
            f"{where}: a single document has a path and no pattern",
            "pattern" if pattern else "name",
        )
    _check_pattern(src, where, pattern)
    sections = _sections(src, where, raw)
    return DocType(
        name=raw["name"],
        title=_typed(src, raw, "title", str, where, raw["name"]),
        path=_typed(src, raw, "path", str, where, ""),
        pattern=_typed(src, raw, "pattern", str, where, ""),
        multi=multi,
        genre=_choice(src, raw, "genre", GENRES, where, "other"),
        owner=_typed(src, raw, "owner", str, where, ""),
        skeleton=_typed(src, raw, "skeleton", str, where, ""),
        sections=tuple(sections),
        triggers=_strs(src, f"{where}.triggers", raw.get("triggers", [])),
        validate=_validate(src, raw.get("validate", {})),
        schema_version=_typed(src, raw, "schema_version", int, where, CURRENT_SCHEMA),
        extends=_typed(src, raw, "extends", str, where, ""),
        contract=_choice(src, raw, "contract", CONTRACTS, where, "evergreen"),
        review=_review(src, raw.get("review", {})),
        source=src.source,
    )


def parse(text: str, *, source: str = "<doctype>") -> DocType:
    """One standalone descriptor (``extends`` is refused here: use `resolve`)."""
    raw, src = read_raw(text, source)
    if raw.get("extends"):
        raise src.err("extends needs the layers it resolves against; use resolve()", "extends")
    return build(raw, src)


#: A layer: ``(label, [(file name, text), ...])``, in resolution order shipped, project, config.
Layer = tuple[str, Sequence[tuple[str, str]]]


def resolve(layers: Sequence[Layer]) -> dict[str, DocType]:
    """Every type the layers define, by name. A later layer's type replaces an earlier one of
    the same name, or extends it; ``extends = "other"`` takes any type visible at that layer."""
    defs: dict[str, list[tuple[dict[str, Any], _Src, int]]] = {}
    for i, (label, files) in enumerate(layers):
        for fname, text in files:
            raw, src = read_raw(text, f"{label}/{fname}" if label else fname)
            have = defs.setdefault(raw["name"], [])
            if any(d[2] == i for d in have):
                raise src.err(f"document type {raw['name']!r} is defined twice in {label}", "name")
            have.append((raw, src, i))

    def merged(name: str, upto: int, trail: tuple[str, ...]) -> tuple[dict[str, Any], _Src]:
        cands = [d for d in defs.get(name, []) if d[2] <= upto]
        if not cands:
            raise DocTypeError(
                f"unknown document type {name!r}. Known: {', '.join(sorted(defs)) or '(none)'}"
            )
        raw, src, _ = cands[-1]
        parent = raw.get("extends", "")
        if not parent:
            return raw, src
        if not isinstance(parent, str):
            raise src.err("extends must be a type name", "extends")
        if parent == name:  # extend the same name from an earlier layer
            if len(cands) == 1:
                raise src.err(f"{name!r} extends itself but no earlier layer defines it", "extends")
            base, _ = merged(name, cands[-2][2], trail)
        else:
            if parent in trail or name in (parent,):
                raise src.err(f"extends cycle: {' -> '.join((*trail, name, parent))}", "extends")
            try:
                base, _ = merged(parent, upto, (*trail, name))
            except DocTypeError as exc:
                if str(exc).startswith("unknown document type"):
                    raise src.err(str(exc), "extends") from None
                raise
        return _merge(base, raw, src), src

    out = {}
    for name in sorted(defs):
        raw, src = merged(name, len(layers), ())
        raw = {**raw, "name": name}
        out[name] = build(raw, src)
    return out


def read_layer(label: str, directory: Path) -> Layer:
    """The ``*.toml`` files of a directory (none when it does not exist), by file name."""
    files = sorted(directory.glob("*.toml")) if directory.is_dir() else []
    return label, [(f.name, f.read_text(encoding="utf-8")) for f in files]


SHIPPED_DIR = Path(__file__).resolve().parents[2] / "templates" / "doctypes"


def registry(layers: Sequence[Layer] | None = None) -> Registry[DocType]:
    """A registry of the resolved types; by default only the shipped ones."""
    use = list(layers) if layers is not None else [read_layer("shipped", SHIPPED_DIR)]
    return Registry("document type", key=lambda d: d.name, loader=lambda: resolve(use).values())
