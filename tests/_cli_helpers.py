"""`run_cli`, in a module whose name cannot collide with the root ``conftest.py``.

Anything a ``multiprocessing`` *spawn* worker imports must come from here, never from
``conftest``: the worker re-imports the test module with the repo root first on
``sys.path``, where ``conftest`` is the root file, not ``tests/conftest.py``.
"""

import os
import subprocess
import sys
from pathlib import Path

from _wait import slow_factor

#: The quiet-machine ceiling of one CLI call; `run_cli` multiplies it by `_wait.slow_factor()`.
RUN_CLI_TIMEOUT_S = 300
#: ... capped, so a real deadlock still ends the run in half an hour.
RUN_CLI_TIMEOUT_MAX_S = 1800


def _text(blob) -> str:
    return blob.decode(errors="replace") if isinstance(blob, bytes) else (blob or "")


def run_cli(repo: Path, *argv: str, agent: str = "") -> tuple[int, str, str]:
    """Invoke the CLI as a real subprocess, as an agent would: in the repository under
    test. Anchoring the cwd there keeps a launch of the tree under test the explicit
    external launch the foreign-launch guard allows (B93e00ef048), never the cross-lane
    run it refuses when the caller stands inside the checkout family."""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    args = [sys.executable, "-m", "ddflow", "--repo", str(repo)]
    if agent:
        args += ["--agent", agent]
    # The ceiling for a hang, not a measurement: a CLI start under the load of many agents'
    # ci runs takes minutes (Bfaef48ced6), so it grows with the load (tests/_wait.py).
    limit = min(RUN_CLI_TIMEOUT_S * slow_factor(), RUN_CLI_TIMEOUT_MAX_S)
    try:
        p = subprocess.run(
            [*args, *argv], capture_output=True, text=True, env=env, cwd=str(repo), timeout=limit
        )
    except subprocess.TimeoutExpired as exc:  # say WHICH command, with what it printed
        seen = f"stdout={_text(exc.stdout)!r} stderr={_text(exc.stderr)!r}"
        raise AssertionError(
            f"`ddflow {' '.join(argv)}` did not finish within {limit:.0f}s: {seen}"
        ) from exc
    return p.returncode, p.stdout, p.stderr
