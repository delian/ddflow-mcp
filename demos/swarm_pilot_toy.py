"""The swarm pilot's toy phase and its sandbox (SW-pilot-toy, D-swarm-pilot-2026-11-05).

Twelve doc-and-test items with disjoint globs and a dependency chain of four: wide enough for
three lanes, narrow enough that a fleet cannot just run everything at once. They live in a
sandbox repository so red main in the real project cannot force an item through or skew a figure.

The sandbox never touches the host, and each of these is checked, not assumed:

* it is created only outside the host, outside any other work tree, in an empty directory;
* it is opened only with its manifest, as its own work tree, with no remote;
* every process gets an allow-listed environment: no GIT_* redirect, no DDFLOW_* setting, no
  provider key, and a HOME of its own, so nothing reads or writes the person's configuration;
* only the verbs a pilot needs can be called, so nothing here reaches a reviewer or a model.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHASE = "TOY"
MANIFEST = ".ddflow/local/swarm-pilot.json"
#: Every agent the harness writes as starts with this, so a stray event names its source.
AGENT_PREFIX = "toy-"
LEAD = f"{AGENT_PREFIX}lead"
CALL_TIMEOUT_S = 300.0
#: Slots for the fleet the harness runs, and the sample floor of a full toy phase.
SLOTS = 3
MIN_SAMPLES = 10
#: The ddflow verbs a pilot needs. `review` and `lanes` are out: they reach a model.
VERBS = frozenset(
    {"init", "phase", "task", "next", "claim", "gate", "merge", "complete", "metrics", "show"}
)
#: The git verbs the harness uses: `config` is not one, so nothing can repoint the repository.
GIT_VERBS = frozenset({"add", "commit", "remote", "rev-parse"})
COMMITTER = ("swarm pilot sandbox", "toy@sandbox.invalid")
PASSED_ENV = ("LANG", "LC_ALL")


class Refused(Exception):
    """The harness will not run here: the words say why."""


@dataclass(frozen=True)
class ToyItem:
    id: str
    title: str
    needs: tuple[str, ...] = ()
    #: How many first attempts its gate sends back, so a rework rate has something to count.
    rework: int = 0

    @property
    def doc(self) -> str:
        return f"toy/{self.id}.md"

    @property
    def test(self) -> str:
        return f"tests/test_{self.id.replace('-', '_')}.py"

    @property
    def globs(self) -> tuple[str, str]:
        return self.doc, self.test


ITEMS = (
    ToyItem("toy-01", "Glossary of the toy domain"),
    ToyItem("toy-02", "Install notes for the toy tool"),
    ToyItem("toy-03", "Configuration reference page"),
    ToyItem("toy-04", "Troubleshooting checklist"),
    ToyItem("toy-05", "Release notes template", rework=1),
    ToyItem("toy-06", "Contributing guide"),
    ToyItem("toy-07", "Tutorial built on the glossary", ("toy-01",)),
    ToyItem("toy-08", "Quick start built on the install notes", ("toy-02",)),
    ToyItem("toy-09", "Worked configuration examples", ("toy-03",)),
    ToyItem("toy-10", "User guide joining tutorial and quick start", ("toy-07", "toy-08")),
    ToyItem("toy-11", "Operations guide from the examples", ("toy-09",)),
    ToyItem("toy-12", "Handbook index over both guides", ("toy-10", "toy-11")),
)


def clean_env(home: Path) -> dict[str, str]:
    """The whole environment of a sandbox process: an allow-list, so a key never leaks in. Temp
    files stay in ``home``, and no PATH entry resolves into the host."""
    host = ROOT.resolve()
    path = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    env = {k: os.environ[k] for k in PASSED_ENV if k in os.environ}
    return env | {
        "PATH": os.pathsep.join(p for p in path if not Path(p).resolve().is_relative_to(host)),
        "HOME": str(home),
        "TMPDIR": str(home),
        "PYTHONPATH": str(ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": COMMITTER[0],
        "GIT_AUTHOR_EMAIL": COMMITTER[1],
        "GIT_COMMITTER_NAME": COMMITTER[0],
        "GIT_COMMITTER_EMAIL": COMMITTER[1],
    }


def _top_of(path: Path) -> str:
    """The work tree git maps ``path`` to ("" when none), whatever this process inherited."""
    p = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
        env=clean_env(path),
    )
    return p.stdout.strip() if p.returncode == 0 else ""


def _existing(path: Path) -> Path:
    while not path.exists():
        path = path.parent
    return path


def apart_from_host(path: Path) -> Path:
    """The resolved ``path``, neither inside the host nor holding it; `Refused` otherwise."""
    real, host = path.resolve(), ROOT.resolve()
    if real.is_relative_to(host):
        raise Refused(f"{real} is inside the host repository {host}")
    if host.is_relative_to(real):
        raise Refused(f"{real} holds the host repository {host}")
    return real


def guard_new(path: Path) -> Path:
    """The resolved ``path`` if a sandbox may be created there; `Refused` otherwise."""
    real = apart_from_host(path)
    if real.exists() and (not real.is_dir() or any(real.iterdir())):
        raise Refused(f"{real} is not empty")
    top = _top_of(_existing(real))
    if top:
        raise Refused(f"{real} is inside the git work tree {top}")
    return real


def _config(min_samples: int) -> str:
    return f"""# Written by demos/swarm_pilot_toy.py: a stub run has no reviewer and no model.
