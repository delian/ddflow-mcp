"""B230e852e10: two clones define the same decision, lesson or research id differently.

Each agent's shard is its own file, so two clones merge cleanly and the fold used to pick
the later (lamport, agent) silently -- and the decision handler mixed FIELDS of the two.
Now a second DIFFERENT definition by another agent is CONTESTED, exactly like a rival
`task.added` (B191): shown whole, reported by doctor, withheld from what is in force, and
settled by `ddflow resolve`. An identical re-add is not a contest.
"""

from __future__ import annotations

import json
import shutil

from tests.conftest import run_cli


def _clone_of(repo, name):
    """A second clone that has seen nothing of what happens in ``repo`` afterwards."""
    dest = repo.parent / name
    shutil.copytree(repo, dest)
    return dest


def _merge_shard(repo, clone, agent):
    """What a git merge does: the clone's shard for ``agent`` arrives in ``repo``."""
    src = clone / ".ddflow" / "events" / f"{agent}.jsonl"
    dst = repo / ".ddflow" / "events" / f"{agent}.jsonl"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def _add_decision(repo, agent, title, text, **extra):
    argv = ["decision", "add", "--id", "D-x", "--title", title, "--decision", text]
    argv += ["--globs", "src/a.py", "--by", "operator"]
    for k, v in extra.items():
        argv += [f"--{k}", v]
    code, out, err = run_cli(repo, *argv, agent=agent)
    assert code == 0, out + err


def _rivals(repo, add):
    """alice and bob each define the same id in a clone that has not seen the other; the
    merged view is ``repo`` with both shards. ``add(clone, agent)`` does the defining."""
    assert run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/a.py")[0] == 0
    clones = {agent: _clone_of(repo, agent) for agent in ("alice", "bob")}
    for agent, clone in clones.items():
        add(clone, agent)
    for agent, clone in clones.items():
        _merge_shard(repo, clone, agent)


def _decisions(repo):
    def add(clone, agent):
        if agent == "alice":
            _add_decision(clone, agent, "use sqlite", "Use sqlite", context="ALICE-CONTEXT")
        else:
            _add_decision(clone, agent, "use postgres", "Use postgres")

    _rivals(repo, add)


def test_rival_decisions_are_contested_not_mixed(repo):
    _decisions(repo)

    code, out, _ = run_cli(repo, "decision", "show", "D-x")
    assert "CONTESTED" in out
    assert "use sqlite" in out and "use postgres" in out
    assert "alice" in out and "bob" in out

    # no field of one definition appears on the other
    _code, out, _ = run_cli(repo, "--json", "decision", "show", "D-x")
    shown = json.loads(out)
    rivals = {d["agent"]: d["data"] for d in shown["contested"]}
    assert rivals["alice"].get("context") == "ALICE-CONTEXT"
    assert not rivals["bob"].get("context")
    # the displayed fields are one rival's whole, never a blend
    assert (shown["title"], shown["context"]) in (
        ("use sqlite", "ALICE-CONTEXT"),
        ("use postgres", ""),
    )

    code, out, _ = run_cli(repo, "decision", "list")
    assert "CONTESTED" in out

    code, out, _ = run_cli(repo, "doctor")
    assert code == 1 and "D-x" in out and "CONTESTED" in out

    # neither is in force: applicable and brief do not present them as rules
    code, out, _ = run_cli(repo, "--json", "decision", "applicable", "T1")
    data = json.loads(out)
    assert not data["applicable"] and not data["project_wide"]
    code, out, _ = run_cli(repo, "brief")
    assert "Use sqlite" not in out and "Use postgres" not in out


def test_resolve_settles_a_decision_contest(repo):
    _decisions(repo)
    code, out, err = run_cli(repo, "resolve", "D-x", "--keep", "bob")
    assert code == 0, out + err
    _code, out, _ = run_cli(repo, "--json", "decision", "show", "D-x")
    shown = json.loads(out)
    assert not shown.get("contested")
    assert shown["title"] == "use postgres" and not shown["context"]
    assert run_cli(repo, "doctor")[0] == 0
    code, out, _ = run_cli(repo, "--json", "decision", "applicable", "T1")
    assert [d["id"] for d in json.loads(out)["applicable"]] == ["D-x"]
    code, out, _ = run_cli(repo, "resolve", "D-x", "--keep", "bob")
    assert code == 3  # not contested any more


