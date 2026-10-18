"""B4067086b6b / D-test-economy-r2: a precise selection must keep recall.

Replays the last SCAN merges of THIS repository from git history (Bb5b8b17161: four were too
few, a merge's miss left the window before anyone saw it, and the test failed on main two
merges later). For each, the tests the
merge changed alongside its source are the ones its author had to touch -- a regression
test, or a test the change broke -- and the selection computed from the SOURCE change alone
(over the tree as it was at the merge) must hold them, bounded by the project's defaults.
Two floors: nothing the earlier, unbounded selector found is lost, and the absolute recall
stays high. A merge whose answer is the whole suite (a hub changed) has perfect recall.

Bounded (`SCAN` merges, a few processes, no network) and skipped when git history is not there: a
shallow clone, a tarball, or `DDFLOW_SKIP_HISTORY_REPLAY=1`.
"""

from __future__ import annotations

import ast
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers import git_proc, git_raw

from ddflow.config import Config
from ddflow.services import testidents as TI
from ddflow.services import testscope as TSC
from ddflow.services import testselect as T

ROOT = Path(__file__).resolve().parents[1]
SCAN = 40  #: the newest merges looked at (`git log -n`: the bound); those with source and tests in them are replayed, about seven seconds each
WORKERS = 4  #: processes replaying at once
#: Merges whose co-changed test only the hub cut loses, and nothing a test spells reaches: the
#: earlier selector held it through ``ddflow.config`` (a hub of 100+ importers), by luck of an
#: unrelated knob file changing alongside (B81e0e941df). Pinned by sha and path; not a recall
#: miss to fix, a route the bound gave up on purpose.
UNROUTABLE = {
    "af8410da tests/test_tallies.py": "drives `task add` through the CLI, imports no changed module"
}
#: A test that drives the program in a SUBPROCESS (`run_cli(...)`, `subprocess.run`) imports
#: nothing it exercises: the selector's stated blind spot (`testselect`, "What it misses"), a
#: route no import graph can draw. Such a miss is neither scored nor hidden: it is listed in
#: the report as `blind`, and the floor measures the tests the selector can route (B9934c6c6aa).
SUBPROCESS_CALLS = frozenset({"run", "Popen", "check_output", "check_call", "call"})
ABSOLUTE_FLOOR = 0.85  #: share of co-changed tests the selection holds, over all replays


def merge_log(root: Path) -> list[str]:
    """The ``merge <item>: ...`` commits ddflow wrote, newest first, reachable by ANY path.
    Not ``--first-parent``: on a branch that merged main in, main's merges sit on the second
    parent, so a first-parent walk saw an older window than main does and the replay failed
    on a merge main had long left behind (Bfaef48ced6, Bb5b8b17161). Only ``merge <id>:``
    subjects: a hand-made ``merge main`` sync commit is not a ddflow merge (B2d1ecadd87)."""
    log = git_proc(
        root, "log", "--merges", "--grep=^merge [^ :]*:", "-n", str(SCAN), "--format=%H %s"
    )
    if log.returncode != 0:
        return []
    rows = [ln.split(" ", 1) for ln in log.stdout.splitlines() if ln.strip()]
    # `merge main: ...` still reads like an item id to the grep, and is a sync merge
    return [r[0] for r in rows if len(r) < 2 or not r[1].startswith("merge main:")]


def _merges() -> list[str]:
    if os.environ.get("DDFLOW_SKIP_HISTORY_REPLAY"):
        pytest.skip("DDFLOW_SKIP_HISTORY_REPLAY is set")
    probe = git_proc(ROOT, "rev-parse", "--is-shallow-repository")
    if probe.returncode != 0 or probe.stdout.strip() != "false":
        pytest.skip("no full git history here")
    shas = merge_log(ROOT)
    if len(shas) < 3:
        pytest.skip("fewer than three merges in this history")
    return shas


def _replay(sha: str) -> tuple[list[str], list[str]] | None:
    """(source files, test files) a merge changed, or None when it has no source or no test
    change to replay."""
    names = git_raw(ROOT, "diff", "--name-only", "--no-renames", f"{sha}^1", sha).splitlines()
    names = [n for n in names if n and not n.startswith(".ddflow/")]
    src = [n for n in names if not T.is_test_file(n)]
    co = [n for n in names if T.is_test_file(n)]
    return (src, co) if src and co else None