[agent]
reviewer_independence = "off"

[gates]
task_pipeline = ["implement", "unit_tests", "merge"]

[gate.unit_tests]
command = "{shlex.quote(sys.executable)} -m unittest discover -q -s tests -t ."

[schedule]
parallel = "fixed"
max_parallel_tasks = {SLOTS}
max_parallel_min = {SLOTS}

[metrics]
min_samples = {min_samples}
"""


class Sandbox:
    """One pilot repository, apart from the host however it is built; `setup` and `open` run the
    rest of the guards."""

    def __init__(self, repo: Path) -> None:
        self.repo = apart_from_host(repo)
        self.home = apart_from_host(self.repo.parent / f"{self.repo.name}.home")
        self.env = clean_env(self.home)

    @classmethod
    def open(cls, path: Path) -> Sandbox:
        sb = cls(path)
        if not (sb.repo / MANIFEST).is_file():
            raise Refused(f"{sb.repo} is no sandbox: {MANIFEST} is missing")
        sb.check()
        return sb

    def check(self) -> None:
        """Refuse a repository that is not its own work tree or that has somewhere to push."""
        if _top_of(self.repo) != str(self.repo) or not (self.repo / ".git").is_dir():
            raise Refused(f"{self.repo} is not its own git work tree")
        if self.git("remote").strip():
            raise Refused(f"{self.repo} has a remote: a sandbox has nowhere to push")

    def git(self, *argv: str, cwd: Path | None = None) -> str:
        """Stdout of one git verb in this sandbox or its worktrees; `Refused` otherwise."""
        tree = (cwd or self.repo).resolve()
        if not argv or argv[0] not in GIT_VERBS or not tree.is_relative_to(self.repo):
            raise Refused(f"git {' '.join(argv[:2])} in {tree}: not a pilot verb")
        p = subprocess.run(
            ["git", "-C", str(tree), *argv],
            capture_output=True,
            text=True,
            check=True,
            env=self.env,
            timeout=CALL_TIMEOUT_S,
        )
        return p.stdout

    def ddflow(self, *argv: str, agent: str = LEAD, as_json: bool = False) -> tuple[int, str, str]:
        """(exit, stdout, stderr) of one ddflow verb in this sandbox; `Refused` off the list."""
        if argv[0] not in VERBS:
            raise Refused(f"{argv[0]!r} is not a pilot verb")
        if "--force" in argv:
            raise Refused("--force would skew the figures: a pilot item completes on its gates")
        cmd = [sys.executable, "-P", "-m", "ddflow", "--repo", str(self.repo), "--agent", agent]
        p = subprocess.run(
            [*cmd, *(["--json"] if as_json else []), *argv],
            capture_output=True,
            text=True,
            check=False,
            cwd=self.repo,
            env=self.env,
            timeout=CALL_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
        )
        return p.returncode, p.stdout, p.stderr

    def must(self, *argv: str, agent: str = LEAD) -> str:
        code, out, err = self.ddflow(*argv, agent=agent)
        if code:
            raise RuntimeError(f"ddflow {' '.join(argv)}: exit {code}: {err or out}")
        return out

    def must_json(self, *argv: str, agent: str = LEAD) -> str:
        code, out, err = self.ddflow(*argv, agent=agent, as_json=True)
        if code:
            raise RuntimeError(f"ddflow {' '.join(argv)}: exit {code}: {err or out}")
        return out

    def manifest(self) -> dict:
        return json.loads((self.repo / MANIFEST).read_text())

    def write_manifest(self, data: dict) -> None:
        (self.repo / MANIFEST).write_text(json.dumps(data, indent=2) + "\n")


def _scaffold(sb: Sandbox) -> None:
    sb.repo.mkdir(parents=True, exist_ok=True)
    sb.home.mkdir(exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(sb.repo)],
        check=True,
        env=sb.env,
        capture_output=True,
    )
    (sb.repo / "tests").mkdir()
    (sb.repo / "tests" / "__init__.py").write_text("")
    (sb.repo / "README.md").write_text("# Swarm pilot sandbox\n\nThrowaway: safe to delete.\n")


def setup(
    path: Path, items: tuple[ToyItem, ...] = ITEMS, min_samples: int = MIN_SAMPLES
) -> Sandbox:
    """A fresh sandbox at ``path`` holding the toy phase, every setup change committed."""
    sb = Sandbox(guard_new(path))
    _scaffold(sb)
    sb.must("init")
    local = sb.repo / MANIFEST
    local.parent.mkdir(parents=True, exist_ok=True)
    (local.parent / "config.toml").write_text(_config(min_samples))
    sb.write_manifest({"sandbox": True, "items": [i.id for i in items], "worker": "none"})
    sb.git("add", "-A")
    sb.git("commit", "-qm", "ddflow: adopt")
    sb.must("phase", "add", PHASE, "--title", "Swarm pilot toy phase")
    for it in items:
        needs = [d for d in it.needs if d in {i.id for i in items}]
        sb.must(
            "task", "add", it.id, "--phase", PHASE, "--title", it.title, "--new",
            "--globs", ",".join(it.globs),
            *(["--needs", ",".join(needs)] if needs else ["--tags", "no-deps"]),
        )  # fmt: skip
    sb.check()
    return sb
