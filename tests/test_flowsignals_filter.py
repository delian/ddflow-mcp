"""`_review_samples` filters before it sorts (B-gap-log-scale) and gives what sorting everything did."""

from __future__ import annotations

from logscale_helpers import _real_copy, any_log  # noqa: F401  (fixtures)

from ddflow.core import flowsignals as FS
from ddflow.core import progress as PR


def _oracle(events, now):
    """The function as it was: sort the whole log, then skip what is not a review outcome."""
    out, seen = [], set()
    for ev in sorted(events, key=lambda e: (PR.epoch(e.ts), e.lamport)):
        d = ev.data if isinstance(ev.data, dict) else {}
        evidence = d.get("evidence")
        if (
            FS._gate(ev) not in PR.REVIEW_GATES
            or not FS._outcome(ev)
            or not isinstance(evidence, dict)
        ):
            continue
        took, chunks = evidence.get("elapsed_s"), evidence.get("chunks_total") or 1
        end = PR.epoch(ev.ts)
        if not isinstance(took, int | float) or isinstance(took, bool) or took <= 0:
            continue
        if end <= 0 or end > now:
            continue
        same = (
            evidence.get("output_file")
            or evidence.get("output_digest")
            or (evidence.get("reviewed_head"), evidence.get("diff_sha"))
        )
        if same == (None, None):
            same = ev.lamport
        once = (ev.subject, FS._gate(ev), same, took)
        if once in seen:
            continue
        seen.add(once)
        waves = evidence.get("waves")
        if not isinstance(waves, int) or isinstance(waves, bool) or waves < 1:
            waves = max(1, -(-int(chunks) // FS.WAVE_CHUNKS)) if isinstance(chunks, int) else 1
        out.append((end - took, end, took / waves))
    return out


def test_filtering_first_gives_the_same_samples(any_log):  # noqa: F811
    events = any_log.read_all()
    for now in (1e12, 1.79e9 + 40000):
        assert FS._review_samples(events, now) == _oracle(events, now)
