"""What makes an exported document safe to commit and to read (D-export 5).

Redaction
    ``[export].redact`` (default true; ``[export.<doc>].redact`` overrides) runs
    ``services.redact_report`` over the RENDERED body, before it is truncated, framed or
    digested, so the header digest and ``--check`` both cover the redacted bytes. The names
    redacted as words are ``[upstream].redact_extra`` when that section exists; the project's
    own name is NOT one (it is the project's own public document). Secrets use the
    session patterns of the loaded config. The header says what happened:
    ``redacted=N redacted-kinds=ipv4:1,path:2`` or ``redacted=off``.

Append mode
    Only the entries are redacted; an append-mode header carries no count (it would have to
    be cumulative across writes).

Fencing
    Over MCP the whole printed document is wrapped in one ``core.provenance`` fence: its
    text was written by agents, so a reader is told it is data, by whom and from where.
    A file written for humans stays plain markdown.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...config import Config
from ...core import provenance
from ...core.redact import Redacted, Redactor, names_for  # noqa: F401 -- names_for re-exported

_MAX_AUTHORS = 5


def redact_text(text: str, cfg: Config) -> Redacted:
    """``text`` with secrets, private addresses, hosts and home paths removed.

    ``repo_root`` is passed empty on purpose: the repository's directory name is the
    project's own and stays; absolute home paths still go.
    """
    return Redactor("export", cfg).text(text)


def redaction_attrs(counts: Mapping[str, int] | None) -> dict[str, str]:
    """Header attributes for a redaction result: ``None`` means it was switched off."""
    if counts is None:
        return {"redacted": "off"}
    out = {"redacted": str(sum(counts.values()))}
    if counts:
        out["redacted-kinds"] = ",".join(f"{k}:{counts[k]}" for k in sorted(counts))
    return out


def authors(events: Any) -> str:
    """The distinct agents that wrote the log, as one short attribute value."""
    names = sorted({str(e.agent) for e in events if getattr(e, "agent", "")})
    if len(names) > _MAX_AUTHORS:
        return f"{len(names)} agents"
    return ",".join(names)


def fence_document(doc: str, text: str, by: str) -> str:
    """The printed document as DATA, with its authors (``by``, from ``authors``) and where it
    came from."""
    o = provenance.Origin(provenance.AGENT, by, f"ddflow export {doc}")
    return provenance.fence("export", doc, text, o, inline=False)


def fence_overhead(doc: str, by: str) -> int:
    """Bytes ``fence_document`` adds around an EMPTY text; escaping tag-like text adds more."""
    return len(fence_document(doc, "", by).encode("utf-8"))
