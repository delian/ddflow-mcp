"""Keep the suite off the REAL repository (B1d82c6b76f).

Loaded as a plugin by the root ``conftest.py``, before any test module imports.

Root cause. A suite run from a git hook (the gate runs `ci` / `unit_tests` from one) inherits
``GIT_DIR`` and ``GIT_INDEX_FILE``, and git lets those override ``-C``. Every fixture that
does ``git init`` / ``git -C tmp config user.name Test`` / ``git -C tmp commit`` then acted
on the repository running the gate: its ``.git/config`` got a stray identity, ``git init
--bare`` set ``core.bare = true`` in it, and "Test init" commits and a staged one-line
README.md landed on the item's branch. So, before anything runs:

* every variable that makes git ignore the directory it is told to work in is removed from
  ``os.environ`` (``REPO_VARS``), and
* ``HOME``, ``XDG_CONFIG_HOME``, ``GIT_CONFIG_GLOBAL`` and ``GIT_CONFIG_SYSTEM`` point at a
  scratch directory, so no test reads or writes the developer's own git configuration.

And a guard fails the run, naming the test module, if the real repository's ``.git/config``,
``core.bare`` or tracked-file status changed anyway.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

#: Variables that redirect git away from the directory it was told to use.
REPO_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_QUARANTINE_PATH",
    "GIT_INTERNAL_SUPER_PREFIX",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
)

#: What the scratch global git configuration carries: the identity the `repo` fixture uses
#: (so a test that commits without configuring one still can), and nothing else.
SUITE_EMAIL = "t@example.com"
GLOBAL_CONFIG = f"[user]\n\tname = Test\n\temail = {SUITE_EMAIL}\n"

REAL_REPO = Path(__file__).resolve().parents[1]
HOME_ENV = "DDFLOW_SUITE_HOME"
BASELINE_ENV = "DDFLOW_SUITE_REAL_REPO_BASELINE"

#: Config keys another process legitimately writes to a shared repository while the suite runs.
_VOLATILE = ("branch.", "remote.", "submodule.", "worktree.")


def isolate_environment(env: dict[str, str] | os._Environ[str] = os.environ) -> Path:
    """Strip ``REPO_VARS`` and point git's global/system configuration and ``HOME`` at a
    scratch directory (one per suite run: xdist workers inherit it). Returns that directory."""
    for var in REPO_VARS:
        env.pop(var, None)
    named = env.get(HOME_ENV, "")
    home = Path(named)
    if not named or not home.is_dir():
        home = Path(tempfile.mkdtemp(prefix="ddflow-suite-home-"))
        env[HOME_ENV] = str(home)
        atexit.register(shutil.rmtree, home, ignore_errors=True)
    (home / "gitconfig").write_text(GLOBAL_CONFIG)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / "xdg")
    env["GIT_CONFIG_GLOBAL"] = str(home / "gitconfig")
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return home


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REAL_REPO), *args], capture_output=True, text=True, timeout=60
    )


def _config_snapshot() -> dict[str, object]:
    common = _git("rev-parse", "--git-common-dir").stdout.strip()
    cfg = (REAL_REPO / common / "config") if common else None
    if cfg is None or not cfg.is_file():
        return {"config": None, "bare": None}
    listed = subprocess.run(
        ["git", "config", "--file", str(cfg), "--list"], capture_output=True, text=True, timeout=60
    ).stdout.splitlines()
    kept = sorted(line for line in listed if not line.startswith(_VOLATILE))
    bare = [line for line in listed if line.startswith("core.bare=")]
    return {"config": kept, "bare": bare}


#: The subjects of the commits the suite's own fixtures make ("init" by the author the
#: fixtures configure): finding one on the real repository's branch is the leak itself.
STRAY_SUBJECTS = ("init", "Test init")


def _head() -> str:
    return _git("rev-parse", "-q", "--verify", "HEAD").stdout.strip()


def stray_commits(since: str) -> list[str]:
    """Fixture-made commits on the real repository's HEAD since ``since`` (a commit id), and a
    line saying so when ``since`` is no longer an ancestor of HEAD (the branch was rewritten)."""
    head = _head()
    if not since or not head or head == since:
        return []
    if _git("merge-base", "--is-ancestor", since, head).returncode != 0:
        return [f"HEAD {since[:8]} is no longer an ancestor of {head[:8]} (branch rewritten)"]
    rows = _git("log", "--format=%h %ae %s", f"{since}..{head}").stdout.splitlines()
    out = []
    for row in rows:
        sha, email, subject = [*row.split(" ", 2), "", ""][:3]
        if email == SUITE_EMAIL or subject in STRAY_SUBJECTS:
            out.append(f"{sha} {subject}")
    return out


def snapshot(*, status: bool = True) -> dict[str, object]:
    """The real repository's local config, ``core.bare``, HEAD and tracked-file status (less
    ``.ddflow``, which ddflow itself keeps writing)."""
    snap = _config_snapshot()
    snap["head"] = _head()
    if status:
        r = _git("status", "--porcelain", "--untracked-files=no", "--", ".", ":(exclude).ddflow")
        snap["status"] = sorted(r.stdout.splitlines()) if r.returncode == 0 else None
    return snap


def _changes(before: dict[str, object], after: dict[str, object]) -> list[str]:
    out = []
    if before.get("head") and (stray := stray_commits(str(before["head"]))):
        out.append(f"fixture commits on the real branch: {stray}")
    for key in sorted(after):
        if key != "head" and key in before and before[key] != after[key]:
            old, new = before[key] or [], after[key] or []
            gone = [x for x in old if x not in new]  # type: ignore[union-attr]
            came = [x for x in new if x not in old]  # type: ignore[union-attr]
            out.append(f"{key}: removed {gone}, added {came}")
    return out


def _baseline() -> dict[str, object]:
    raw = os.environ.get(BASELINE_ENV)
    if raw:
        return json.loads(raw)
    snap = snapshot()
    os.environ[BASELINE_ENV] = json.dumps(snap)
    return snap


isolate_environment()
_BASELINE = _baseline()


@pytest.fixture(scope="module", autouse=True)
def _real_repo_config_untouched(request: pytest.FixtureRequest):
    """After each test module: the real repository's config and ``core.bare`` are as they were."""
    yield
    changed = _changes(_BASELINE, snapshot(status=False))
    if changed:
        pytest.fail(
            f"{request.module.__name__} (or a test run beside it) changed the REAL repository "
            f"{REAL_REPO}: {'; '.join(changed)}. A git command ran without an explicit "
            "cwd/-C, or git was redirected by GIT_DIR (tests/_isolation.py).",
            pytrace=False,
        )


def _updating(session: pytest.Session) -> bool:
    """Whether this session re-records pin files (a snapshot update, `DDFLOW_UPDATE_GOLDEN`):
    changing tracked files is its job (`ddflow pins regenerate` runs it)."""
    config = getattr(session, "config", None)
    return bool(os.environ.get("DDFLOW_UPDATE_GOLDEN")) or bool(
        config is not None and config.getoption("snapshot_update", False)
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    changed = _changes(_BASELINE, snapshot(status=not _updating(session)))
    if changed:
        print(f"\nREAL repository {REAL_REPO} was modified by the suite: {'; '.join(changed)}")
        session.exitstatus = 1
