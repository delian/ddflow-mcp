import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.services import gates as G

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _run_git(repo, *args) -> None:
    """Commit inside the fixture repo, so a test can create a TRACKED file — the
    fingerprint treats tracked and untracked changes differently, and the difference is
    the whole point of the two tests below."""
    import subprocess

    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def gd(repo, cfg):
    return G.load_gates(repo, cfg)


def item(log):
    log.append("phase.added", "P1", {})
    log.append("task.added", "T1", {"parent": "P1"})
    return fold(log.read_all())


# -- THE regression test for the bug this project's own bug-hunt found -----------------
@pytest.mark.parametrize(
    "command,expected",
    [
        ("definitely-not-a-real-binary-xyzzy --run", "unavailable"),
        ("true && definitely-not-a-real-binary-xyzzy", "unavailable"),
        ("exit 3", "failed"),
        ("exit 127", "failed"),  # a suite MAY exit 127 on purpose
        ("echo ok", "passed"),
        ("sh -c 'exit 1'", "failed"),
    ],
)
def test_a_missing_tool_is_unavailable_not_failed(gd, repo, command, expected):
    """A tool that vanished must never read as a check that ran and failed.

    Found by bug-hunting this module: under `shell=True` a missing binary exits 127,
    which the first implementation classified as `failed`. The consequence is the
    silent inverse of a vacuous pass — a linter nobody installed looks like a linter
    reporting problems, and worse, a *reviewer* that stopped being reachable looks
    like a reviewer with findings. Mutation-verified: reverting the pre-flight in
    `_missing_executable` turns the first two cases red.
    """
    gd["unit_tests"].command = command
    outcome, _ev = G.run_command_gate(gd["unit_tests"], repo)
    assert outcome == expected


def test_a_gate_requiring_evidence_cannot_pass_by_assertion(log, cfg, gd):
    item(log)
    with pytest.raises(ValueError, match="requires evidence"):
        G.record(log, cfg, "T1", "unit_tests", "passed", gates=gd)


def test_unavailable_must_carry_a_reason(log, cfg, gd):
    item(log)
    with pytest.raises(ValueError, match="must carry a --reason"):
        G.record(log, cfg, "T1", "critic", "unavailable", gates=gd)


def test_a_skip_must_be_explained(log, cfg, gd):
    item(log)
    with pytest.raises(ValueError, match="must carry --reason"):
        G.record(log, cfg, "T1", "dedupe", "skipped", gates=gd)
    G.record(log, cfg, "T1", "dedupe", "skipped", reason="no shared code touched", gates=gd)


def test_unavailable_is_not_a_pass_in_status(log, cfg, gd):
    item(log)
    G.record(log, cfg, "T1", "critic", "unavailable", reason="endpoint down", gates=gd)
    st = fold(log.read_all())
    s = G.status(st, cfg, "T1")
    assert "critic" in s.unavailable
    assert "critic" not in s.done
    assert not s.complete


def test_same_family_panel_is_not_independent(log, cfg, gd):
    item(log)
    G.record(
        log, cfg, "T1", "rubber_duck", "passed", evidence={"model": "claude-sonnet-5"}, gates=gd
    )
    G.record(
        log, cfg, "T1", "standards", "passed", evidence={"model": "claude-haiku-4-5"}, gates=gd
    )
    st = fold(log.read_all())
    ok, why = G.reviewer_independence(st, cfg, "T1", "claude-opus-5")
    assert not ok and "same as the author" in why
    G.record(log, cfg, "T1", "critic", "passed", evidence={"model": "gemini-2.5-pro"}, gates=gd)
    st = fold(log.read_all())
    ok, why = G.reviewer_independence(st, cfg, "T1", "claude-opus-5")
    assert ok and "google" in why


def test_no_reviewer_at_all_is_not_independence(log, cfg):
    item(log)
    st = fold(log.read_all())
    ok, why = G.reviewer_independence(st, cfg, "T1", "claude-opus-5")
    assert not ok and "no reviewer ran" in why


def test_gates_overlay_rather_than_replace(repo, cfg):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "gates.toml").write_text('[gate.unit_tests]\ncommand = "pytest -q"\n')
    gates = G.load_gates(repo, cfg)
    assert gates["unit_tests"].command == "pytest -q"
    assert gates["bug_hunt"].prompt, "unmentioned gates must keep their defaults"


def test_an_unknown_gate_field_is_an_error_not_a_silent_drop(repo, cfg):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "gates.toml").write_text('[gate.unit_tests]\ncomand = "typo"\n')
    with pytest.raises(ValueError, match="unknown field"):
        G.load_gates(repo, cfg)