@contextlib.contextmanager
def _checkout(sha: str):
    """The tree of ``sha`` extracted to a scratch directory, and its file list."""
    raw = Path(tempfile.mkdtemp())
    data = subprocess.run(["git", "-C", str(ROOT), "archive", sha], capture_output=True, check=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(data.stdout)) as tar:
            tar.extractall(raw, filter="data")
        yield raw, git_raw(ROOT, "ls-tree", "-r", "--name-only", sha).splitlines()
    finally:
        shutil.rmtree(raw, ignore_errors=True)


def _replayed(sha: str, src: list[str], co: list[str]) -> tuple[int, int, list[str], list[str]]:
    """(co-changed tests that still exist, how many the selection holds, the ones it lost
    that the earlier selector -- no hubs, no budget, no identifiers -- had held, every miss
    with its cause: cut at the budget, or no route reached it)."""
    with _checkout(sha) as (raw, listed):
        py = [f for f in src if f.endswith(".py")]
        idents = TI.changed_identifiers(ROOT, f"{sha}^1", py, tip=sha, source=raw)

        phrases = TI.changed_phrases(ROOT, f"{sha}^1", py, tip=sha, source=raw)
        added = git_raw(
            ROOT, "diff", "--name-only", "--no-renames", "--diff-filter=AD", f"{sha}^1", sha
        ).splitlines()
        new = T.select(
            raw,
            "x",
            changed=src,
            listed=listed,
            idents=idents,
            membership=added,
            phrases=phrases,
            **TSC.limits(Config()),
        )
        co = [c for c in co if (raw / c).exists()]
        assert new is not None
        if new.full:
            return len(co), len(co), [], []
        got = {t.path for t in new.tests}
        budget = TSC.limits(Config())["max_files"]
        floor = sum(1 for t in new.tests if t.rank == T.RANK_CHANGED)
        assert len(got) <= max(budget, floor), f"{sha[:8]}: {len(got)} tests, budget {budget}"
        missed = [c for c in co if c not in got]
        if not missed:
            return len(co), len(co), [], []
        cut = {t.path for t in new.cut}
        old = T.select(raw, "x", changed=src, listed=listed, idents={}, membership=[], phrases={})
        assert old is not None
        before = {t.path for t in old.tests}
        lost = [f"{sha[:8]} {c}" for c in missed if c in before]  # blind ones too: pins stay honest
        blind = [
            c for c in missed if drives_subprocess((raw / c).read_text("utf-8", "replace"), src)
        ]
        scored = [c for c in co if c not in blind]
        causes = [
            f"{sha[:8]} {c} ({'cut at the budget' if c in cut else 'no route reaches it'})"
            for c in missed
            if c not in blind
        ]
        listed_blind = [f"{sha[:8]} {c} (blind: drives a subprocess, not scored)" for c in blind]
        return len(scored), len(scored) - len(causes), lost, [*causes, *listed_blind]


def _imported(text: str) -> set[str] | None:
    """Every dotted module name a test module imports (`from a.b import c` gives `a.b` and
    `a.b.c`, `import a.b.c` gives all three prefixes); None when it does not parse."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                names.update(".".join(parts[: i + 1]) for i in range(len(parts)))
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def imports_changed(text: str, src: list[str]) -> bool:
    """Whether a test module imports one of the changed source modules directly (a route the
    selector draws, so a miss of it is a real one, whatever else the test does). A changed
    package `__init__.py` is the package itself."""
    seen = _imported(text)
    if seen is None:  # unparsable: do not call it blind on a guess
        return True
    for path in src:
        if path.endswith(".py"):
            module = path[:-3].replace("/", ".").removesuffix(".__init__")
            if module in seen:
                return True
    return False


def drives_subprocess(text: str, src: list[str] = ()) -> bool:
    """Whether a test module drives the program in a subprocess (the selector's blind spot) and
    imports none of the changed ``src`` modules itself. Read from the syntax tree: a comment or
    a docstring that mentions `run_cli(` is not a call."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = (
                f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            )
            owner = (
                f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
            )
            if name == "run_cli" or (owner == "subprocess" and name in SUBPROCESS_CALLS):
                return not imports_changed(text, list(src))
    return False


