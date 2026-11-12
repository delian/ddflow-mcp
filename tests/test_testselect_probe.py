"""TS-impact-probe: replay recent merges against the EXISTING selector (`services/testselect`).

`probe(root, shas, limits)` answers, per merge and over all of them: the recall of the tests
the merge changed next to its source, the share of the suite the selection runs (a whole-suite
answer is 1.0), how often the hub fallback fires, what a narrower choice at a hub would have
held, and how many misses are subprocess-driven tests that only recorded dependencies
(TS-cover-deps) could route. The read-out lives in docs/ddflow/research/test-impact.md.

Run it on this repository:  ``uv run python tests/test_testselect_probe.py 100``
(about seven seconds a merge, a few processes, no network). The tests below replay a small
fixture repository, so the suite stays fast.
"""

from __future__ import annotations

import io
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import git_proc, git_raw
from test_testselect_recall import drives_subprocess, merge_log

from ddflow.config import Config
from ddflow.services import testidents as TI
from ddflow.services import testscope as TSC
from ddflow.services import testselect as T

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class MergeRow:
    sha: str
    source: int  #: changed non-test files (outside .ddflow)
    suite: int  #: test files in the tree at the merge
    selected: int  #: tests the selection runs (the whole suite when `full`)
    full: bool  #: the selector answered "the whole suite" (hub or test infrastructure)
    unrouted: bool  #: source changed, nothing selected, no fallback: only the later full run looks
    co: int = 0  #: tests changed alongside the source that still exist
    held: int = 0  #: of those, the ones the selection holds (all of them when `full`)
    blind: list[str] = field(default_factory=list)  #: missed, subprocess-driven (not routable)
    missed: list[str] = field(default_factory=list)  #: missed, with an import or name route
    hub: bool = False  #: the whole-suite answer is a changed hub (not test infrastructure)
    narrow_selected: int | None = None  #: at a hub: tests a hub-unbounded selection runs
    narrow_held: int | None = None  #: at a hub: of the co-changed tests, how many that holds

    @property
    def share(self) -> float:
        return self.selected / self.suite if self.suite else 1.0


@dataclass
class Probe:
    rows: list[MergeRow]
    skipped: int = 0  #: merges that changed no source file (nothing to select), kept visible
    errors: list[str] = field(default_factory=list)  #: merges the selector raised on, loudly kept

    @property
    def scored(self) -> list[MergeRow]:
        return [r for r in self.rows if r.co]

    @property
    def recall(self) -> float | None:
        total = sum(r.co for r in self.scored)
        return sum(r.held for r in self.scored) / total if total else None

    @property
    def routable_recall(self) -> float | None:
        """Recall once subprocess-driven misses are set aside (they have no import route)."""
        total = sum(r.co - len(r.blind) for r in self.scored)
        held = sum(r.held for r in self.scored)
        return held / total if total else None  # nothing routable to score: not a perfect 1.0

    @property
    def median_share(self) -> float:
        shares = [r.share for r in self.rows if r.suite]
        return statistics.median(shares) if shares else 0.0

    @property
    def fallback_rate(self) -> float:
        return sum(r.full for r in self.rows) / len(self.rows) if self.rows else 0.0

    @property
    def hub_rate(self) -> float:
        return sum(r.hub for r in self.rows) / len(self.rows) if self.rows else 0.0

    @property
    def unrouted_rate(self) -> float:
        return sum(r.unrouted for r in self.rows) / len(self.rows) if self.rows else 0.0

    @property
    def blind_total(self) -> int:
        return sum(len(r.blind) for r in self.rows)

    def hub_rows(self) -> list[MergeRow]:
        return [r for r in self.rows if r.hub and r.narrow_selected is not None]

    def hub_narrow_share(self) -> float | None:
        """None when no hub merge had a narrower choice to measure (never a perfect 0)."""
        shares = [r.narrow_selected / r.suite for r in self.hub_rows() if r.suite]
        return statistics.median(shares) if shares else None

    def hub_narrow_recall(self) -> float | None:
        hubs = [r for r in self.hub_rows() if r.co]
        total = sum(r.co for r in hubs)
        return sum(r.narrow_held or 0 for r in hubs) / total if total else None

    def report(self) -> str:
        return (
            f"merges {len(self.rows)} (scored {len(self.scored)}); "
            f"recall {_fmt(self.recall)} (routable {_fmt(self.routable_recall)}); "
            f"median selected share {self.median_share:.3f}; "
            f"whole-suite rate {self.fallback_rate:.3f} (hub-fallback rate {self.hub_rate:.3f}); "
            f"unrouted rate {self.unrouted_rate:.3f}; "
            f"hub narrowed share {_fmt(self.hub_narrow_share())} at recall "
            f"{_fmt(self.hub_narrow_recall())}; subprocess-blind misses {self.blind_total}; "
            f"no-source merges skipped {self.skipped}; errors {len(self.errors)}"
        )


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def _extract(root: Path, sha: str) -> tuple[Path, list[str]]:
    raw = Path(tempfile.mkdtemp())
    try:
        data = subprocess.run(
            ["git", "-C", str(root), "archive", sha], capture_output=True, check=True
        )
        with tarfile.open(fileobj=io.BytesIO(data.stdout)) as tar:
            tar.extractall(raw, filter="data")
        return raw, git_raw(root, "ls-tree", "-r", "--name-only", sha).splitlines()
    except BaseException:  # no caller owns the directory yet: remove it here
        shutil.rmtree(raw, ignore_errors=True)
        raise


