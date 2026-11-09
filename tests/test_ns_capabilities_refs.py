"""Regression for B70abe055ed: the NS-capabilities task body cited record ids that
`ddflow show` could not resolve ("no such item or bug").

``R-mode-parity`` noticed the task's body named ``BL-b109b112`` as its storage/git seam and
"Research R-no-shell-audit", but neither id existed anywhere. A dangling reference is
silent stuck work: a reader resolving it finds nothing, exactly like an import that keeps
a dependency on a missing id deliberately unmet. The fix corrects the citation to the real
phase ``BL-b109b112-remote-filesystem-ind`` and files (or corrects) the ``R-no-shell-audit``
note, so every record id the body names resolves; this lint keeps it so.
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
    cited = {m.group(0) for m in _CITED.finditer(st.items["NS-capabilities"].body)}
    dangling = sorted(cited - _known_ids(st))
    assert dangling == [], (
        "record ids cited in the NS-capabilities body that resolve to nothing "
        f"(`ddflow show <id>` -> no such item or bug): {dangling}"
    )
