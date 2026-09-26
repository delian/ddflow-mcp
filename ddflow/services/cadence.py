"""The lessons-compression cadence rule.

Derived from the log like every other cadence: the trigger is how much the lessons
corpus has grown since the last recorded compression, so there is no state file to
drift out of step with reality.

In `services/` rather than `surfaces/` because `api/operations.py` computes the due list
and may not import a surface to finish it. It was briefly put in `surfaces/` during the
B36 extraction and `test_a_module_imports_only_from_below` refused it on the spot, which
is the layer check doing exactly what it is for.
"""

from __future__ import annotations

import json
from typing import Any


def lessons_cadence(st, cfg) -> list[dict[str, Any]]:
    """Is a lessons-compression pass due?

    Measured as GROWTH since the last recorded pass, not as an absolute size. An
    absolute threshold fires forever once crossed -- including immediately after a pass
    that just correctly compressed the corpus -- which trains everyone to ignore it.
    Growth goes quiet when the work is done, which is the only behaviour that keeps a
    cadence trigger credible.

    Both halves must clear: total bytes AND bytes-per-entry. Dividing by entry count
    alone would fire on a MERGE (fewer entries, same bytes => per-entry rises), i.e. on
    exactly the action the cadence exists to produce.
    """
    live = [x for x in st.lessons.values() if not x.superseded_by]
    if len(live) < cfg.lessons.cadence_min_entries:
        return []
    runs = st.cadences.get("lessons_compression", [])
    now_bytes = sum(len(x.text().encode("utf-8")) for x in live)
    now_per = now_bytes / max(1, len(live))
    if not runs:
        return [
            {
                "cadence": "lessons_compression",
                "since": len(live),
                "every": 0,
                "unit": "entries (no baseline recorded yet)",
            }
        ]
    try:
        was = json.loads(runs[-1].get("result") or "{}")
        was_bytes, was_entries = float(was["bytes"]), int(was["entries"])
    except (ValueError, KeyError, TypeError):
        return []
    was_per = was_bytes / max(1, was_entries)
    grow_bytes = (now_bytes - was_bytes) / max(1.0, was_bytes) * 100
    grow_per = (now_per - was_per) / max(1e-9, was_per) * 100
    thresh = cfg.lessons.cadence_growth_pct
    if grow_bytes >= thresh and grow_per >= thresh:
        return [
            {
                "cadence": "lessons_compression",
                "since": round(grow_per, 1),
                "every": thresh,
                "unit": f"% growth per entry (total +{grow_bytes:.1f}%)",
            }
        ]
    return []