def _changed_sets(root: Path, sha: str) -> tuple[list[str], list[str]]:
    """(source files, test files) the merge changed, outside .ddflow."""
    names = git_raw(root, "diff", "--name-only", "--no-renames", f"{sha}^1", sha).splitlines()
    names = [n for n in names if n and not n.startswith(".ddflow/")]
    return [n for n in names if not T.is_test_file(n)], [n for n in names if T.is_test_file(n)]


def _stand_ins(root: Path, raw: Path, sha: str, src: list[str], listed: list[str]) -> dict:
    py = [f for f in src if f.endswith(".py")]
    added = git_raw(
        root, "diff", "--name-only", "--no-renames", "--diff-filter=AD", f"{sha}^1", sha
    ).splitlines()
    return {
        "changed": src,
        "listed": listed,
        "idents": TI.changed_identifiers(root, f"{sha}^1", py, tip=sha, source=raw),
        "membership": added,
        "phrases": TI.changed_phrases(root, f"{sha}^1", py, tip=sha, source=raw),
    }


def _score(raw: Path, row: MergeRow, co: list[str], got: set[str], src: list[str]) -> None:
    """Recall of the co-changed tests; a missed one is blind (drives a subprocess) or routable."""
    row.held = row.co if row.full else sum(1 for c in co if c in got)
    for c in (c for c in co if not row.full and c not in got):
        blind = drives_subprocess((raw / c).read_text("utf-8", "replace"), src)
        (row.blind if blind else row.missed).append(c)


def replay_one(args: tuple[str, str, dict]) -> MergeRow | None:
    """One merge against the existing selector; None when it changed no source."""
    root_s, sha, limits = args
    root = Path(root_s)
    src, co_all = _changed_sets(root, sha)
    if not src:
        return None
    raw, listed = _extract(root, sha)
    try:
        stand_in = _stand_ins(root, raw, sha, src, listed)
        sel = T.select(raw, "x", **stand_in, **limits)
        assert sel is not None
        suite = [p for p in listed if T.is_test_file(p) and (raw / p).is_file()]
        co = [c for c in co_all if (raw / c).exists()]
        full = bool(sel.full) and not sel.neutral
        got = {t.path for t in sel.tests}
        row = MergeRow(
            sha=sha[:8],
            source=len(src),
            suite=len(suite),
            selected=len(suite) if full else len(got),
            full=full,
            unrouted=not full and not got,
            co=len(co),
        )
        _score(raw, row, co, got, src)
        row.hub = full and sel.full.startswith("a hub module changed")
        if row.hub:  # what a choice narrower than the whole suite would have held at the hub
            try:
                narrow = T.select(raw, "x", **stand_in, **{**limits, "hub_importers": 0})
            except Exception:  # the counterfactual is optional: the merge still counts
                narrow = None
            if narrow is None or narrow.full:  # no narrower choice could be measured
                return row
            held = {t.path for t in narrow.tests}
            row.narrow_selected = len(held)
            row.narrow_held = sum(1 for c in co if c in held)
        return row
    finally:
        shutil.rmtree(raw, ignore_errors=True)


def _safe(job: tuple[str, str, dict]) -> MergeRow | str | None:
    """A merge the selector raises on is reported, never taken for a pass or a whole-window abort."""
    try:
        return replay_one(job)
    except Exception as exc:
        return f"{job[1][:8]}: {type(exc).__name__}: {exc}"


