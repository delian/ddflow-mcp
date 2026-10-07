"""Old reads new: each released ddflow, run against a project the current release wrote
(B-uni-compat-tests; decision D-compat 2).

The project is the newest fixture under tests/fixtures/releases/ with what a FUTURE release
would add layered on: a shard carrying an event kind no release knows, a known kind with a
field it has never had, the current release's own `ddflow.seen` stamps, and config keys and
sections nobody has defined. Each OLD release (a wheel built from its release commit,
`scripts/ci/compat-matrix venv`) must

- read it without crashing: no traceback from any read command;
- take a write it understands (a lesson, a task, a memory) and leave every byte that was
  already there untouched: the existing shards, the config file and its unknown keys;
- leave a project the current release still folds, with the new records in it and the old
  ones unchanged.

Two things D-compat 2 asks of the releases cannot be met by releases already out, and this
file pins what they do instead, so the gap is measured and the lists below cannot outlive it:

- LOG_GUARDED: releases that refuse EVERY log write once a newer ddflow has stamped the log
  (exit 3, "Upgrade ddflow-mcp to >= X"; the current release stamps its logs), wider than the
  direct conflicts D-compat limits refusal to. The others write what they understand. The
  project is also run with the stamps removed, where every release must write.
- ADOPT_OVERWRITES: `adopt` re-renders the managed files (.mcp.json, the driver document,
  settings) a newer ddflow stamped, and no released version refuses or preserves them. The
  guard is the current release's (B-uni-compat-contract).

Slow: a wheel build per release the first time (cached under /tmp/ddflow-compat/venvs).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from projection import differences, plain, project

from ddflow.core.events import version_key
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
RELEASES = ROOT / "tests" / "fixtures" / "releases"
MATRIX = ROOT / "scripts" / "ci" / "compat-matrix"


FIXTURES = sorted(
    (p.name for p in RELEASES.iterdir() if (p / "meta.json").is_file()), key=version_key
)
#: the project the current release wrote is the newest fixture; every earlier one is an old
#: release to run against it
NEWEST, OLD = FIXTURES[-1], FIXTURES[:-1]

#: releases whose guard (D-upgrade-skew-guard) refuses every log write on a log stamped by a
#: newer ddflow. A release that stops refusing, or one that starts, fails the test: edit here.
LOG_GUARDED = {"0.1.15", "0.1.18"}
#: version -> the files its `adopt` rewrites although a newer ddflow stamped them, exactly
ADOPT_OVERWRITES: dict[str, set[str]] = {
    "0.1.3": {".mcp.json", ".ddflow/.gitignore", "docs/ddflow/drivers/implement-phase.md"},
    "0.1.10": {".mcp.json", ".ddflow/.gitignore", "docs/ddflow/drivers/implement-phase.md"},
    "0.1.15": {".mcp.json", ".claude/settings.json", "docs/ddflow/drivers/implement-phase.md"},
    "0.1.18": {".claude/settings.json", ".mcp.json", "docs/ddflow/drivers/implement-phase.md"},
}

#: (version, command) -> a read that fails in that release on ITS OWN projects too, so it says
#: nothing about the newer one: 0.1.3's `decision list` dies with `KeyError: 'live'` on any
#: project that has a decision. Declared so a fixed or unrelated failure is told apart.
OWN_BUGS = {("0.1.3", "decision list"): "KeyError: 'live'"}

FUTURE_SHARD = "zz-future.jsonl"
FUTURE_CONFIG_TOP = "future_top_level_key = 1\n"
FUTURE_CONFIG_SECTION = '\n[future_section]\nknob = "kept"\nlist = [1, 2]\n'


def _event(kind: str, subject: str, n: int, data: dict, **extra: object) -> str:
    event = {
        "agent": "fx-future",
        "data": data,
        "id": f"f{n:024d}",
        "kind": kind,
        "lamport": 1000 + n,
        "schema": 1,
        "subject": subject,
        "ts": f"2099-01-01T00:00:{n:02d}.000000Z",
        **extra,
    }
    return json.dumps(event, sort_keys=True)


def _write_future_layer(root: Path, *, stamped: bool) -> None:
    lessons = (root / ".ddflow" / "events").glob("*.jsonl")
    # a lesson event copied from the log, plus a field no release defines, on a new id
    lesson = next(
        json.loads(line)
        for path in sorted(lessons)
        for line in path.read_text(encoding="utf-8").splitlines()
        if '"lesson.recorded"' in line
    )
    lesson.update(
        id=f"f{9:024d}",
        subject="L-FUTURE",
        lamport=1009,
        future_envelope_key={"nested": [1, 2, 3]},
    )
    lesson["data"] = {**lesson["data"], "id": "L-FUTURE", "future_field": "kept"}
    lines = [
        _event("future.thing", "X-FUTURE", 2, {"anything": ["goes", 1, None]}),
        json.dumps(lesson, sort_keys=True),
    ]
    if not stamped:
        # what a release that never recorded a version stamp would have left
        for path in (root / ".ddflow" / "events").glob("*.jsonl"):
            kept = [x for x in path.read_text().splitlines() if '"ddflow.seen"' not in x]
            path.write_text("".join(x + "\n" for x in kept))
    (root / ".ddflow" / "events" / FUTURE_SHARD).write_text("\n".join(lines) + "\n")
    cfg = root / ".ddflow" / "config.toml"
    cfg.write_text(FUTURE_CONFIG_TOP + cfg.read_text(encoding="utf-8") + FUTURE_CONFIG_SECTION)


def _template(factory: pytest.TempPathFactory, *, stamped: bool) -> Path:
    """The project the current release wrote, plus the future layer, committed in a git repo
    of its own. A template: each test copies it (the old release writes to its copy)."""
    root = factory.mktemp("newer") / "proj"
    shutil.copytree(RELEASES / NEWEST / "project", root)
    _write_future_layer(root, stamped=stamped)
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t.invalid"]
    subprocess.run([*git, "init", "--quiet", "-b", "main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "--quiet", "--no-verify", "-m", "newer"], check=True)
    return root


@pytest.fixture(scope="module")
def unstamped_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Future records and config, and every `ddflow.seen` stamp removed: no old release has
    reason to refuse."""
    return _template(tmp_path_factory, stamped=False)


