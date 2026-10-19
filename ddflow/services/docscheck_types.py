"""The documentation check's result types: a finding, and the report that lists them.

Split from ``docscheck`` (rule r-small-files) so every family module can build findings
without importing the checker.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Finding kinds, in the order a report lists them.
KINDS = ("identifier", "flag", "command", "link", "anchor", "count", "doctype", "lint")


@dataclass(frozen=True)
class Finding:
    kind: str  #: one of KINDS, or "path" for a note
    path: str  #: the document
    line: int
    ref: str  #: what the document names
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "path": self.path,
            "line": self.line,
            "ref": self.ref,
            "detail": self.detail,
        }


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    notes: list[Finding] = field(default_factory=list)
    #: What was looked at: the documents and how many references of each kind were checked.
    checked: dict = field(default_factory=dict)
    digest: str = ""

    @property
    def clean(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict:
        return {
            "findings": [f.as_dict() for f in self.findings],
            "notes": [n.as_dict() for n in self.notes],
            "checked": self.checked,
            "digest": self.digest,
        }