def probe(root: Path, shas: list[str], limits: dict, workers: int = 4) -> Probe:
    jobs = [(str(root), s, limits) for s in shas]
    if workers <= 1:
        rows = [_safe(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(_safe, jobs))
    return Probe(
        [r for r in rows if isinstance(r, MergeRow)],
        sum(r is None for r in rows),
        [r for r in rows if isinstance(r, str)],
    )


# ---- fixture repository ------------------------------------------------------------------


def _g(repo: Path, *args: str) -> None:
    # hermetic: the user's global git config (signing, hooks) must not reach the fixture
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _merge(repo: Path, name: str, files: dict[str, str]) -> str:
    _g(repo, "checkout", "-q", "-b", name)
    for rel, text in files.items():
        _write(repo, rel, text)
    _g(repo, "add", "-A")
    _g(repo, "commit", "-q", "-m", f"work {name}")
    _g(repo, "checkout", "-q", "main")
    _g(repo, "merge", "-q", "--no-ff", "-m", f"merge {name}: x", name)
    return git_proc(repo, "rev-parse", "HEAD").stdout.strip()


def make_repo(tmp: Path) -> tuple[Path, dict[str, str]]:
    """A package with a leaf, a hub three tests import, an unmapped data file, a subprocess test."""
    _g(tmp, "init", "-q", "-b", "main")
    _g(tmp, "config", "user.email", "t@t")
    _g(tmp, "config", "user.name", "t")
    _write(tmp, "pkg/__init__.py", "")
    _write(tmp, "pkg/leaf.py", "def leaf():\n    return 1\n")
    _write(tmp, "pkg/hub.py", "def hub():\n    return 1\n")
    _write(tmp, "pkg/tool.py", "print('tool')\n")
    _write(tmp, "tests/test_leaf.py", "from pkg import leaf\n\n\ndef test_a():\n    assert leaf\n")
    for i in range(3):
        _write(
            tmp,
            f"tests/test_use{i}.py",
            f"from pkg import hub\n\n\ndef test_{i}():\n    assert hub\n",
        )
    _write(
        tmp,
        "tests/test_smoke.py",
        "import subprocess\n\n\ndef test_t():\n    subprocess.run(['python', 'pkg/tool.py'])\n",
    )
    _g(tmp, "add", "-A")
    _g(tmp, "commit", "-q", "-m", "base")
    shas = {
        "leaf": _merge(
            tmp,
            "leaf",
            {
                "pkg/leaf.py": "def leaf():\n    return 2\n",
                "tests/test_leaf.py": "from pkg import leaf\n\n\ndef test_a():\n    assert leaf()\n",
            },
        ),
        "hub": _merge(
            tmp,
            "hub",
            {
                "pkg/hub.py": "def hub():\n    return 2\n",
                "tests/test_use0.py": "from pkg import hub\n\n\ndef test_0():\n    assert hub()\n",
            },
        ),
        "tool": _merge(
            tmp,
            "tool",
            {
                "pkg/tool.py": "print('tool 2')\n",
                "tests/test_smoke.py": "import subprocess\n\n\ndef test_t():\n    subprocess.run(['python', 'pkg/tool.py'], check=True)\n",
            },
        ),
        "data": _merge(tmp, "data", {"notes/unmapped.txt": "x\n"}),
    }
    return tmp, shas


LIMITS = {"max_files": 100, "hub_importers": 3, "max_hops": 2, "name_max_tests": 30}


def test_a_leaf_merge_selects_its_tests_only_and_keeps_recall(tmp_path):
    repo, shas = make_repo(tmp_path)
    p = probe(repo, [shas["leaf"]], LIMITS, workers=1)
    (row,) = p.rows
    assert not row.full and row.selected < row.suite
    assert row.held == row.co == 1
    assert p.recall == 1.0 and p.median_share < 1.0 and p.fallback_rate == 0.0


def test_a_hub_merge_falls_back_to_the_whole_suite_and_reports_a_narrower_choice(tmp_path):
    repo, shas = make_repo(tmp_path)
    p = probe(repo, [shas["hub"]], LIMITS, workers=1)
    (row,) = p.rows
    assert row.full and row.selected == row.suite  # safety: a hub change selects everything
    assert p.fallback_rate == 1.0 and p.hub_rate == 1.0 and p.recall == 1.0
    # the hub-unbounded choice: the three importers, fewer than the whole suite, and it holds
    # the co-changed test
    assert row.narrow_selected is not None and 3 <= row.narrow_selected < row.suite
    assert row.narrow_held == row.co == 1
    share = p.hub_narrow_share()
    assert share is not None and share < 1.0 and p.hub_narrow_recall() == 1.0


def test_a_subprocess_only_test_is_a_blind_miss_the_recorded_dependencies_would_route(tmp_path):
    repo, shas = make_repo(tmp_path)
    p = probe(repo, [shas["tool"]], LIMITS, workers=1)
    (row,) = p.rows
    assert row.co == 1 and row.held == 0
    assert row.blind == ["tests/test_smoke.py"] and row.missed == []
    assert p.blind_total == 1 and p.recall == 0.0


def test_an_unmapped_file_is_counted_as_unrouted_not_as_a_pass(tmp_path):
    repo, shas = make_repo(tmp_path)
    p = probe(repo, [shas["data"]], LIMITS, workers=1)
    (row,) = p.rows
    assert row.unrouted and not row.full and row.selected == 0
    assert p.unrouted_rate == 1.0
    assert "unrouted rate 1.000" in p.report()


def test_the_report_names_every_measure(tmp_path):
    repo, shas = make_repo(tmp_path)
    p = probe(repo, list(shas.values()), LIMITS, workers=1)
    text = p.report()
    for word in (
        "recall",
        "median selected share",
        "hub-fallback rate",
        "errors",
        "subprocess-blind",
    ):
        assert word in text
    assert len(p.rows) == 4


def test_the_probe_runs_on_this_repository_window():
    """A thin live smoke: the newest merges until one with source is scored (the full 100-merge
    read-out is the script)."""
    shas = merge_log(ROOT)[:3]
    if not shas:
        pytest.skip("no merge history here")
    p = probe(ROOT, shas, TSC.limits(Config()), workers=1)
    assert not p.errors, p.errors
    if not p.rows:
        pytest.skip("none of the newest merges changed source: nothing to select")
    assert all(0.0 <= r.share <= 1.0 for r in p.rows)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    import test_testselect_probe as P  # not __main__: a spawned worker must import `_safe`
    import test_testselect_recall as R

    R.SCAN = n
    window = merge_log(ROOT)
    result = P.probe(ROOT, window, TSC.limits(Config()))
    print(result.report())
    for r in result.rows:
        if r.missed or r.blind:
            print(r.sha, "missed", r.missed, "blind", r.blind)


def test_a_hub_share_with_an_empty_suite_does_not_crash():
    row = MergeRow(
        sha="a",
        source=1,
        suite=0,
        selected=0,
        full=True,
        unrouted=False,
        hub=True,
        narrow_selected=0,
        narrow_held=0,
    )
    assert "hub narrowed share n/a" in Probe([row]).report()


def test_a_non_hub_whole_suite_answer_is_not_a_hub_row():
    row = MergeRow(sha="a", source=1, suite=5, selected=5, full=True, unrouted=False)
    p = Probe([row])
    assert p.fallback_rate == 1.0 and p.hub_rate == 0.0 and p.hub_rows() == []


def test_a_merge_the_selector_raises_on_is_reported_not_fatal(tmp_path):
    repo, shas = make_repo(tmp_path)
    p = probe(repo, [shas["leaf"], "0" * 40], LIMITS, workers=1)
    assert len(p.rows) == 1 and len(p.errors) == 1 and "errors 1" in p.report()


def test_a_merge_with_no_source_change_is_counted_as_skipped(tmp_path):
    repo, shas = make_repo(tmp_path)
    p = probe(
        repo,
        [shas["leaf"], _merge(repo, "docsonly", {"tests/test_leaf.py": "x = 1\n"})],
        LIMITS,
        workers=1,
    )
    assert len(p.rows) == 1 and p.skipped == 1 and "skipped 1" in p.report()


def test_routable_recall_is_undefined_when_every_miss_is_blind_and_nothing_else_scores(tmp_path):
    repo, shas = make_repo(tmp_path)
    p = probe(repo, [shas["tool"]], LIMITS, workers=1)
    assert p.routable_recall is None and "routable n/a" in p.report()


def test_recall_is_undefined_when_no_merge_changed_a_test_beside_its_source(tmp_path):
    repo, shas = make_repo(tmp_path)
    p = probe(repo, [shas["data"]], LIMITS, workers=1)
    assert p.recall is None and "recall n/a" in p.report()
