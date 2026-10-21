"""Spawn workers re-import test modules with the repo root FIRST on sys.path, where a bare
``conftest`` is the ROOT conftest.py, not ``tests/conftest.py`` (Bconftest-spawn-collision).
A helper a worker needs must therefore not be imported from ``conftest``."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _worker_imports(tmp_path: Path) -> subprocess.CompletedProcess:
    script = tmp_path / "w.py"
    script.write_text(
        "import multiprocessing as mp, sys\n"
        f"sys.path.insert(0, {str(ROOT / 'tests')!r})\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"  # root first, as in the CI spawn worker
        "import test_mcp_load\n"
        "if __name__ == '__main__':\n"
        "    p = mp.get_context('spawn').Process(target=test_mcp_load._noop)\n"
        "    p.start(); p.join(120)\n"
        "    sys.exit(1 if p.exitcode is None else p.exitcode)\n"
    )
    return subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=300, cwd=tmp_path
    )


def test_a_spawn_worker_with_the_repo_root_first_imports_the_load_test_module(tmp_path):
    p = _worker_imports(tmp_path)
    assert p.returncode == 0, p.stderr[-1500:]


def test_the_load_test_takes_run_cli_from_a_uniquely_named_module():
    src = (ROOT / "tests" / "test_mcp_load.py").read_text()
    assert "from conftest import" not in src