def test_gate_config_is_read_from_config_toml_not_only_gates_toml(repo, cfg):
    """`ddflow configure` writes to config.toml; gates were read only from gates.toml.

    The result was a config write that reported success and changed nothing — the MCP
    `ddflow_configure` tool accepted `[gate.unit_tests]`, wrote it, said "appended",
    and the gate kept its default. Found by the state-aware-instructions test, which
    configured a project and was still told the project was unconfigured.
    Mutation-verified: dropping config.toml from the read list makes this red.
    """
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text(
        '[gate.unit_tests]\ncommand = "pytest -q --tb=short"\n'
    )
    gates = G.load_gates(repo, cfg)
    assert gates["unit_tests"].command == "pytest -q --tb=short"


def test_gates_toml_wins_over_config_toml(repo, cfg):
    """Both are read; the more specific file decides, and neither is silently ignored."""
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text(
        '[gate.unit_tests]\ncommand = "from-config"\ntimeout_s = 111\n'
    )
    (repo / ".ddflow" / "gates.toml").write_text('[gate.unit_tests]\ncommand = "from-gates"\n')
    gates = G.load_gates(repo, cfg)
    assert gates["unit_tests"].command == "from-gates"
    assert gates["unit_tests"].timeout_s == 111, "the config.toml overlay was discarded"


# -- B21: a probe is evidence about the tree it ran on ----------------------------------


def _repo_with_a_command_gate(repo, command="python3 -c 'print(1)'"):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "gates.toml").write_text(
        f'[gate.unit_tests]\ncommand = "{command}"\ncwd = "repo"\n'
    )


def test_the_runner_forbids_bytecode_caching(repo):
    """Same-second, same-size edits leave a valid-looking stale `.pyc`, so the run
    AFTER a patch re-executes the code from BEFORE it — and reports a pass about source
    that is no longer there. The interpreter's own cache invalidation is a timestamp
    and a size, and an agent editing in a loop defeats both by accident."""
    from ddflow.services.gates import GateDef, run_command_gate

    g = GateDef(
        id="probe",
        command="python3 -c \"import os; print(os.environ.get('PYTHONDONTWRITEBYTECODE'))\"",
        cwd="repo",
    )
    _outcome, ev = run_command_gate(g, repo)
    assert "1" in ev["tail"], f"PYTHONDONTWRITEBYTECODE not set for the gate: {ev['tail']!r}"


def test_a_gate_result_records_which_tree_it_ran_on(repo):
    """Evidence with no subject is an assertion. Two agents in two worktrees, or one
    agent editing between gates, and 'the tests passed' stops naming what it passed on."""
    from ddflow.services.gates import GateDef, run_command_gate

    _outcome, ev = run_command_gate(GateDef(id="probe", command="true", cwd="repo"), repo)
    assert ev.get("tree_sha"), f"no tree_sha in the evidence: {sorted(ev)}"


def test_the_tree_sha_changes_when_the_tree_does(repo):
    """Otherwise it is a constant wearing a fingerprint's name."""
    from ddflow.services.gates import GateDef, run_command_gate

    g = GateDef(id="probe", command="true", cwd="repo")
    before = run_command_gate(g, repo)[1]["tree_sha"]
    (repo / "new_file.py").write_text("x = 1\n")
    after = run_command_gate(g, repo)[1]["tree_sha"]
    assert before != after, "an untracked file appeared and the fingerprint did not move"


def test_the_tree_sha_changes_when_an_ALREADY_DIRTY_file_is_edited_again(repo):
    """The case the fingerprint was written for, and the one it could not see.

    `git status --porcelain` is two status letters and a path. No content, no size, no
    mtime. So once a file is modified, every FURTHER edit to that same file produces
    byte-identical porcelain and the digest does not move.

    That is not an edge case here, it is the normal one. At gate time the agent has
    been editing all along, so the tree is ALREADY dirty when the gate records its
    fingerprint — the clean→dirty transition happened before the gate ran. The
    follow-up edit is then most often to a file that is already modified, which is
    precisely `stale_evidence`'s own documented scenario: "run the tests, edit one more
    thing, complete".

    The sibling test above passes by adding an UNTRACKED file, which changes the path
    LIST and so moves the digest for a reason unrelated to content. It proved the case
    that already worked.

    *Found by the cross-family critic on 87d5fbb, CONFIRMED, then reproduced here:
    two writes to one tracked file produced one fingerprint.*
    """
    from ddflow.services.gates import GateDef, run_command_gate, tree_fingerprint

    tracked = repo / "tracked.py"
    tracked.write_text("original\n")
    _run_git(repo, "add", "tracked.py")
    _run_git(repo, "commit", "-m", "add tracked")

    # Dirty it FIRST — this is the state a gate actually runs in.
    tracked.write_text("original\nwork in progress\n")
    g = GateDef(id="probe", command="true", cwd="repo")
    before = run_command_gate(g, repo)[1]["tree_sha"]

    # ...and now "edit one more thing", in the same already-modified file.
    tracked.write_text("original\nwork in progress\nsomething else entirely\n")
    after = run_command_gate(g, repo)[1]["tree_sha"]

    assert before != after, (
        "an already-modified file was edited again and the fingerprint did not move, "
        "so a gate that passed on the old content reads as fresh evidence"
    )
    # and directly, without the gate machinery in between
    assert tree_fingerprint(repo) == after