def test_identical_readd_and_same_agent_rerecord_are_not_contests(repo):
    def add(clone, agent):
        _add_decision(clone, agent, "use sqlite", "Use sqlite")

    _rivals(repo, add)
    _add_decision(repo, "alice", "use sqlite", "Use sqlite, revised")  # a revision
    _code, out, _ = run_cli(repo, "--json", "decision", "show", "D-x")
    shown = json.loads(out)
    assert not shown.get("contested")
    assert run_cli(repo, "doctor")[0] == 0


def test_rival_lessons_and_research_are_contested(repo):
    def add(clone, agent):
        lock = "always" if agent == "alice" else "never"
        argv = ["lesson", "add", "--id", "L-x", "--title", f"{lock} lock"]
        assert run_cli(clone, *argv, "--rule", f"{lock} take the lock", agent=agent)[0] == 0
        claim = "x is fast" if agent == "alice" else "x is slow"
        code, out, err = run_cli(
            clone,
            "research",
            "add",
            "--id",
            "R-x",
            "--question",
            "is x fast?",
            "--claim",
            claim,
            "--verdict",
            "THEORETICAL",
            "--probe",
            "none",
            agent=agent,
        )
        assert code == 0, out + err

    _rivals(repo, add)
    code, out, _ = run_cli(repo, "doctor")
    assert code == 1 and "L-x" in out and "R-x" in out and "CONTESTED" in out
    # a contested lesson is not advice
    code, out, _ = run_cli(repo, "lesson", "search", "lock")
    assert "take the lock" not in out
    for rid in ("L-x", "R-x"):
        code, out, err = run_cli(repo, "resolve", rid, "--keep", "alice")
        assert code == 0, (rid, out, err)
    assert run_cli(repo, "doctor")[0] == 0
    code, out, _ = run_cli(repo, "lesson", "search", "lock")
    assert "always take the lock" in out and "never take the lock" not in out


def test_revision_by_the_kept_author_after_a_settlement_is_not_a_contest(repo):
    _decisions(repo)
    assert run_cli(repo, "resolve", "D-x", "--keep", "bob")[0] == 0
    _add_decision(repo, "bob", "use postgres", "Use postgres 16")
    _code, out, _ = run_cli(repo, "--json", "decision", "show", "D-x")
    shown = json.loads(out)
    assert not shown.get("contested")
    assert shown["decision"] == "Use postgres 16"
    assert run_cli(repo, "doctor")[0] == 0


def test_revision_by_either_author_of_an_agreed_definition_is_not_a_contest(repo):
    def add(clone, agent):
        _add_decision(clone, agent, "use sqlite", "Use sqlite")

    _rivals(repo, add)
    _add_decision(repo, "bob", "use sqlite", "Use sqlite 3")
    _code, out, _ = run_cli(repo, "--json", "decision", "show", "D-x")
    shown = json.loads(out)
    assert not shown.get("contested")
    assert shown["decision"] == "Use sqlite 3"
    assert run_cli(repo, "doctor")[0] == 0


def test_rivals_that_converge_are_no_longer_a_contest(repo):
    _decisions(repo)
    assert run_cli(repo, "doctor")[0] == 1
    _add_decision(repo, "bob", "use sqlite", "Use sqlite", context="ALICE-CONTEXT")
    _code, out, _ = run_cli(repo, "--json", "decision", "show", "D-x")
    shown = json.loads(out)
    assert not shown.get("contested")
    assert shown["title"] == "use sqlite"
    assert run_cli(repo, "doctor")[0] == 0


def test_a_contested_decision_is_not_exported_as_a_rule(repo):
    _decisions(repo)
    _code, out, _ = run_cli(repo, "export", "rules", "--path", str(repo / "RULES.md"))
    assert "0 decision(s)" in out
    assert run_cli(repo, "resolve", "D-x", "--keep", "bob")[0] == 0
    _code, out, _ = run_cli(repo, "export", "rules", "--path", str(repo / "RULES.md"))
    assert "1 decision(s)" in out