@pytest.fixture(scope="module")
def stamped_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """As the current release wrote it, `ddflow.seen` stamps included, plus the future layer."""
    return _template(tmp_path_factory, stamped=True)


_VENVS: dict[str, Path] = {}


def _venv(version: str) -> Path:
    if version not in _VENVS:
        proc = subprocess.run(
            [sys.executable, str(MATRIX), "venv", version], capture_output=True, text=True
        )
        assert proc.returncode in (0, 2), proc.stderr[-1500:]
        if proc.returncode == 2:
            # exit 2: the release's commit is not in this checkout (a shallow clone). Say so,
            # never pass; any other failure -- a build, uv, a version mismatch -- is a failure
            pytest.skip(f"cannot build ddflow {version}: {proc.stderr.strip()[-300:]}")
        _VENVS[version] = Path(proc.stdout.strip())
    return _VENVS[version]


class Old:
    """One old release run against its own copy of the newer project."""

    def __init__(self, version: str, template: Path, tmp: Path) -> None:
        self.version = version
        self.root = tmp / "proj"
        shutil.copytree(template, self.root)
        self.home = tmp / "home"
        self.home.mkdir()
        venv = _venv(version)
        self.env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("DDFLOW_", "GIT_", "VIRTUAL_ENV", "PYTHON"))
        }
        self.env.update(
            PATH=os.pathsep.join([str(venv / "bin"), "/usr/bin", "/bin"]),
            HOME=str(self.home),
            NO_COLOR="1",
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@t.invalid",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t.invalid",
        )

    def run(self, *argv: str) -> tuple[int, str, str]:
        p = subprocess.run(
            ["ddflow", "--agent", "fx-old", *argv],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return p.returncode, p.stdout, p.stderr

    def snapshot(self) -> dict[str, bytes]:
        """Every file but git's own, by relative path."""
        return {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in sorted(self.root.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(self.root).parts
        }


@pytest.fixture(params=OLD)
def old(request: pytest.FixtureRequest, stamped_project: Path, tmp_path: Path) -> Old:
    return Old(request.param, stamped_project, tmp_path)


@pytest.fixture(params=OLD)
def old_unstamped(request: pytest.FixtureRequest, unstamped_project: Path, tmp_path: Path) -> Old:
    return Old(request.param, unstamped_project, tmp_path)


def _changed(old: Old, before: dict[str, bytes]) -> set[str]:
    """Files whose bytes differ after the run (a derived index may be rebuilt by a read)."""
    after = old.snapshot()
    return {
        p
        for p, data in before.items()
        if after.get(p) != data and not p.startswith(".ddflow/index")
    }


def test_the_matrix_has_old_releases() -> None:
    """A fixture directory that silently went missing would make every test below vacuous."""
    assert {"0.1.3", "0.1.10", "0.1.15", "0.1.18"} <= set(OLD)
    assert set(ADOPT_OVERWRITES) == set(OLD), "every old release needs a declared adopt outcome"
    assert LOG_GUARDED <= set(OLD)


@pytest.mark.parametrize(
    "argv",
    [
        ("status",),
        ("brief",),
        ("show", "T1"),
        ("board",),
        ("replay",),
        ("bug", "list", "--all"),
        ("decision", "list"),
        ("doctor",),
    ],
    ids="-".join,
)
def test_old_reads_the_newer_project(old: Old, argv: tuple[str, ...]) -> None:
    """Reads work even where writes are refused, and change no tracked file."""
    before = old.snapshot()
    code, out, err = old.run(*argv)
    assert "Traceback" not in err + out, (err + out)[-1500:]
    own = OWN_BUGS.get((old.version, " ".join(argv)))
    if own:
        assert code == 1 and own in err + out, (code, out, err)
        return
    assert not re.search(r"^\w*Error\b", err + out, re.M), (err + out)[-1500:]
    # doctor's exit 1 is a finding about the project, not a failure to read it
    assert code in ((0, 1, 2) if argv[0] == "doctor" else (0, 2)), (code, out, err)
    assert out.strip() or err.strip()
    assert _changed(old, before) == set()


WRITES = [
    ("lesson", "add", "--id", "L-OLD", "--title", "Written by an old release", "--rule", "r",
     "--why", "w", "--how", "h"),
    ("memory", "add", "A fact an old release recorded"),
    ("task", "add", "T-OLD", "--phase", "P1", "--title", "Added by an old release"),
]  # fmt: skip


def test_old_write_preserves_what_it_does_not_understand(old_unstamped: Old) -> None:
    old = old_unstamped
    before = old.snapshot()
    for argv in WRITES:
        code, out, err = old.run(*argv)
        assert code == 0, (argv, code, out, err)
        assert "Traceback" not in err + out

    # nothing that was there changed by a single byte -- the future shard, the config with
    # its unknown keys -- the old release only added an event file of its own
    assert _changed(old, before) == set()
    config = old.snapshot()[".ddflow/config.toml"].decode()
    assert FUTURE_CONFIG_TOP in config and FUTURE_CONFIG_SECTION in config
    assert any(p not in before and p.startswith(".ddflow/events/") for p in old.snapshot())

    # the current release reads the result: the unknown kind skipped and named, the old
    # release's records there, every record the newer project had unchanged
    state = fold(EventLog(old.root, cache_writes=False).read_all(), strict=False)
    assert list(state.skipped_kinds) == ["future.thing"]
    assert "L-OLD" in state.lessons and "T-OLD" in state.items
    assert any("an old release recorded" in m.text for m in state.memories.values())
    assert state.lessons["L-FUTURE"].title  # the field no release defines did not break it
    got = plain(state)
    for name, records in _newest_projection().items():
        if name in {"items", "lessons", "decisions", "research", "sessions"}:
            for key, record in records.items():
                assert differences(record, got[name][key], f"{name}.{key}") == []


def _newest_projection() -> dict:
    return project(RELEASES / NEWEST / "project")


def test_old_log_write_on_a_stamped_log(old: Old) -> None:
    """Refused where the release has the guard, and then nothing changes; written where it
    does not."""
    before = old.snapshot()
    code, out, err = old.run(*WRITES[0])
    assert "Traceback" not in err + out
    if old.version in LOG_GUARDED:
        assert code == 3, (code, out, err)
        assert "upgrade ddflow" in (out + err).lower()
        assert _changed(old, before) == set()
        assert not {
            p
            for p in set(old.snapshot()) - set(before)
            if p.startswith(".ddflow/events/") and p.endswith(".jsonl")
        }
    else:
        assert code == 0, f"{old.version} is not in LOG_GUARDED but refused: {out}{err}"
        assert _changed(old, before) == set()
        # wrote something: a release that exits 0 and records nothing is not "written"
        assert any(
            p.startswith(".ddflow/events/") and p.endswith(".jsonl") and p not in before
            for p in old.snapshot()
        )


def test_old_adopt_over_newer_managed_files(old: Old) -> None:
    """The write D-compat calls a direct conflict: re-rendering files a newer ddflow wrote.
    No released version refuses it; this records what each does."""
    before = old.snapshot()
    code, out, err = old.run("adopt", "--agents", "claude", "--launch", "uvx")
    assert "Traceback" not in err + out
    changed = _changed(old, before)
    if code == 3:
        assert "upgrade ddflow" in (out + err).lower()
        assert changed == set()
        pytest.fail(f"{old.version} refuses adopt now; ADOPT_OVERWRITES must drop it")
    assert code == 0, (code, out, err)
    assert changed == ADOPT_OVERWRITES[old.version]
    # the event log is not among the things it rewrote
    assert not any(p.startswith(".ddflow/events/") for p in changed)
