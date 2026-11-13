"""The completion shim passes the CI static-security hook (bandit at medium and above)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_completion_shell_has_no_medium_bandit_finding():
    # `argcomplete.shellcode(..., shell="zsh")` names a shell; bandit B604 reads it as
    # subprocess `shell=True`. The justified `# nosec B604` is what keeps the hook green.
    run = subprocess.run(
        [
            "uv",
            "run",
            "--no-project",
            "--with",
            "bandit",
            "bandit",
            "-q",
            "-ll",
            str(ROOT / "ddflow" / "services" / "completion_shell.py"),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=ROOT,
    )
    assert run.returncode == 0, run.stdout + run.stderr
