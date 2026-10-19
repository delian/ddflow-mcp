"""Validate a document against its type (B-dt-validate): the engine.

``validate(dt, text, path)`` reads the document once (`mdparse`), finds the type's sections in it,
and runs each ``[[doctype.validate.rules]]`` entry of the type. A rule kind is a function in a
``rule_*.py`` file of this package that registers itself with `rule` -- a new kind is a new file,
and `spec.RULE_KINDS` (which finds those files) lists it, so a descriptor can name it. A rule
that cannot be applied (a bad regex, a link target it cannot read) is a finding saying so,
never silence. Findings are the docs check's `Finding` of kind ``doctype``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ...core import clock
from ..docscheck_md import lines
from ..docscheck_types import Finding
from . import mdparse
from .spec import RULE_KINDS, DocType, RuleKind

Read = Callable[[str], "str | None"]
Check = Callable[["Ctx", Mapping[str, Any]], Iterable[Finding]]

#: Keys every rule kind may carry: the section it applies to, and the message that replaces
#: its default.
COMMON_KEYS = frozenset({"section", "message"})


def rule(name: str, *keys: str) -> Callable[[Check], Check]:
    """Register ``fn`` as the check of the validate rule kind ``name`` (with these keys)."""

    def deco(fn: Check) -> Check:
        RULE_KINDS.register(RuleKind(name, COMMON_KEYS | frozenset(keys), fn))
        return fn

    return deco


@dataclass
class Ctx:
    """One document under check: its type, structure, path, and what a rule may look up."""

    dt: DocType
    doc: mdparse.Doc
    path: str
    read: Read | None = None  #: text of another tracked file (None: not tracked / unreadable)
    today: date = field(default_factory=lambda: clock.now_utc().date())
    found: dict[str, mdparse.Part | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for sec in self.dt.sections:
            self.found[sec.id] = (
                self._locate(sec.heading, sec.match, sec.level) if sec.heading else None
            )

    def _locate(self, heading: str, match: str, level: int) -> mdparse.Part | None:
        try:
            rx = (
                re.compile(heading)
                if match == "regex"
                else re.compile(re.escape(heading), re.IGNORECASE)
            )
        except re.error:
            return None  # `spec` refuses a bad heading regex at load; a hand-built type reads as absent
        hits = [p for p in self.doc.parts if rx.fullmatch(p.heading.text)]
        return next((p for p in hits if p.heading.level == level), hits[0] if hits else None)

    def finding(self, rule_kind: str, line: int, detail: str, rule_: Mapping[str, Any]) -> Finding:
        ref = rule_kind + (f":{rule_['section']}" if rule_.get("section") else "")
        return Finding("doctype", self.path, line, ref, str(rule_.get("message") or detail))

    def scope(self, rule_: Mapping[str, Any]) -> mdparse.Part | None:
        """The part a rule judges: its section, or the whole document when it names none.
        None when it names a section the document lacks: `required_sections` reports that."""
        sid = rule_.get("section")
        return self.found.get(sid) if sid else self.doc.whole

    def prose(self, rule_: Mapping[str, Any]) -> list[tuple[int, str]]:
        """(line number, line) of the prose in a rule's scope; fenced code is left out."""
        part = self.scope(rule_)
        if part is None:
            return []
        return [
            (n, ln)
            for n, ln, code in lines(self.doc.text)
            if part.heading.line < n <= part.end and not code
        ]


def regex(
    ctx: Ctx, rule_: Mapping[str, Any], key: str = "regex"
) -> tuple[re.Pattern | None, list[Finding]]:
    """The compiled ``rule_[key]``, or a finding saying the rule cannot be applied."""
    try:
        return re.compile(str(rule_.get(key, "")), re.M), []
    except re.error as exc:
        return None, [
            ctx.finding(str(rule_["kind"]), 1, f"{key} is not a regular expression: {exc}", rule_)
        ]


def validate(
    dt: DocType, text: str, path: str, *, read: Read | None = None, today: date | None = None
) -> list[Finding]:
    """Every finding of ``dt``'s rules against ``text``, in line order."""
    ctx = Ctx(dt, mdparse.parse(text), path, read, today or clock.now_utc().date())
    out: list[Finding] = []
    if ctx.doc.front_error:
        out.append(Finding("doctype", path, 1, "front_matter", ctx.doc.front_error))
    for r in dt.validate.get("rules", []):
        try:
            kind = RULE_KINDS[r["kind"]]
        except KeyError as exc:
            out.append(Finding("doctype", path, 1, str(r.get("kind")), str(exc)))
            continue
        if kind.check is None:
            out.append(Finding("doctype", path, 1, kind.name, "the rule kind has no check"))
            continue
        out += kind.check(ctx, r)
    return sorted(out, key=lambda f: (f.line, f.ref, f.detail))
