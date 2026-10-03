"""B184: concurrent demo runs must not share (and rmtree) one workspace."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "demos"), str(ROOT)]


def test_each_run_gets_its_own_workspace(monkeypatch):
    import run_all

    monkeypatch.delenv("DDFLOW_DEMOS_DIR", raising=False)
    a, b = run_all.demo_base(), run_all.demo_base()
    try:
        assert a != b and a.is_dir() and b.is_dir()
        assert str(a) != "/tmp/ddflow-demos"
    finally:
        a.rmdir()
        b.rmdir()


def test_workspace_can_be_pinned(monkeypatch, tmp_path):
    import run_all

    monkeypatch.setenv("DDFLOW_DEMOS_DIR", str(tmp_path / "x"))
    assert run_all.demo_base() == tmp_path / "x"


def test_run_never_wipes_the_workspace_it_does_not_own(monkeypatch, tmp_path):
    """The real main(): a pinned directory's other contents (another run's scenarios)
    survive; only the scenario being run has its own subdirectory cleared."""
    import run_all

    base = tmp_path / "pinned"
    (base / "other-run").mkdir(parents=True)
    (base / "other-run" / "marker").write_text("x")
    (base / "mine").mkdir()
    (base / "mine" / "stale").write_text("old")
    seen = {}

    def fake(sc):
        seen["dir"] = sc.dir
        seen["stale_gone"] = not (sc.dir / "stale").exists()

    monkeypatch.setenv("DDFLOW_DEMOS_DIR", str(base))
    monkeypatch.setattr(run_all, "SCENARIOS", [("mine", fake)])
    run_all.main([])
    assert (base / "other-run" / "marker").read_text() == "x"
    assert seen["stale_gone"] and seen["dir"] == base / "mine"
