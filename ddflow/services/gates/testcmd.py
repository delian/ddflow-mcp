"""How a project runs its tests, and what its runner prints.

One home for four questions that onboarding and the unit_tests gate used to answer each
for itself: which runner the project uses (`detect`), whether it runs on one core and the
parallel command to suggest, and how a runner's verdict lines read (`summary_lines`,
`counts_of`, `last_count_line`) -- B-uni-onboard-testcmd.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

# -- what a runner prints -------------------------------------------------------------

#: A test runner's own verdict lines, wherever in the output they fall: pytest's
#: `=== 3 failed, 112 passed in 4.2s ===` banners (bare under `-q`), unittest's `Ran 12 tests in 0.1s`
#: and `FAILED (failures=2)` / `OK (skipped=1)`.
_VERDICT = r"(passed|failed|errors?|skipped|xfailed|xpassed|deselected)"
_SUMMARY_LINE = re.compile(
    rf"=+ .*\b({_VERDICT[1:-1]}|no tests ran)\b.* =+"
    # pytest -q: the same verdicts, with no banner
    rf"|(\d+ {_VERDICT}\b.*|no tests ran) in \d[\d.]*s\b.*"
    r"|Ran \d+ tests? in \S+"
    r"|(OK|FAILED) \(.*\)"
)
#: `2 passed, 1 failed in 3.41s` and friends, in the same vocabulary; group 1 is the number,
#: group 2 the verdict.
_COUNT = re.compile(rf"(\d+) {_VERDICT}\b")
#: How many summary lines are kept: the first and last half of them when there are more.
MAX_SUMMARY_LINES = 20


def summary_lines(out: str) -> list[str]:
    """The suite's own verdict lines from the WHOLE output, not the tail."""
    found = [ln.strip() for ln in out.splitlines() if _SUMMARY_LINE.fullmatch(ln.strip())]
    if len(found) <= MAX_SUMMARY_LINES:
        return found
    half = MAX_SUMMARY_LINES // 2
    return [*found[:half], f"... {len(found) - 2 * half} more ...", *found[-half:]]


def last_count_line(out: str) -> str:
    """The last line of ``out`` that counts verdicts (``3 passed``), stripped; "" if none.
    Wider than `summary_lines`: a line need not be a whole banner (jest's
    ``Tests: 3 passed, 3 total`` counts)."""
    for line in reversed(out.splitlines()):
        if _COUNT.search(line):
            return line.strip()
    return ""


def counts_of(summary: str) -> dict[str, int]:
    """``{"passed": 112, "failed": 3}`` from a summary line (plurals folded: ``errors`` ->
    ``error``)."""
    return {m.group(2).rstrip("s"): int(m.group(1)) for m in _COUNT.finditer(summary)}


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


# -- which runner a project has -------------------------------------------------------


@dataclass(frozen=True)
class Runner:
    """How this project runs its tests, and where that was read from."""

    family: str  #: python | node | make
    command: str  #: base command WITHOUT a worker count
    evidence: str  #: the file that said so
    workers: str = ""  #: e.g. "-n 12", when parallelism applies and is declared
    #: The unit_tests command `gate run` proposes when none is configured
    #: (`suggested_test_command`): "" for a project that does not use pytest.
    suggested: str = ""


def _text(path: Path) -> str:
    return path.read_text("utf-8", errors="replace") if path.is_file() else ""


def detect(root: Path) -> Runner | None:
    """How this project runs its tests, from its own files; None when nothing says.

    The evidence path is part of the answer: the operator is being asked to trust a
    command, and a command with no file behind it is a guess. Whether pytest is in use and
    whether xdist is declared are `uses_pytest` / `declares_xdist`'s answers, the same the
    unit_tests gate gives (a project that declares them in `requirements-dev.txt` is not
    told otherwise here).
    """
    root = Path(root)
    pyproject = root / "pyproject.toml"
    text = _text(pyproject)
    tests = root / "tests"
    if (
        uses_pytest(root)
        or (root / "tox.ini").is_file()
        or (tests.is_dir() and any(tests.glob("test_*.py")))
    ):
        evidence = str(pyproject) if "[tool.pytest.ini_options]" in text else "tests/ + conftest"
        workers = ""
        if declares_xdist(root):
            # Several agents run this gate at once, so a count that saturates the box is
            # the wrong answer (`auto` included); a quarter of the cores leaves room.
            workers = f"-n {max(2, (os.cpu_count() or 4) // 4)}"
        base = "uv run pytest" if (root / "uv.lock").is_file() else "python -m pytest"
        return Runner("python", base, evidence, workers, suggested_test_command(root))
    package = root / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(_text(package)).get("scripts") or {}
        except json.JSONDecodeError:
            scripts = {}
        if isinstance(scripts, dict) and scripts.get("test"):
            return Runner("node", "npm test --silent", str(package))
    makefile = root / "Makefile"
    if re.search(r"^test:", _text(makefile), re.M):
        return Runner("make", "make test", str(makefile))
    return None
