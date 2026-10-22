"""Bdfc7ed1299 / Be7aea9436a: finishing a PARTIAL round is not a new round, and a finding a
`--chunk` re-run raises is the delta's own.

A full round whose chunk came back off contract was charged, and the retry (a `--chunk` is
refused once the branch moved) was charged again: 6/4. The one retry of a partial full round
is free. A finding first raised by a `--chunk` re-run of a delta was not in `delta_raised`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_review_partial_delta_chunk import _commit, _gate, _review, _setup

BAD = {"a.py": "x = 1", "b.py": "y = 2", "c.py": "z = 3  # BADCHUNK"}


def _full_rounds_with(repo, tmp_path, cap: str) -> Path:
    flaky = _setup(repo, tmp_path, base=BAD)
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(
        cfg.read_text().replace("delta_default = true\nmax_rounds = 0", f"max_rounds = {cap}")
    )
    return flaky


def test_the_retry_of_a_partial_full_round_is_not_charged(repo, tmp_path):
    flaky = _full_rounds_with(repo, tmp_path, "1")
    first = _review(repo)
    assert first.data["outcome"] == "partial", first.reason
    assert _gate(repo).evidence["rounds"] == 1
    flaky.unlink()
    _commit(repo, {"d.py": "w = 4"})  # the branch moved: `--chunk` would be refused
    retry = _review(repo)
    assert retry.data["outcome"] == "passed", retry.reason
    ev = _gate(repo).evidence
    assert ev["retry_of_partial"] and ev["rounds"] == 1, ev


def test_only_one_retry_is_free(repo, tmp_path):
    _full_rounds_with(repo, tmp_path, "1")
    assert _review(repo).data["outcome"] == "partial"
    _commit(repo, {"d.py": "w = 4"})
    assert _review(repo).data["outcome"] == "partial"  # still flaky: the free retry
    _commit(repo, {"e.py": "w = 5"})
    out = _review(repo)
    assert out.exit == 3 and "review rounds" in out.reason, out.reason


def test_a_finding_a_chunk_re_run_raises_is_in_delta_raised(repo, tmp_path):
    flaky = _setup(repo, tmp_path)
    assert _review(repo).data["outcome"] == "passed"
    _commit(repo, {**BAD, "c.py": "z = 3  # BADCHUNK FINDME"})
    assert _review(repo).data["outcome"] == "partial"
    assert _gate(repo).evidence["delta_raised"] == []
    (bad,) = (g["chunk"] for g in _gate(repo).evidence["unreviewed"])
    flaky.unlink()
    out = _review(repo, chunks=[bad])
    assert out.data["outcome"] == "failed", out.reason
    ev = _gate(repo).evidence
    assert [f["digest"] for f in ev["chunk_findings"]] == ev["delta_raised"], ev
