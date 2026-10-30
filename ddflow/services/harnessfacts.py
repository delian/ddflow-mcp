"""Descriptor facts with provenance: the `[facts]` overlay and the generated facts table.

A descriptor value stays where it is (`[headless] argv = ...`). Its provenance goes in
`[facts."headless.argv"]` as the shared record of `facts` (`s`, `src`, `d`, `ver?`, `note?`;
no `v`, the value is the descriptor's own). A value with no overlay reads as `unverified`
with the `[verified]` source and date: a lead, never believed. A section that says
`none = true` reads as status `none`. Absent sections are NOT VERIFIED and are listed only
on request. A key the file wrote (`given`) is a fact even when it is 0 or false; a falsy default
is not, unless an overlay names it. No probing here; staleness by version is `P-agent-parity.drift`.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any

from . import facts
from .harnessv2 import RESERVED_SECTIONS, DescriptorError

_NOT_SECTIONS = frozenset(
    {"id", "rank", "family", "delta", "title", "mcp", "verified", "facts", "reserved"}
)
ABSENT = "-"


def parse_overlay(raw: Any, where: str) -> dict[str, dict[str, Any]]:
    """`[facts]` as read from TOML: a table of fact tables, each with the shared fields."""
    if not isinstance(raw, dict):
        raise DescriptorError(f"{where}[facts]: expected a table")
    for key, node in raw.items():
        if not isinstance(node, dict):
            raise DescriptorError(f"{where}[facts].{key}: expected a table")
        if "v" in node:
            raise DescriptorError(f"{where}[facts].{key}: the value lives in the descriptor")
        bad = facts.problems(f"{where}[facts].{key}", node)
        if bad:
            raise DescriptorError("; ".join(bad))
    return {k: dict(v) for k, v in raw.items()}


def given_keys(raw: dict[str, Any]) -> tuple[str, ...]:
    """`section.key` for each key a descriptor file wrote, so an explicit 0 or false is a
    fact and a dataclass default is not."""
    return tuple(
        sorted(
            f"{name}.{k}"
            for name, table in raw.items()
            if isinstance(table, dict) and name not in _NOT_SECTIONS
            for k in table
            if k != "none"
        )
    )


def _show(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items())
    if isinstance(value, (tuple, list)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _present(h: Any) -> dict[str, tuple[Any, bool]]:
    """`{path: (value, section_is_none)}` for every value the descriptor gives, and
    `{section: ("none", True)}` for a documented absence."""
    out: dict[str, tuple[Any, bool]] = {}
    for f in fields(h):
        sec = getattr(h, f.name)
        if f.name in _NOT_SECTIONS or sec is None or not is_dataclass(sec):
            continue
        if getattr(sec, "none", False):
            out[f.name] = ("none", True)
            continue
        for sf in fields(sec):
            v = getattr(sec, sf.name)
            if sf.name != "none" and (v or f"{f.name}.{sf.name}" in h.given):
                out[f"{f.name}.{sf.name}"] = (v, False)
    for name, res in h.reserved.items():
        if res.none:
            out[name] = ("none", True)
            continue
        for k, v in res.values.items():
            out[f"{name}.{k}"] = (v, False)
    return out


def _known(h: Any) -> set[str]:
    """Every path an overlay may name: values given, plus any field of a present section."""
    known = set(_present(h))
    for f in fields(h):
        sec = getattr(h, f.name)
        if f.name not in _NOT_SECTIONS and sec is not None and is_dataclass(sec):
            known.add(f.name)
            known |= {f"{f.name}.{sf.name}" for sf in fields(sec) if sf.name != "none"}
    known |= set(h.reserved) | {f"{n}.{k}" for n, r in h.reserved.items() for k in r.values}
    return known


def _value(h: Any, key: str, present: dict[str, tuple[Any, bool]]) -> Any:
    """The descriptor's value for `key` (None when it has none), falsy values included."""
    if key in present:
        return present[key][0]
    sec, _, name = key.partition(".")
    return getattr(getattr(h, sec, None), name, None) if name else None


def validate(h: Any, where: str) -> None:
    """Each overlay entry names a fact of this descriptor and its status fits the value."""
    present = _present(h)
    known = _known(h)  # a falsy value is still named by an overlay
    for key, node in h.facts.items():
        if key not in known:
            raise DescriptorError(f"{where}: [facts] {key!r}: no such fact in this descriptor")
        is_none = present.get(key, (None, False))[1]
        if node["s"] == "none" and not is_none:
            raise DescriptorError(f"{where}: [facts] {key!r}: status none needs none = true")
        sec, _, name = key.partition(".")
        absent = present.get(sec, (None, False))[1] and name  # the section says none = true
        if node["s"] in facts.READABLE and (is_none or absent or _value(h, key, present) is None):
            raise DescriptorError(f"{where}: [facts] {key!r}: status {node['s']} needs a value")


def _row(h: Any, key: str, value: str, default: str, node: dict[str, Any] | None) -> dict:
    node = node or {}
    return {
        "agent": h.id,
        "fact": key,
        "value": value,
        "status": node.get("s", default),
        "source": node.get("src", h.verified.sources[0]),
        "date": node.get("d", h.verified.date),
        "tool_version": node.get("ver", ""),
        "note": node.get("note", ""),
    }


def rows(h: Any, *, absent: bool = False) -> list[dict[str, str]]:
    """The table of one descriptor; `absent` adds each section left out (NOT VERIFIED)."""
    have = _present(h)
    for key in h.facts:  # an overlay names a falsy value: it is a fact the author meant
        if key not in have and "." in key:
            if (
                getattr(h, key.partition(".")[0], None) is not None
                and not (have.get(key.partition(".")[0], (0, False))[1])
            ):  # a section that says none = true has no values to name
                val = _value(h, key, have)
                have[key] = (val if val is not None else "", False)
    out = [
        _row(h, key, _show(value), "none" if is_none else "unverified", h.facts.get(key))
        for key, (value, is_none) in sorted(have.items())
    ]
    if absent:
        left = [f.name for f in fields(h) if f.name not in _NOT_SECTIONS]
        left = [n for n in left if getattr(h, n) is None]
        left += [n for n in RESERVED_SECTIONS if n not in h.reserved]
        out += [_row(h, n, ABSENT, "unverified", h.facts.get(n)) for n in sorted(left)]
    return out


def table(rs: list[dict[str, str]]) -> str:
    """The rows as one markdown table."""
    head = "| agent | fact | value | status | source | date | version | note |"

    def cell(text: str) -> str:
        return " ".join(text.replace("|", "/").split())

    body = [
        "| "
        + " | ".join(
            cell(r[k] or "-")
            for k in ("agent", "fact", "value", "status", "source", "date", "tool_version", "note")
        )
        + " |"
        for r in rs
    ]
    return "\n".join([head, "|" + "---|" * 8, *body])