def test_stale_evidence_reports_a_same_file_re_edit(repo):
    """The consequence, at the level an operator sees.

    A fingerprint that cannot move is only a bug because something depends on it. This
    is that something: `stale_evidence` returned [] for a gate whose source had since
    changed, so `complete` printed no warning and a stale pass was indistinguishable
    from a pass about the shipped code.
    """
    from ddflow.config import Config
    from ddflow.core.model import fold
    from ddflow.infra.log import EventLog
    from ddflow.services.gates import stale_evidence

    run_cli(repo, "init")
    # A COMMAND gate: `unit_tests` ships as an agent gate, and `gate run` on one
    # records no tree_sha at all -- so a version of this test that skipped this setup
    # would have asserted against an empty evidence dict and passed for the wrong
    # reason once the fix landed.
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "true"\ncwd = "repo"\n'
    )
    run_cli(repo, "task", "add", "T1", "--globs", "tracked.py")
    tracked = repo / "tracked.py"
    tracked.write_text("original\n")
    _run_git(repo, "add", "tracked.py")
    _run_git(repo, "commit", "-m", "add tracked")
    tracked.write_text("original\nwip\n")  # dirty BEFORE the gate, as is normal

    assert run_cli(repo, "gate", "run", "T1", "unit_tests")[0] == OK
    st = fold(EventLog(repo).read_all(), strict=False)
    cfg = Config.load(repo)
    assert stale_evidence(st, cfg, "T1", repo) == [], "reported stale before anything changed"

    tracked.write_text("original\nwip\nand more\n")
    st = fold(EventLog(repo).read_all(), strict=False)
    assert "unit_tests" in stale_evidence(st, cfg, "T1", repo), (
        "the file the gate tested was edited after it passed and nothing said so"
    )


def test_the_tree_sha_changes_when_an_UNTRACKED_file_is_rewritten(repo):
    """The other half of the same hole, and the commoner one.

    `git diff HEAD` never shows untracked content and porcelain shows only `?? path`,
    so a brand new module — untracked until its first commit, which is the ORDINARY
    state of agent work — could be rewritten completely between the gate and the
    completion with the fingerprint unmoved.

    The B87 fix closed the tracked case and left this one, and the filed limit
    (binaries) did not cover it. *Found by roborev on 0e23b31, CONFIRMED by git
    semantics, reproduced before fixing.*
    """
    from ddflow.services.gates import tree_fingerprint

    new_module = repo / "feature.py"
    new_module.write_text("def a(): pass\n")  # never `git add`ed
    before = tree_fingerprint(repo)
    new_module.write_text("def a(): return 'completely rewritten'\n")
    assert tree_fingerprint(repo) != before, (
        "an untracked file was rewritten and the fingerprint did not move"
    )


def test_ddflows_OWN_bookkeeping_does_not_move_the_fingerprint(repo):
    """Recording a gate outcome must not invalidate the gate it just recorded.

    `.ddflow/` holds the event log, and appending to it is what taking a gate's outcome
    DOES. Hashing untracked content without excluding it made the fingerprint move as a
    direct consequence of recording — so `complete` warned "passed on a different tree"
    after every single gate, on the ordinary path rather than an edge case. A warning
    that always fires is one nobody reads, which is how this check gets switched off.

    Introduced while fixing the untracked-file hole and caught by
    `test_it_stays_quiet_when_nothing_moved`, which is why that test exists.
    """
    from ddflow.services.gates import tree_fingerprint

    before = tree_fingerprint(repo)
    run_cli(repo, "task", "add", "FPT1", "--globs", "fp.py")
    run_cli(repo, "lesson", "add", "--title", "wrote several events")
    assert tree_fingerprint(repo) == before, (
        "ddflow writing its own events changed the fingerprint of the project's source"
    )


def test_fingerprinting_writes_nothing_to_the_object_store(repo):
    """`git hash-object` WITHOUT `-w`. A function whose job is to observe must not
    change what it observes, and the original docstring rejected `write-tree` for
    exactly this reason — hashing untracked content must not smuggle it back."""
    import subprocess

    from ddflow.services.gates import tree_fingerprint

    def count() -> str:
        out = subprocess.run(
            ["git", "-C", str(repo), "count-objects", "-v"], capture_output=True, text=True
        )
        return out.stdout

    (repo / "untracked.py").write_text("x = 1\n")
    before = count()
    tree_fingerprint(repo)
    assert count() == before, "fingerprinting wrote objects into the repository"


