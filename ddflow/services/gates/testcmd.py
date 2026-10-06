"""Whether a project's test command runs on one core, and the parallel command to suggest."""

from __future__ import annotations

import re
from pathlib import Path

# -- a test gate that uses one core --------------------------------------------------
#
# Measured on this repository (research R537ed343e7): the unit_tests gate ran pytest
# serially in 49 minutes on a 192-core machine, and in under a minute with pytest-xdist.
# Every per-item gate waited on that, so the cheapest speed-up in the workflow was a flag.

#: Where a Python project declares what it installs. Read as TEXT, never imported: the
#: question is what the PROJECT's environment will have, and ddflow's own interpreter is
#: a different environment.
_PY_MANIFESTS = (
    "pyproject.toml", "uv.lock", "poetry.lock", "Pipfile", "Pipfile.lock",
    "setup.cfg", "setup.py", "tox.ini",
)  # fmt: skip

_PYTEST = re.compile(r"(?:^|[\s/])(?:py\.test|pytest)(?:\s|$)")

#: A pytest command that has already chosen its workers: `-n N`, `-nauto`,
#: `--numprocesses`, `--dist`, or xdist switched off with `-p no:xdist` -- the last two
#: are how an operator says serial is deliberate, and deliberate is not advised against.
_XDIST_CHOSEN = re.compile(r"(?:^|\s)(?:-n\s*\S|--numprocesses\b|--dist\b|-p\s*no:xdist\b)")


#: A `#` comment in TOML, INI, requirements and Python alike -- at line start or after
#: whitespace, so the `#egg=` fragment of a requirement URL is not taken for one.
_COMMENT = re.compile(r"(?:^|\s)#.*$", re.MULTILINE)

#: Whole package names: `pytest-xdist-foo` is not pytest-xdist, `pytest-cov` is not
#: pytest; `[tool.pytest.ini_options]` IS pytest configuration, so `.` may border it.
_XDIST_NAME = re.compile(r"(?<![\w.-])pytest[-_]xdist(?![\w-])", re.IGNORECASE)
_PYTEST_NAME = re.compile(r"(?<![\w-])pytest(?![\w-])", re.IGNORECASE)


def _manifest_texts(root: Path) -> list[str]:
    """Each Python manifest's text with comments removed: a commented-out dependency is
    not a declared one."""
    names = [*_PY_MANIFESTS, *sorted(p.name for p in root.glob("requirements*.txt"))]
    out = []
    for name in names:
        try:
            out.append(
                _COMMENT.sub("", (root / name).read_text(encoding="utf-8", errors="replace"))
            )
        except OSError:
            continue
    return out


def declares_xdist(root: Path) -> bool:
    """Whether the project's manifests declare pytest-xdist."""
    return any(_XDIST_NAME.search(t) for t in _manifest_texts(root))


def uses_pytest(root: Path) -> bool:
    """Evidence the project's tests run under pytest: its own files, or a manifest naming it."""
    if (root / "conftest.py").is_file() or (root / "pytest.ini").is_file():
        return True
    return any(_PYTEST_NAME.search(t) for t in _manifest_texts(root))


def runs_pytest_serially(command: str) -> bool:
    return bool(_PYTEST.search(command)) and not _XDIST_CHOSEN.search(command)


def suggested_test_command(root: Path) -> str:
    """The unit_tests command to propose for a pytest project, or "" for any other."""
    if not uses_pytest(root):
        return ""
    return "pytest -q -n auto" if declares_xdist(root) else "pytest -q"


def parallel_test_advice(command: str, root: Path) -> str:
    """One sentence when ``command`` runs pytest on one core, else ""."""
    if not runs_pytest_serially(command):
        return ""
    serial_ok = "`-p no:xdist` records that serial is deliberate"
    if declares_xdist(root):
        return (
            "runs pytest on ONE core although pytest-xdist is declared: add `-n auto` "
            f"(on a very large machine a fixed `-n N` can be faster); {serial_ok}."
        )
    return (
        "runs pytest on ONE core: declare pytest-xdist (e.g. `uv add --dev pytest-xdist`) "
        f"and add `-n auto`; {serial_ok}."
    )