def _one(sha: str) -> tuple[int, int, list[str], list[str]] | None:
    rows = _replay(sha)
    return None if rows is None else _replayed(sha, *rows)


def test_the_selection_holds_the_tests_recent_merges_changed_with_their_source():
    shas = _merges()
    assert len(shas) <= SCAN, f"{len(shas)} merges replayed, the bound is {SCAN}"
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        results = [r for r in pool.map(_one, shas) if r is not None]
    total = sum(r[0] for r in results)
    held = sum(r[1] for r in results)
    lost = [x for r in results for x in r[2]]
    residual = [x for r in results for x in r[3]]
    if len(results) < 2 or total == 0:
        pytest.skip("too few replayable merges in this history")
    report = f"recall {held}/{total} over {len(results)} merges; misses: {residual}"
    # Exactly the pinned losses: a new one fails, and so does a pinned one the selector now
    # routes (drop its entry then), so the pin cannot go stale or hide a fresh miss.
    unexpected = [x for x in lost if x not in UNROUTABLE]
    assert not unexpected, (
        f"the precise selection lost tests the earlier selector held: {unexpected}; {report}"
    )
    in_window = {sha[:8] for sha in shas if _replay(sha) is not None}  # scored, not just asked for
    stale = [k for k in UNROUTABLE if k.split()[0] in in_window and k not in lost]
    assert not stale, f"pinned as unroutable, but the selection holds them now: {stale}"
    assert held / total >= ABSOLUTE_FLOOR, report


def test_a_subprocess_driving_test_is_a_blind_spot_not_a_scored_miss():
    """B9934c6c6aa: eight such tests sank the floor of a window whose selector was fine."""
    assert drives_subprocess("code, out, err = run_cli(repo, 'bug', 'show', 'B1')")
    assert drives_subprocess("import subprocess\nsubprocess.run(['ddflow', 'show'])")
    assert not drives_subprocess("from ddflow.services import ci\nci.run(repo)")
    assert not drives_subprocess("# run_cli(repo) is the helper for this\nresult = run_cli_like")
    # a test that imports a changed module AND drives a subprocess is routable: its miss counts
    both = "from ddflow.services import ci\nrun_cli(repo, 'x')"
    assert not drives_subprocess(both, ["ddflow/services/ci.py"])
    assert drives_subprocess(both, ["ddflow/services/gates.py"])
    assert drives_subprocess("import ddflow.api.review\nrun_cli(r)", ["ddflow/api/cirun.py"])
    # shapes a regex missed: a parenthesised multi-line import, a changed package __init__
    multi = "from ddflow.services import (\n    ci,\n)\nrun_cli(repo)\n"
    assert not drives_subprocess(multi, ["ddflow/services/ci.py"])
    assert not drives_subprocess(
        "from ddflow.api import review\nrun_cli(r)", ["ddflow/api/__init__.py"]
    )
    assert not drives_subprocess("import ddflow.api\nrun_cli(r)", ["ddflow/api/__init__.py"])


def test_merge_log_ignores_a_hand_made_sync_merge(tmp_path):
    """B2d1ecadd87: `git merge main -m 'merge main'` is not a ddflow merge."""

    def g(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)

    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    for branch, subject in (
        ("a", "merge T1: real"),
        ("b", "merge main"),
        ("c", "merge main from origin"),
        ("d", "merge main: sync with main"),
    ):
        g("commit", "-q", "--allow-empty", "-m", "base" + branch)
        g("checkout", "-q", "-b", "side" + branch)
        g("commit", "-q", "--allow-empty", "-m", "side" + branch)
        g("checkout", "-q", "main")
        g("commit", "-q", "--allow-empty", "-m", "main" + branch)
        g("merge", "-q", "--no-ff", "-m", subject, "side" + branch)
    out = git_proc(tmp_path, "log", "--format=%s").stdout.splitlines()
    assert "merge main" in out
    subjects = [
        git_proc(tmp_path, "show", "-s", "--format=%s", sha).stdout.strip()
        for sha in merge_log(tmp_path)
    ]
    assert subjects == ["merge T1: real"]
