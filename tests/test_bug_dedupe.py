"""`ddflow bug dedupe`: cluster near-duplicate OPEN bugs by symptom + root cause.

Two signals, checked against a small acceptance set (DX-bug-dedupe-sweep):

* TWO reports of ONE defect cluster whatever the wording — paraphrases that keep the
  root-cause content words score high on the TF-IDF overlap alone, and paraphrases that
  share NO word of the other cluster on the semantic signal when an embedder is present;
* TWO genuinely distinct defects do NOT cluster;
* `--apply` links only the real duplicates, each younger one onto its cluster's
  canonical (oldest) member — never closing anything.

The engine is `ddflow.services.bugsim`; the command is `ddflow/api.knowledge.bug_dedupe`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import bugsim

#: One defect, in two wordings that share its root-cause vocabulary but no whole sentence.
MERGE_DEADLOCK_A = "the merge queue deadlocks when two tasks wait on each other's file lease"
MERGE_DEADLOCK_B = "a circular wait for a file lease between tasks blocks the merge queue"
#: A genuinely different defect.
DOC_KNOB = "the README still documents a config knob that was removed two releases ago"

FLOOR = 0.5


def _recs(*pairs: tuple[str, str]) -> list[dict]:
    return [
        {"id": bid, "kind": "bug", "title": "", "body": body, "item": ""} for bid, body in pairs
    ]


# -- the engine, pure --------------------------------------------------------------------


def test_two_paraphrased_descriptions_of_one_cause_cluster():
    """One defect in two wordings clusters on the overlap signal alone (same root-cause
    content words, no matching sentence), so the sweep works with no embedder configured."""
    records = _recs(("B1", MERGE_DEADLOCK_A), ("B2", MERGE_DEADLOCK_B))
    groups, _scores = bugsim.cluster(records, floor=FLOOR)
    assert ["B1", "B2"] in groups


def test_two_genuinely_distinct_causes_do_not_cluster():
    records = _recs(
        ("B1", MERGE_DEADLOCK_A),
        ("B2", "the README still documents a config knob that was removed two releases ago"),
    )
    assert bugsim.cluster(records, floor=FLOOR)[0] == []


def test_the_semantic_signal_clusters_disjoint_paraphrases():
    """Two paraphrases that share NO content word cluster only when an embedder is present:
    the vectors stand in for `[rag]`, which is a machine-local knob. Without them the
    overlap matcher rightly finds nothing (the shared meaning is in no shared word)."""
    a = ("B1", "a claimed item never expires so no other agent can take it")
    b = ("B2", "an agent's lease is held forever and the work is stranded")
    distinct = ("B3", "the README still documents a config knob that was removed")
    records = _recs(a, b, distinct)
    assert bugsim.cluster(records, floor=FLOOR)[0] == [], (
        "a word-disjoint paraphrase matched overlap"
    )
    # B1 ~ B2 are the same meaning (cosine 1.0); B3 is orthogonal to both.
    vectors = [(1.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    groups, _scores = bugsim.cluster(records, floor=FLOOR, vectors=vectors)
    assert ["B1", "B2"] in groups
    assert not any("B3" in g for g in groups)


# -- the command, end to end --------------------------------------------------------


def _file_two(repo: Path) -> None:
    run_cli(repo, "init")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", MERGE_DEADLOCK_A, "--no-task")
    run_cli(repo, "bug", "found", "--id", "B2", "--summary", MERGE_DEADLOCK_B, "--no-task")


def test_bug_dedupe_reports_a_cluster_reached_by_the_command(repo):
    _file_two(repo)
    code, out, err = run_cli(repo, "bug", "dedupe")
    assert code == 0, err
    assert "B1" in out and "B2" in out


def test_bug_dedupe_finds_nothing_when_no_open_bugs_cluster(repo):
    run_cli(repo, "init")
    run_cli(
        repo,
        "bug",
        "found",
        "--id",
        "B1",
        "--summary",
        MERGE_DEADLOCK_A,
        "--no-task",
    )
    run_cli(repo, "bug", "found", "--id", "B2", "--summary", DOC_KNOB, "--no-task")
    assert run_cli(repo, "bug", "dedupe")[0] == 2


def test_apply_links_only_the_real_duplicates_onto_the_canonical(repo):
    _file_two(repo)
    run_cli(repo, "bug", "found", "--id", "B3", "--summary", DOC_KNOB, "--no-task")
    code, out, err = run_cli(repo, "bug", "dedupe", "--apply")
    assert code == 0, err
    st = fold(EventLog(repo).read_all())
    dupes = st.links["B2"].linked(relation="duplicate_of")
    assert "B1" in dupes, f"the younger duplicate was not linked onto B1: {out}"
    assert st.bugs["B2"].open, "--apply closed a duplicate (it must never auto-close)"
    assert st.bugs["B1"].open, "--apply closed the canonical bug"
    b3 = st.links.get("B3")
    assert b3 is None or b3.linked() == set(), "a distinct bug was linked as a duplicate"
    # The link settles the pair for `ddflow dupes` still (nothing is closed here).
    assert run_cli(repo, "dupes")[0] == 2


def test_apply_is_idempotent_a_re_run_offers_nothing(repo):
    """The settled pair is never re-offered or re-linked (the finding a bare `--apply`
    re-link would hide): a second sweep reports nothing once the duplicate is linked."""
    _file_two(repo)
    assert run_cli(repo, "bug", "dedupe", "--apply")[0] == 0
    assert run_cli(repo, "bug", "dedupe")[0] == 2
