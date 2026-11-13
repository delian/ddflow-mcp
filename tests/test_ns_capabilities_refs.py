"""Regression for B70abe055ed: the NS-capabilities task body cited record ids that
`ddflow show` could not resolve ("no such item or bug").

``R-mode-parity`` noticed the task's body named ``BL-b109b112`` as its storage/git seam and
"Research R-no-shell-audit", but neither id existed anywhere. A dangling reference is
silent stuck work: a reader resolving it finds nothing, exactly like an import that keeps
a dependency on a missing id deliberately unmet. The fix corrects the citation to the real
phase ``BL-b109b112-remote-filesystem-ind`` and files the ``R-no-shell-audit`` note, so
every record id the body names resolves; this lint keeps it so.

The lint is scoped to the task the bug names, not every task body: beyond that one body a
record-id *shaped* token is indistinguishable from ordinary prose (a single-letter ``R-``
prefix is a real research-id kind, but ``R-log``/``R-ids``/``R-req`` in a body are words), so
a queue-wide scan would flag words as citations. The bug is the citation the task makes, and
that citation is what this guards.
"""

from __future__ import annotations

import re
from pathlib import Path

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

_ROOT = Path(__file__).resolve().parents[1]

#: Record-kind prefixes of a cited id in a task body. Deliberately narrow: a citation is
#: an ALL-CAPS kind prefix plus a dash (``BL-``, ``R-``, ``D-``, ``NS-``, ``ST-``, ``DX-``).
#: Prose (e.g. "API-first", "Append-only") is not a citation, and the lowercase ``r-`` rule
#: ids are resolved separately from the queue; both stay out of reach on purpose.
_CITED = re.compile(r"\b(?:BL|R|D|NS|ST|DX)-\w[\w.-]*\b")


def _state():
    # Read-only: cache_writes=False so folding this checkout's own log leaves no snapshot.
    return fold(EventLog(_ROOT, "bg106-test", cache_writes=False).read_all(), strict=False)


def _known_ids(st) -> set[str]:
    return (
        set(st.items)
        | set(st.bugs)
        | set(st.research)
        | set(st.decisions)
        | set(st.lessons)
        | set(st.jobs)
    )


def test_every_record_id_cited_by_ns_capabilities_resolves() -> None:
    st = _state()
    body = st.items["NS-capabilities"].body
    cited = {m.group(0) for m in _CITED.finditer(body)}
    dangling = sorted(cited - _known_ids(st))
    assert dangling == [], (
        "record ids cited in the NS-capabilities body that resolve to nothing "
        f"(`ddflow show <id>` -> no such item or bug): {dangling}"
    )
    # Non-vacuous: the two citations this bug is about are still cited, and each still
    # resolves. The seam now names the full phase id (not the truncated ``BL-b109b112``),
    # and the no-shell audit note exists. A body rewording that dropped either citation
    # would otherwise pass this lint by citing nothing at all.
    assert "BL-b109b112-remote-filesystem-ind" in cited
    assert "R-no-shell-audit" in cited
