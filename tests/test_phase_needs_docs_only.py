"""A docs-only task does not inherit its phase's needs (B-research-task-inherits-phase-deps).

A research/doc task added under a phase that needs another open phase was blocked by
"phase P-x has 26 open task(s) (inherited from P-y)" though it writes only a document.
`[schedule].docs_ignore_phase_needs` (default on) lets it start; code tasks still wait.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.core.schedule import inherited_deps
from ddflow.infra.log import EventLog


def _queue(repo) -> None:
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P-gate", "--title", "gate")
    run_cli(repo, "task", "add", "G1", "--phase", "P-gate", "--globs", "core/g.py")
    run_cli(repo, "phase", "add", "P-next", "--title", "next", "--needs", "P-gate")
    add = ("task", "add")
    run_cli(repo, *add, "N-code", "--phase", "P-next", "--globs", "core/n.py")
    run_cli(repo, *add, "N-res", "--phase", "P-next", "--kind", "research",
            "--globs", "docs/ddflow/research/n.md")  # fmt: skip
    run_cli(repo, *add, "N-docs", "--phase", "P-next", "--globs", "docs/n.md")
    run_cli(repo, *add, "N-mixed", "--phase", "P-next", "--globs", "docs/n.md,core/m.py")


def _ready(repo) -> set[str]:
    code, out, err = run_cli(repo, "--json", "next")
    assert code in (0, 2), f"exit {code}\n{out}\n{err}"
    return {r["id"] for r in json.loads(out)["ready"]}


def test_docs_only_tasks_are_ready_code_tasks_still_wait(repo):
    _queue(repo)
    ready = _ready(repo)
    assert {"N-res", "N-docs"} <= ready, ready
    assert "N-code" not in ready and "N-mixed" not in ready, ready


def test_the_knob_turns_it_off(repo):
    _queue(repo)
    code, _o, err = run_cli(repo, "config", "--set", "schedule.docs_ignore_phase_needs", "false")
    assert code == 0, err
    assert not ({"N-res", "N-docs"} & _ready(repo))


def test_inherited_deps_reports_the_exemption(repo):
    _queue(repo)
    st = fold(EventLog(repo, "t").read_all(), strict=False)
    cfg = Config()
    assert inherited_deps(st, st.items["N-res"], cfg) == []
    assert ("P-next", "P-gate") in inherited_deps(st, st.items["N-code"], cfg)


def test_a_recursive_glob_is_not_docs_under_a_narrower_doc_glob(repo):
    """`docs/**` reaches code in subdirectories; a doc glob of `docs/*` must not excuse it."""
    _queue(repo)
    st = fold(EventLog(repo, "t").read_all(), strict=False)
    cfg = Config()
    cfg.gates.doc_globs = ["docs/*"]
    it = st.items["N-docs"]
    it.globs = ["docs/**"]
    assert ("P-next", "P-gate") in inherited_deps(st, it, cfg)
    it.globs = ["docs/*.md"]
    assert inherited_deps(st, it, cfg) == []


def test_a_partly_covered_wildcard_is_not_docs(repo):
    """`docs/*` also matches `docs/y`, which a doc glob of `docs/x*` does not cover."""
    _queue(repo)
    st = fold(EventLog(repo, "t").read_all(), strict=False)
    cfg = Config()
    cfg.gates.doc_globs = ["docs/x*"]
    it = st.items["N-docs"]
    it.globs = ["docs/*"]
    assert ("P-next", "P-gate") in inherited_deps(st, it, cfg)


def test_containment_rules_are_sound_and_cover_the_usual_shapes(repo):
    from ddflow.core.schedule import _glob_is_docs

    docs = ["docs/**", "**/*.md"]
    assert _glob_is_docs("docs/**/*.md", docs)
    assert _glob_is_docs("docs/x*", docs)
    assert _glob_is_docs("src/readme.md", docs)
    assert _glob_is_docs("src/*.md", docs)
    assert not _glob_is_docs("src/*.py", docs)
    assert not _glob_is_docs("docs/../src/a.py", docs)
    assert not _glob_is_docs("docs/*", ["docs/x*"])
    assert not _glob_is_docs("docs/*", ["docs/x", "docs/zq9"])
    assert not _glob_is_docs("docs/**", ["docs/*"])


def test_reporting_views_follow_the_knob(repo):
    """critical_path and the loop detector's wait graph agree with the scheduler."""
    from ddflow.core.progress import _waits_on
    from ddflow.core.schedule import critical_path

    _queue(repo)
    st = fold(EventLog(repo, "t").read_all(), strict=False)
    on, off = Config(), Config()
    off.schedule.docs_ignore_phase_needs = False
    assert "G1" not in _waits_on(st, "N-res", on)
    assert "G1" in _waits_on(st, "N-res", off)
    assert "N-res" not in critical_path(st, "", on) or "G1" not in critical_path(st, "", on)
    assert critical_path(st, "", off)
