"""ddflow/api/record_update.py stays clean under the CI bandit step (-ll): the item_set refusal
text must not be built by a string expression bandit reads as SQL (B608)."""

import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parent.parent / "ddflow" / "api" / "record_update.py"


def test_record_update_has_no_bandit_finding():
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv not available")
    p = subprocess.run(
        [uv, "run", "--no-project", "--with", "bandit", "bandit", "-q", "-ll", str(MODULE)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-500:]