def test_a_flood_of_untracked_files_degrades_LOUDLY_rather_than_silently(repo, monkeypatch):
    """Above the cap it hashes names instead of content — and says so IN the digest.

    A fingerprint that quietly stopped covering content would make `stale_evidence` go
    silent for exactly the repositories where it matters most, and nothing would
    indicate that the guarantee had weakened.
    """
    from ddflow.services import gates as G

    monkeypatch.setattr(G, "MAX_UNTRACKED_HASHED", 2)
    for i in range(5):
        (repo / f"scratch{i}.py").write_text(f"x = {i}\n")
    assert "names-only:5" in G._untracked_digest(repo)


def test_an_unchanged_tree_keeps_the_same_sha(repo):
    """The other half: a fingerprint that changes every call identifies nothing."""
    from ddflow.services.gates import GateDef, run_command_gate

    g = GateDef(id="probe", command="true", cwd="repo")
    assert run_command_gate(g, repo)[1]["tree_sha"] == run_command_gate(g, repo)[1]["tree_sha"]


def test_completing_warns_when_a_passing_gate_ran_on_a_different_tree(repo):
    """The ordinary way it happens: run the tests, edit one more thing, complete.

    The recorded pass is then true about source nobody is shipping — and in the log it
    is indistinguishable from a pass about the code that shipped. A warning, not a
    block: the evidence is real, and refusing on a comment-sized change is how a check
    gets turned off.
    """
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "true"\ncwd = "repo"\n'
    )
    run_cli(repo, "workflow", "pipeline", "task", "implement,unit_tests,merge")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree")
    assert run_cli(repo, "gate", "run", "T1", "unit_tests")[0] == OK
    run_cli(repo, "gate", "record", "T1", "implement", "--outcome", "passed")
    run_cli(repo, "gate", "record", "T1", "merge", "--outcome", "passed")

    (repo / "a.py").write_text("changed after the tests ran\n")
    _code, _out, err = run_cli(repo, "complete", "T1")
    assert "different tree" in err, err


def test_it_stays_quiet_when_nothing_moved(repo):
    """A warning that always fires is one nobody reads."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "true"\ncwd = "repo"\n'
    )
    run_cli(repo, "workflow", "pipeline", "task", "implement,unit_tests,merge")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree")
    run_cli(repo, "gate", "run", "T1", "unit_tests")
    run_cli(repo, "gate", "record", "T1", "implement", "--outcome", "passed")
    run_cli(repo, "gate", "record", "T1", "merge", "--outcome", "passed")
    _code, _out, err = run_cli(repo, "complete", "T1")
    assert "different tree" not in err, err


# -- how much the gate was looking at ---------------------------------------------------


def test_a_command_gate_records_how_much_had_changed(repo):
    """`tree_sha` answers "which tree" and is opaque. This answers "how big", which is
    what makes a recorded pass auditable later: a review gate that passed over 4,000
    changed lines in two minutes is a different claim from one that passed over 12, and
    without this the log cannot tell them apart."""
    from ddflow.services.gates import GateDef, run_command_gate

    tracked = repo / "tracked.py"
    tracked.write_text("a\nb\nc\n")
    _run_git(repo, "add", "tracked.py")
    _run_git(repo, "commit", "-m", "add tracked")
    tracked.write_text("a\nb\nc\nd\ne\n")
    (repo / "brand_new.py").write_text("x = 1\n")

    _outcome, ev = run_command_gate(GateDef(id="probe", command="true", cwd="repo"), repo)
    stat = ev["diff_stat"]
    assert stat["files"] == 1, stat
    assert stat["insertions"] == 2, stat
    assert stat["untracked"] == 1, stat


def test_the_diff_stat_ignores_ddflows_own_events(repo):
    """Same exclusion as the fingerprint, for the same reason: a number that grows every
    time ddflow records an event describes ddflow's bookkeeping, not the work."""
    from ddflow.services.gates import diff_stat

    before = diff_stat(repo)
    run_cli(repo, "task", "add", "DS1", "--globs", "ds.py")
    run_cli(repo, "lesson", "add", "--title", "several more events")
    assert diff_stat(repo) == before


def test_the_diff_stat_keys_always_exist(tmp_path):
    """Outside a repository it returns zeros rather than raising — but the KEYS are
    there, so a reader never has to tell "no change" apart from "this field did not
    exist in the version that wrote the event"."""
    from ddflow.services.gates import diff_stat

    stat = diff_stat(tmp_path)
    assert set(stat) == {"files", "insertions", "deletions", "untracked"}
    assert all(v == 0 for v in stat.values())
