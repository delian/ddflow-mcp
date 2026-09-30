"""`scripts/ci/build-probe.sh` fails a wheel that would fail its first user (Ba5345f975d).

CI's build step used to run its checks from the repository root, where `python -c` puts
the SOURCE tree first on sys.path: it imported the checkout, which always has its
templates, instead of the wheel it had just installed. A wheel with `companions.toml`
stripped out passed. It also ran `python -m ddflow`, which never touches the console
script, so a misspelled `[project.scripts]` target passed too.

Each case builds a real wheel from a copy of the project with exactly one thing broken.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not shutil.which("uv"), reason="uv builds the wheel")


def _copy(tmp_path: Path) -> Path:
    """What the wheel is built from, and the probe script, outside the checkout."""
    proj = tmp_path / "proj"
    shutil.copytree(ROOT / "ddflow", proj / "ddflow", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pyproject.toml", "README.md", "LICENSE", "uv.lock"):
        shutil.copy(ROOT / name, proj / name)
    (proj / "scripts" / "ci").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "ci" / "build-probe.sh", proj / "scripts" / "ci")
    return proj


def _probe(proj: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(proj / "scripts" / "ci" / "build-probe.sh")],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=400,
    )


def test_a_wheel_missing_its_templates_fails_the_probe(tmp_path):
    """The SOURCE still has the file; only the wheel lacks it -- what a packaging-config
    mistake does, and exactly what a probe that imports the checkout cannot see."""
    proj = _copy(tmp_path)
    toml = proj / "pyproject.toml"
    marker = '[tool.hatch.build.targets.wheel]\npackages = ["ddflow"]\n'
    assert marker in toml.read_text(), "the wheel target moved; update this test"
    toml.write_text(
        toml.read_text().replace(
            marker, marker + 'exclude = ["ddflow/templates/companions.toml"]\n'
        )
    )
    assert (proj / "ddflow" / "templates" / "companions.toml").is_file()
    r = _probe(proj)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "missing package data" in r.stdout + r.stderr
    assert "companions.toml" in r.stdout + r.stderr


def test_a_broken_console_script_fails_the_probe(tmp_path):
    proj = _copy(tmp_path)
    toml = proj / "pyproject.toml"
    text, n = re.subn(r'(?m)^(ddflow = "[^":]+):\w+"', r'\1:mian"', toml.read_text())
    assert n == 1, "the console-script line moved; update this test"
    toml.write_text(text)
    r = _probe(proj)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "mian" in r.stdout + r.stderr
