"""Scored rubrics a document type can name: a list of yes/no checks, scored by the share met.

Shipped: ``security-policy`` (after OpenSSF Scorecard: contact, disclosure terms, response
time), ``keep-a-changelog`` and ``madr``. A rubric is a function of the parsed document that
returns ``(check, passed)`` pairs; a new one is registered in `RUBRICS`.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from ...core.registry import Registry
from . import mdparse

Rubric = Callable[[mdparse.Doc], list[tuple[str, bool]]]
RUBRICS: Registry[tuple[str, Rubric]] = Registry("rubric", key=lambda r: r[0])


def _has(doc: mdparse.Doc, rx: str) -> bool:
    return re.search(rx, doc.text, re.I) is not None


def _heading(doc: mdparse.Doc, rx: str) -> bool:
    return any(re.search(rx, h.text, re.I) for h in doc.headings)


def security_policy(doc: mdparse.Doc) -> list[tuple[str, bool]]:
    return [
        ("a contact to report to", _has(doc, r"[\w.+-]+@[\w-]+\.[\w.-]+|https?://\S+")),
        ("disclosure terms", _has(doc, r"disclos|coordinated|responsible")),
        (
            "a response time",
            _has(doc, r"(respon|acknowledg)[^.\n]*\b\d+\s*(business\s+)?(hours?|days?|weeks?)\b"),
        ),
    ]


def keep_a_changelog(doc: mdparse.Doc) -> list[tuple[str, bool]]:
    kinds = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
    return [
        ("an Unreleased section", _heading(doc, r"^\[?unreleased\]?$")),
        (
            "a dated version heading",
            _heading(doc, r"^\[?v?\d+\.\d+(\.\d+)?\S*\]?\s*[-\u2013]\s*\d{4}-\d{2}-\d{2}"),
        ),
        ("change-type subsections", any(_heading(doc, rf"^{k}$") for k in kinds)),
    ]


def madr(doc: mdparse.Doc) -> list[tuple[str, bool]]:
    return [
        ("a title", any(h.level == 1 for h in doc.headings)),
        ("a problem statement", _heading(doc, r"context and problem statement")),
        ("considered options", _heading(doc, r"considered options")),
        ("a decision outcome", _heading(doc, r"decision outcome")),
        ("a status in the front matter", "status" in doc.front),
    ]


for _name, _fn in (
    ("security-policy", security_policy),
    ("keep-a-changelog", keep_a_changelog),
    ("madr", madr),
):
    RUBRICS.register((_name, _fn))
