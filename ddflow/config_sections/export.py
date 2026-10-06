"""The `[export]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._docs import _doc

#: What `[export].refresh` accepts: when a selected document regenerates by itself
#: (`services/export/refresh.py`). `off` never writes.
EXPORT_REFRESH_MODES = ("off", "merge", "phase_close", "docs_gate")

#: Where each document kind is written when `[export.<doc>].path` does not say. A copy of
#: each kind's `DocKind.default_target`, kept here because `core` (the lease scheduler,
#: which registers every selected target as a shared path) may not import `services`;
#: tests/test_export_ops.py asserts the two tables agree.
EXPORT_DEFAULT_TARGETS: dict[str, str] = {
    "bugs": "BUGS.md",
    "changelog": "CHANGELOG.md",
    "decisions": "DECISIONS.md",
    "roadmap": "ROADMAP.md",
    "rules": "RULES.md",
    "sessions": "SESSION.md",
    "status": "STATUS.md",
    "worklog": "LOG.md",
}


@dataclass
class ExportConfig:
    """`ddflow export`: which documents are kept, where, and how (decisions D-export*)."""

    documents: list[str] = field(default_factory=list)
    redact: bool = True
    max_bytes: int = 60_000
    refresh: str = "off"
    #: The per-document tables, `[export.<doc>]`: path, mode, template, filters, refresh,
    #: redact. Filled from those tables by `Config._apply`; this field is only the store.
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)

    def table(self, doc: str) -> dict[str, Any]:
        return self.tables.get(doc, {})

    def targets(self) -> list[tuple[str, str, str]]:
        """`(doc, repo-relative path, mode)` of every selected document, selection order.
        A document with no table and no default target (an unknown kind) has none."""
        out = []
        for doc in self.documents:
            t = self.table(doc)
            path = str(t.get("path") or EXPORT_DEFAULT_TARGETS.get(doc, ""))
            if path:
                out.append((doc, path, str(t.get("mode") or "whole")))
        return out


_doc(
    "export",
    "documents",
    "The documents `ddflow export --all` writes and keeps (roadmap, bugs, status, worklog, sessions, decisions, rules, changelog). EMPTY by default: nothing is generated unless selected. `ddflow export` lists every kind with its state; any kind can still be printed or written once on demand whether or not it is listed here. Each selected target is a shared path for leases (no claim is needed to regenerate it).",
)
_doc(
    "export",
    "redact",
    "Strip private addresses and credentials from exported documents (they are public-repo files at the repo root). ON by default: the rendered body is redacted before it is digested, so the header digest and `export --check` cover the redacted text, and the header says `redacted=N`. Secrets, private addresses and hosts, home paths, emails and the machine hostname become [REDACTED:<kind>]; the project's own name is kept. [export.<doc>].redact overrides one document.",
)
_doc(
    "export",
    "max_bytes",
    "Size cap for a document printed to stdout or returned over MCP, in bytes; a cut document ends in an explicit [truncated: N more] footer. 0 = no cap. A file written by --update or --out is never capped. `--max-bytes` overrides per call.",
)
_doc(
    "export",
    "refresh",
    "When selected documents regenerate by themselves: off | merge | phase_close | docs_gate. `merge` regenerates into the item's merge, `phase_close` at phase completion, `docs_gate` at the phase docs gate; `off` never writes. Per document: [export.<doc>].refresh wins.",
)
_doc(
    "export",
    "tables",
    "The per-document tables as one map; write them as [export.<doc>] tables instead, with keys path (repo-relative target), mode (whole | region | append), template (path of a Jinja2 template), filters ({since, limit, status, phase, session, tag}), refresh and redact. Unknown keys in an [export.<doc>] table are skipped with a warning (and ignored in this map form), so a newer release's keys do not stop an older checkout.",
)

#: Keys an `[export.<doc>]` table understands.
EXPORT_TABLE_KEYS = frozenset({"path", "mode", "template", "filters", "refresh", "redact"})
EXPORT_MODES = ("whole", "region", "append")


def _export_table_problem(doc: str, t: Any) -> str:
    """Why `[export.<doc>]` is wrong, or ""."""
    if not isinstance(t, dict):
        return f"[export.{doc}] must be a table"
    for k in ("path", "mode", "template", "refresh"):
        if k in t and not isinstance(t[k], str):
            return f"[export.{doc}].{k} must be a string"
    if "mode" in t and t["mode"] not in EXPORT_MODES:
        return f"[export.{doc}].mode must be one of {', '.join(EXPORT_MODES)}"
    if "refresh" in t and t["refresh"] not in EXPORT_REFRESH_MODES:
        return f"[export.{doc}].refresh must be one of {', '.join(EXPORT_REFRESH_MODES)}"
    if "redact" in t and not isinstance(t["redact"], bool):
        return f"[export.{doc}].redact must be true or false"
    if "filters" in t and not (
        isinstance(t["filters"], dict)
        and all(
            isinstance(k, str)
            and (isinstance(v, str) or (isinstance(v, int) and not isinstance(v, bool)))
            for k, v in t["filters"].items()
        )
    ):
        return f"[export.{doc}].filters must be a table of strings and integers"
    return ""
