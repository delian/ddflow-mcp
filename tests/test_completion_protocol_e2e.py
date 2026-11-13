"""End to end: the real argcomplete protocol (environment variables, the answer on fd 8)
against the real ``ddflow`` entry in a subprocess -- the path a shell's TAB takes.

Defect (Bcomp-engine-never-answers): nothing called ``argcomplete.autocomplete``, so the
registered completer was never answered and ``ddflow`` ran its normal parser instead
("the following arguments are required: cmd"). The earlier completion tests called the tree,
values and ids functions directly and never drove this protocol.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from _wait import scaled  # noqa: E402

IFS = "\x0b"
_SCRIPT = Path(sys.executable).parent / "ddflow"
#: The real entries: the setuptools console script (when installed) and `python -m ddflow`.
ENTRIES = [pytest.param([sys.executable, "-m", "ddflow"], id="module")]
if _SCRIPT.is_file():
    ENTRIES.append(pytest.param([str(_SCRIPT)], id="console-script"))


def _env(extra: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("_ARGCOMPLETE", "COMP_LINE")}
    env["PYTHONPATH"] = str(ROOT)
    env.update(extra)
    return env


def tab(entry: list[str], line: str, cwd: Path) -> list[str]:
    """What the shell would offer for ``line`` (cursor at its end), via the argcomplete
    protocol: the registration's environment, the candidates read from fd 8."""
    read_fd, write_fd = os.pipe()
    env = _env(
        {
            "_ARGCOMPLETE": "1",
            "_ARGCOMPLETE_IFS": IFS,
            "_ARGCOMPLETE_SHELL": "bash",
            "COMP_LINE": line,
            "COMP_POINT": str(len(line)),
        }
    )

    devnull = os.open(os.devnull, os.O_WRONLY)

    def _fds() -> None:
        # the registration's `8>&1 9>&2`: candidates on fd 8, the debug stream on fd 9
        os.dup2(write_fd, 8)
        os.dup2(devnull, 9)

    proc = subprocess.Popen(
        entry,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=_fds,  # noqa: PLW1509 -- the one way to give the child fd 8 and 9
        close_fds=False,  # fds 8 and 9 are set up by preexec_fn, after the default close
    )
    os.close(write_fd)
    os.close(devnull)
    watchdog = threading.Timer(120, proc.kill)  # a wedged child ends the read at EOF
    watchdog.start()
    try:
        with os.fdopen(read_fd) as fh:  # read while the child runs: a big answer cannot block it
            out = fh.read()
    finally:
        watchdog.cancel()
    assert proc.wait(timeout=120) in (0, 1)
    return [w.strip() for w in out.split(IFS) if w.strip()]


@pytest.fixture
def project(tmp_path):
    d = tmp_path / "proj"
    (d / ".ddflow").mkdir(parents=True)
    con = sqlite3.connect(d / ".ddflow" / "index.db-aaaaaaaa")
    con.executescript("create table items(id text, kind text, title text, state text);")
    con.executemany(
        "insert into items values(?,?,?,?)",
        [
            ("ZED-alpha", "task", "t", "open"),
            ("ZED-beta", "task", "t", "done"),
            ("P-zed", "phase", "p", "open"),
        ],
    )
    con.commit()
    con.close()
    return d


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_command_prefix_completes(entry, project):
    assert "completion" in tab(entry, "ddflow comple", project)


@pytest.mark.parametrize("entry", ENTRIES)
def test_the_completion_subcommands_complete(entry, project):
    got = tab(entry, "ddflow completion ", project)
    assert {"bash", "zsh", "fish", "install", "uninstall"} <= set(got)


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_state_value_completes_from_the_project(entry, project):
    got = tab(entry, "ddflow list --state ", project)
    assert {"open", "done"} <= set(got)


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_record_id_completes_from_the_project_index(entry, project):
    assert tab(entry, "ddflow show ZED-a", project) == ["ZED-alpha"]


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_gate_name_completes(entry, project):
    got = tab(entry, "ddflow gate record ZED-alpha rub", project)
    assert "rubber_duck" in got


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_phase_id_completes(entry, project):
    assert "P-zed" in tab(entry, "ddflow task add x --phase P-z", project)


def test_a_normal_run_is_untouched_by_the_hook(project):
    """Without the request variable the entry runs the normal CLI."""
    probe = subprocess.run(
        [sys.executable, "-m", "ddflow", "--version"],
        cwd=project,
        env=_env({}),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert probe.returncode == 0 and "ddflow" in probe.stdout


def test_a_tab_meets_the_latency_budget(project):
    entry = [sys.executable, "-m", "ddflow"]
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        tab(entry, "ddflow show ZED-a", project)
        best = min(best, time.perf_counter() - start)
    assert best < scaled(6.0), best


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")
def test_real_zsh_registers_the_command_it_will_ask(project):
    """zsh with the completion system loaded evals the real activation output: the entry
    the registration will run is the one that answers the protocol above."""
    exe = _SCRIPT if _SCRIPT.is_file() else None
    if exe is None:
        pytest.skip("no console script in this environment")
    env = _env({"HOME": str(project), "PATH": f"{exe.parent}:{os.environ['PATH']}"})
    probe = subprocess.run(
        [
            "zsh",
            "-fc",
            'autoload -Uz compinit; compinit -u; eval "$(ddflow completion zsh)"; '
            'print -r -- "REG=${_comps[ddflow]}"',
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "REG=_python_argcomplete" in probe.stdout, probe.stdout + probe.stderr


def test_a_request_without_argcomplete_installed_runs_nothing(project):
    """The shell asked a question: with argcomplete missing the CLI must not run the
    words as a command (it exits quietly)."""
    env = _env(
        {
            "_ARGCOMPLETE": "1",
            "COMP_LINE": "ddflow bug fixed X",
            "PYTHONPATH": f"{ROOT}:{project}",
        }
    )
    (project / "argcomplete.py").write_text("raise ImportError('hidden for the test')\n")
    probe = subprocess.run(
        [sys.executable, "-m", "ddflow", "bug", "fixed", "X"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert probe.returncode == 1
    assert probe.stdout == "" and probe.stderr == ""


@pytest.mark.parametrize(
    ("argv0", "orig", "want"),
    [
        ("/x/bin/ddflow", [], True),
        ("-m", ["python", "-m", "ddflow", "list"], True),
        ("-m", ["python", "-m", "ddflow.surfaces.cli"], True),
        ("-m", ["python", "-m", "pytest"], False),
        ("/x/__main__.py", ["python", "/x/__main__.py"], False),
        ("/x/ddflow/__main__.py", ["python", "-m", "ddflow"], True),
        ("/x/__main__.py", ["python", "/x/__main__.py", "-m", "ddflow"], False),
        ("-m", ["python", "-X", "dev", "-m", "ddflow"], True),
        ("-m", ["python", "-c", "-0 or 1", "-m", "ddflow"], False),
        ("/x/bin/other", [], False),
    ],
)
def test_only_ddflow_launches_count_as_a_request(monkeypatch, argv0, orig, want):
    from ddflow.surfaces import completion_boot as B

    monkeypatch.setenv("_ARGCOMPLETE", "1")
    assert B.asked(argv0, orig) is want
    monkeypatch.delenv("_ARGCOMPLETE")
    assert B.asked(argv0, orig) is False
