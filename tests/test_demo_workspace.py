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


def test_two_runs_do_not_wipe_each_other(tmp_path):
    """Real processes: run B starts while run A is mid-way; A's tree must survive."""
    code = (
        "import sys; sys.path[:0]=[%r,%r]\n"
        "import run_all; b = run_all.demo_base(); print(b)\n"
    ) % (str(ROOT / "demos"), str(ROOT))
    a = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
    marker = Path(a) / "marker"
    marker.write_text("x")
    subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    try:
        assert marker.exists()
    finally:
        marker.unlink()
        Path(a).rmdir()
