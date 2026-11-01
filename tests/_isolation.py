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
import signal
import subprocess
import tempfile
import time
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

#: Where roborev (an external companion some gates shell out to) keeps its daemon's runtime
#: file, relative to that daemon's ``HOME``: ``daemon.<pid>.json``, next to its SQLite
#: ``reviews.db``. A daemon the suite started inherits the scratch ``HOME`` below; when the
#: runner then deletes that ``HOME``, the daemon survives it and keeps holding its address over
#: a deleted database, failing every roborev gate afterwards (B7d61f8c094).
ROBOREV_RUNTIME = Path(".roborev") / "runtime"


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
        atexit.register(_teardown, home)
    (home / "gitconfig").write_text(GLOBAL_CONFIG)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / "xdg")
    env["GIT_CONFIG_GLOBAL"] = str(home / "gitconfig")
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return home


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _daemon_pid(pidfile: Path) -> int | None:
    """The positive pid a roborev daemon recorded in ``daemon.<pid>.json``: its ``pid`` field,
    with the file's ``daemon.<pid>`` name as the fallback — a torn file still being written must
    not hide a live daemon. ``None`` for anything not one positive process id: ``kill(0, sig)``
    and ``kill(-n, sig)`` signal a whole process GROUP, so a corrupt pid must never reach
    ``os.kill``."""
    try:
        pid = int(json.loads(pidfile.read_text())["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        raw = pidfile.name.removeprefix("daemon.").removesuffix(".json")
        try:
            pid = int(raw)
        except ValueError:
            return None
    return pid if pid > 0 else None


def _is_roborev_daemon(pid: int) -> bool:
    """Whether ``pid`` is still the roborev daemon that wrote a pidfile under the scratch
    ``HOME``. A pidfile's pid may have been recycled by an unrelated process its daemon died
    earlier; the command line must still name roborev, or the pid is left alone."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
    except OSError:
        return True
    return b"roborev" in cmdline


def terminate_suite_daemons(home: Path, grace_s: float = 2.0) -> int:
    """Signal every roborev daemon whose runtime pidfile sits under ``home`` (a daemon the
    suite started inherits the scratch ``HOME`` and writes ``daemon.<pid>.json`` there), wait
    up to ``grace_s`` for each to exit, and SIGKILL any survivor. Only a daemon whose pid the
    pidfile names positive and whose command line still says roborev is touched, so the
    operator's real daemon under the real ``HOME`` and any pid an exited daemon's leftover
    file recycled are left alone. Returns how many such daemons it signaled (0 for a leftover
    pidfile naming a pid that was already gone)."""
    runtime = home / ROBOREV_RUNTIME
    if not runtime.is_dir():
        return 0
    pids = [
        pid
        for pidfile in sorted(runtime.glob("daemon.*.json"))
        if (pid := _daemon_pid(pidfile)) is not None and _is_roborev_daemon(pid)
    ]

    def ours_alive() -> list[int]:
        return [pid for pid in pids if _alive(pid) and _is_roborev_daemon(pid)]

    signaled = 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OverflowError):
            continue
        signaled += 1
    deadline = time.monotonic() + grace_s
    alive = ours_alive()
    while alive and time.monotonic() < deadline:
        time.sleep(0.05)
        alive = ours_alive()
    for pid in alive:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OverflowError):
            pass
    return signaled


def _teardown(home: Path) -> None:
    """Remove the scratch ``HOME`` the suite was isolated into — killing any roborev daemon
    the suite spawned there first, so none is orphaned holding its address over a deleted
    database (B7d61f8c094)."""
    terminate_suite_daemons(home)
    shutil.rmtree(home, ignore_errors=True)


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


#: The gate whose command runs in the agent's OWN worktree, where the agent may keep editing
#: while it runs (`ddflow gate run unit_tests`); `ci` and the hooks run in a scratch checkout.
LIVE_TREE_GATE = "unit_tests"


def tracked_status(lines: list[str]) -> list[str]:
    """The `status --porcelain` lines the guard compares. Under the `unit_tests` gate (the
    suite runs in the agent's live worktree) only the INDEX side of each line: what a fixture's
    stray `git add` leaves (`A `, `M `, an intent-to-add ` A`). An unstaged edit (`" M"`, or the
    worktree column turning `M ` into `MM`) is the agent working on a test while the gate runs
    it (B-test-suite-guard-fires-on-concurrent-edit). KNOWN LIMIT, accepted: there the suite
    writing a tracked file WITHOUT `git add` cannot be told from the agent's edit and is not
    caught; everywhere else (a scratch checkout, a hook, a hand run) every line is compared."""
    if os.environ.get("DDFLOW_RUNNING_GATE") != LIVE_TREE_GATE:
        return sorted(lines)
    index_side = []
    for line in lines:
        x, path = line[:1], line[3:]
        if line[:2] == " A":
            index_side.append(f" A {path}")  # intent-to-add: the index holds an entry
        elif x not in (" ", "?", ""):
            index_side.append(f"{x}  {path}")
    return sorted(index_side)


def snapshot(*, status: bool = True) -> dict[str, object]:
    """The real repository's local config, ``core.bare``, HEAD and tracked-file status (less
    ``.ddflow``, which ddflow itself keeps writing)."""
    snap = _config_snapshot()
    snap["head"] = _head()
    if status:
        r = _git("status", "--porcelain", "--untracked-files=no", "--", ".", ":(exclude).ddflow")
        snap["status"] = tracked_status(r.stdout.splitlines()) if r.returncode == 0 else None
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
        config is not None and config.getoption("update_snapshots", False)
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    changed = _changes(_BASELINE, snapshot(status=not _updating(session)))
    if changed:
        print(f"\nREAL repository {REAL_REPO} was modified by the suite: {'; '.join(changed)}")
        session.exitstatus = 1
