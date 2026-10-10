"""The dry-run upgrade: rehearse upgrading a project on a throwaway clone.

Decisions D-dry-run-from-running-version and D-upgrade-on-mcp-connect, task
B-upgrade.12-dry-run. Before an operator upgrades a project, this proves on a COPY that going
from the ddflow they run now to the one on the tree is smooth. Nothing here touches the
project it rehearses, the ddflow installed on the machine or an MCP server that is running:

- the project is only READ, by `git clone --local` (and its log, snapshot tags and backups compared before and after, so
  the report can say the rehearsal left no trace);
- every command runs in a scratch directory with a private HOME, no `DDFLOW_*` variables and no
  network (`DDFLOW_NO_UPDATE_CHECK`), against a clone;
- the NEW ddflow is this source tree, run as a subprocess with PYTHONPATH; the OLD one is a
  command the caller names (an installed release, or one built from the clone at the commit
  the installed ddflow came from) or, for tests, this same code declaring an older version.
  Without an old one the steps that need it report `unavailable`, never a pass.

The rehearsal, in the order a report lists it (the numbers are the task's):

1. clone the project at the commit the installed ddflow was built from;
2. read it with the new code (status, board, doctor, brief, history, replay, show) and digest
   the state it folds into;
3. `upgrade --plan`: every pending change, repair and migration; an item the operator would be
   asked to confirm is a FINDING (the preview names it);
4. `upgrade --apply --backup snapshot`: the digest of the user's records is unchanged, doctor is
   clean, a second apply is a no-op, `--restore` brings the files back and the shards keep
   every original byte;
5. the OLD ddflow reads the upgraded copy, and its write is refused with the remedy or lands
   without disturbing a byte;
6. the connect flow (D-upgrade-on-mcp-connect): the new MCP server over stdio on the old
   project proposes the upgrade, applies NOTHING until consent, then applies on consent;
7. two agents claim, work and release a task with the new version;
8. shards two agents wrote with the old version merge (`merge=union`) and fold identically.

Every check is a row: pass, fail, or unavailable (it could not run). The exit is 0 only when
every row passes, 1 when any fails, 2 when none failed but one could not run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..core import clock
from ..core import digest as D
from ..core import outcome as O
from ..core import version as V
from ..core.events import Event, is_older
from ..core.model import State, fold
from ..infra import fsio
from ..infra import git as G
from ..infra import proc as P
from ..infra.log import EventLog

PASS = "pass"
FAIL = "fail"
UNAVAILABLE = "unavailable"

#: What `State` keeps that an upgrade is ALLOWED to change: its own bookkeeping, and the
#: definitions a migration moves into the log. Everything else is the user's records.
UPGRADE_OWNED = frozenset(
    {
        "event_count",
        "last_lamport",
        "ddflow_versions",
        "skew_overrides",
        "upgrades",
        "upgrade_runs",
        "repairs_applied",
        "older_version_events",
        "capabilities",
        "skipped_kinds",
        "fold_problems",
        "defs",
        "links",
        "definitions",
    }
)

#: The paths a restore does not bring back: the log only grows, the rest is machine-local.
KEPT = (".ddflow/events/", ".ddflow/local/", ".ddflow/backups/", ".ddflow/worktrees/", ".git/")

#: Commands read-only enough to run on the copy of a project (a derived index may be rebuilt).
READS: tuple[tuple[str, ...], ...] = (
    ("status", "--json"),
    ("board",),
    ("doctor",),
    ("brief",),
    ("history",),
    ("replay",),
)

#: Seconds one ddflow command, git call or server session may take.
TIMEOUT_S = 300.0

#: Run as `python -c`: the current code declaring an OLDER release, for tests and for a
#: machine with no old release to hand. It is a simulation, and the report says so.
_SIMULATED_OLD = (
    "import sys, ddflow\n"
    "ddflow.__version__ = sys.argv[1]\n"
    "from ddflow.surfaces.cli import main\n"
    "raise SystemExit(main(sys.argv[2:]))\n"
)


@dataclass
class Check:
    step: int
    name: str
    status: str
    detail: str = ""


@dataclass
class Cmd:
    """A ddflow to run: its argv prefix, the PYTHONPATH it needs ("" for an installed one)
    and how the report names it."""

    argv: list[str]
    pythonpath: str = ""
    label: str = ""


@dataclass
class Options:
    source: Path
    work: Path
    from_commit: str = "HEAD"
    new: Cmd | None = None
    old: Cmd | None = None
    max_show: int = 25
    keep: bool = False
    agents: tuple[str, str] = ("dry-a", "dry-b")
    #: The most bytes a compact `tools/list` may take; 0 reports the size and judges nothing.
    tools_budget: int = 0
    #: Build the old ddflow from the clone (a venv under the scratch directory) when none is given.
    build_old: bool = False
    #: Whether an item the operator would be asked to confirm fails the dry run (the default:
    #: it is the surprise the rehearsal exists to find) or is only named in the preview.
    asked_fails: bool = True
    reason: str = "dry run: the operator would accept this"


@dataclass
class Report:
    source: str
    from_commit: str
    new_label: str
    old_label: str
    started: str = ""
    checks: list[Check] = field(default_factory=list)
    preview: str = ""
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        if any(c.status == FAIL for c in self.checks):
            return 1
        if any(c.status == UNAVAILABLE for c in self.checks) or not self.checks:
            return 2
        return 0

    def table(self) -> str:
        width = max([len(c.name) for c in self.checks] + [4])
        rows = [f"{'step':>4}  {'check':<{width}}  result       detail"]
        for c in self.checks:
            rows.append(f"{c.step:>4}  {c.name:<{width}}  {c.status.upper():<11}  {c.detail}")
        verdict = {0: "PASS", 1: "FAIL", 2: "COULD NOT RUN"}[self.exit_code]
        rows.append(f"dry run {verdict}: {len(self.checks)} check(s), from {self.from_commit[:10]}")
        return "\n".join(rows)

    def markdown(self) -> str:
        lines = [
            "# Upgrade dry run",
            "",
            f"Generated {self.started}. Rehearsed on a clone of `{self.source}` at "
            f"`{self.from_commit[:12]}`; new ddflow: {self.new_label}; old ddflow: "
            f"{self.old_label or 'none'}. Nothing live was touched.",
            "",
            "| step | check | result | detail |",
            "|---|---|---|---|",
        ]
        for c in self.checks:
            lines.append(f"| {c.step} | {c.name} | {c.status} | {c.detail.replace('|', '/')} |")
        if self.preview:
            lines += ["", "## Upgrade preview", "", "```", self.preview.rstrip(), "```"]
        return "\n".join(lines) + "\n"

    def as_data(self) -> dict[str, Any]:
        return {
            "exit": self.exit_code,
            "source": self.source,
            "from": self.from_commit,
            "new": self.new_label,
            "old": self.old_label,
            "checks": [asdict(c) for c in self.checks],
            "facts": self.facts,
        }


class CouldNotRun(Exception):
    """The rehearsal cannot start (no git, a bad commit, a work directory inside the project)."""


# -- helpers that read, never write --------------------------------------------------------


def source_root() -> str:
    """The directory holding the `ddflow` package that is running: the NEW code's PYTHONPATH."""
    return str(Path(__file__).resolve().parents[2])


def new_cmd() -> Cmd:
    return Cmd([sys.executable, "-m", "ddflow"], source_root(), "this source tree")


def simulated_old(version: str) -> Cmd:
    """The current code declaring ``version``: an older reader/writer for tests (a simulation)."""
    return Cmd(
        [sys.executable, "-c", _SIMULATED_OLD, version],
        source_root(),
        f"simulated {version} (this code, declaring that version)",
    )


def release_commit(source: Path, version: str) -> str:
    """The commit that released ``version``: its tag, else the `release X` commit; "" if none."""
    tag = G.run(source, "rev-parse", "--verify", "--quiet", f"v{version}^{{commit}}")
    if tag.ok and tag.out:
        return tag.out
    found = G.run(
        source, "log", "--format=%H", "--fixed-strings", f"--grep=release {version}", "-1"
    )
    return found.out if found.ok else ""


def previous_release(source: Path, current: str) -> str:
    """The newest `v*` tag below ``current``, as a commit; "" when there is none."""
    tags = G.run(source, "tag", "--list", "v*", "--sort=-v:refname")
    for tag in tags.out.splitlines() if tags.ok else []:
        version = tag.removeprefix("v")
        if is_older(version, current):
            done = G.run(source, "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}")
            return done.out if done.ok else ""
    return ""


@dataclass
class Live:
    """The parts of the LIVE project a rehearsal could disturb, as they were when read."""

    shards: dict[str, bytes]
    tags: list[str]
    backups: list[str]


def snapshot_live(source: Path) -> Live:
    """What to compare after the run. Other agents may be working in the live project, so its
    log may GROW meanwhile; what must not happen is that it changes under us, or that anything
    of the rehearsal (an agent named `dry-*`, a snapshot tag, a backup) shows up in it."""
    tags = G.run(source, "tag", "--list", "ddflow-upgrade-snapshot/*")
    backups = Path(source) / ".ddflow" / "backups"
    return Live(
        shard_bytes(source),
        tags.out.splitlines() if tags.ok else [],
        sorted(p.name for p in backups.iterdir()) if backups.is_dir() else [],
    )


def live_problems(before: Live, after: Live) -> list[str]:
    """How the live project differs from before in a way a rehearsal must not cause."""
    out = [
        f"the live shard {n} changed under the run"
        for n in prefixes_kept(before.shards, after.shards)
    ]
    out += [
        f"the live log has a shard of the rehearsal: {n}"
        for n in after.shards
        if n.startswith("dry-")
    ]
    out += [
        f"a snapshot tag appeared in the live project: {t}"
        for t in sorted(set(after.tags) - set(before.tags))
    ]
    out += [
        f"a backup appeared in the live project: {b}"
        for b in sorted(set(after.backups) - set(before.backups))
    ]
    return out


def state_digests(repo: Path) -> dict[str, str]:
    """One digest per part of the state the log folds into (`State` field by field), so a
    difference names where it is."""
    st = fold(EventLog(repo, "dry-digest").read_all(), strict=False)
    return {name: D.of_obj(value, size=8) for name, value in _fields(st).items()}


def _fields(st: State) -> dict[str, Any]:
    return json.loads(json.dumps(asdict(st), default=str))


def users_part(digests: dict[str, str]) -> dict[str, str]:
    """The digests of what is the user's: not the upgrade's own bookkeeping."""
    return {k: v for k, v in digests.items() if k not in UPGRADE_OWNED}


def tree_digest(repo: Path, kept: Iterable[str] = KEPT) -> str:
    """A digest of every file the working tree holds (tracked or not, ignored ones excluded)
    except those under ``kept``: what a restore must bring back byte for byte."""
    names = G.run(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard", z=False)
    rows: list[str] = []
    for name in sorted(names.out.split("\0")) if names.ok else []:
        if not name or name.startswith(tuple(kept)):
            continue
        path = Path(repo) / name
        if path.is_file():
            rows.append(f"{name} {D.hasher(path.read_bytes()).hexdigest()}")
    return D.of_obj(rows)


def shard_bytes(repo: Path) -> dict[str, bytes]:
    root = Path(repo) / ".ddflow" / "events"
    return {p.name: p.read_bytes() for p in sorted(root.glob("*.jsonl"))} if root.is_dir() else {}


def prefixes_kept(before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    """The shards whose original bytes are NOT the start of what is there now."""
    return [n for n, raw in before.items() if not after.get(n, b"").startswith(raw)]


def events_of(repo: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in shard_bytes(repo).values():
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


# -- the rehearsal --------------------------------------------------------------------------


class Rehearsal:
    """One dry run: the scratch directories, the commands, the checks collected so far."""

    def __init__(self, opt: Options) -> None:
        self.opt = opt
        self.new = opt.new or new_cmd()
        self.report = Report(
            str(opt.source), opt.from_commit, self.new.label or "", opt.old.label if opt.old else ""
        )
        self.home = opt.work / "home"
        self.pristine = opt.work / "pristine"
        #: The keys the plan said the operator would be asked to confirm (set by `step_plan`).
        self.confirms: list[str] = []

    # recording

    def add(self, step: int, name: str, ok: bool | None, detail: str = "") -> bool:
        status = UNAVAILABLE if ok is None else (PASS if ok else FAIL)
        self.report.checks.append(Check(step, name, status, detail))
        return bool(ok)

    def guard(self, step: int, name: str, fn: Callable[[], Any]) -> Any:
        """Run one step; whatever it raises becomes a failed row, never a crash."""
        try:
            return fn()
        except CouldNotRun as exc:
            self.add(step, name, None, str(exc))
        except Exception as exc:  # the report must come out whole
            self.add(step, name, False, f"{type(exc).__name__}: {exc}")
        return None

    # running commands

    def env(self, cmd: Cmd) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "XDG_STATE_HOME": str(self.home / "state"),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "LC_ALL": "C.UTF-8",
            "NO_COLOR": "1",
            # `python -m ddflow` run in a clone of ddflow's OWN repository would otherwise
            # import the clone's package (the working directory comes first on sys.path), and
            # the "new" code would be the old one
            "PYTHONSAFEPATH": "1",
            "COLUMNS": "200",
            "DDFLOW_NO_UPDATE_CHECK": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "dry-run",
            "GIT_AUTHOR_EMAIL": "dry-run@example.invalid",
            "GIT_COMMITTER_NAME": "dry-run",
            "GIT_COMMITTER_EMAIL": "dry-run@example.invalid",
        }
        if cmd.pythonpath:
            env["PYTHONPATH"] = cmd.pythonpath
        return env

    def ddflow(
        self, cmd: Cmd, repo: Path, *args: str, agent: str = "", stdin: str | None = None
    ) -> P.Captured:
        flags = ["--repo", str(repo), *(["--agent", agent] if agent else [])]
        return P.capture(
            [*cmd.argv, *flags, *args],
            timeout=TIMEOUT_S,
            cwd=repo,
            env=self.env(cmd),
            input=stdin,
        )

    def git(self, repo: Path, *args: str) -> G.GitResult:
        return G.run(repo, *args, timeout=TIMEOUT_S, env=self.env(self.new))

    def clone(self, name: str, src: Path | None = None) -> Path:
        """A new clone of the pristine copy (or of ``src``) on its own branch."""
        dest = self.opt.work / name
        done = self.git(
            self.opt.work, "clone", "--quiet", "--local", str(src or self.pristine), str(dest)
        )
        if not done.ok:
            raise CouldNotRun(f"git clone failed: {done.text()}")
        for key, value in (("user.name", "dry-run"), ("user.email", "dry-run@example.invalid")):
            self.git(dest, "config", key, value)
        self.git(dest, "config", "commit.gpgsign", "false")
        return dest

    def apply_args(self, backup: str) -> list[str]:
        """`upgrade --apply` with the confirmations the plan asked for (the rehearsal gives them:
        what the operator would be asked is judged by the plan step, not by stopping here)."""
        asked = [x for k in self.confirms for x in ("--confirm", k)]
        return ["upgrade", "--apply", "--backup", backup, *asked, "--reason", self.opt.reason]

    def commit_all(self, repo: Path, message: str) -> None:
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "--quiet", "--no-verify", "--allow-empty", "-m", message)

    def shards_of(self, repo: Path) -> dict[str, bytes]:
        return shard_bytes(repo)

    # the steps

    def run(self) -> Report:
        self.report.started = clock.now_iso(timespec="seconds")
        self.report.facts["new"] = self.report.new_label
        before = self.guard(1, "live project snapshot", lambda: snapshot_live(self.opt.source))
        if self.guard(1, "clone at the installed version's commit", self.step_clone):
            if self.opt.old is None and self.opt.build_old:
                self.guard(1, "build the old ddflow from the clone", self.step_build_old)
            self.guard(2, "read with the new code", self.step_read)
            self.guard(3, "upgrade --plan", self.step_plan)
            self.guard(4, "apply with a snapshot, again, restore", self.step_apply)
            self.guard(5, "old ddflow on the upgraded copy", self.step_old_reader)
            self.guard(
                6,
                "MCP connect: propose, wait for consent, apply",
                self.step_connect,
            )
            self.guard(7, "two agents, one task", self.step_agents)
            self.guard(8, "shards from the old version merge", self.step_shards)
        after = self.guard(9, "live project untouched", lambda: snapshot_live(self.opt.source))
        if before is not None and after is not None:
            moved = live_problems(before, after)
            grew = len(after.shards) - len(before.shards)
            self.add(
                9,
                "live project untouched",
                not moved,
                "; ".join(moved) or "no shard, tag or backup of the rehearsal; its log only grew"
                if grew or after.shards != before.shards
                else "nothing of the live project changed",
            )
        return self.report

    def step_clone(self) -> bool:
        src, work = Path(self.opt.source).resolve(), Path(self.opt.work).resolve()
        if work == src or work.is_relative_to(src) or src.is_relative_to(work):
            raise CouldNotRun("the scratch directory must be outside the project")
        if not G.run(src, "rev-parse", "--git-dir").ok:
            raise CouldNotRun(f"{src} is not a git repository")
        commit = G.run(src, "rev-parse", "--verify", f"{self.opt.from_commit}^{{commit}}")
        if not commit.ok:
            raise CouldNotRun(f"no commit {self.opt.from_commit!r} in {src}")
        self.report.from_commit = commit.out
        if work.is_dir() and any(work.iterdir()):
            raise CouldNotRun(f"the scratch directory {work} is not empty: name a new one")
        work.mkdir(parents=True, exist_ok=True)
        self.home.mkdir(parents=True, exist_ok=True)
        done = self.git(
            work, "clone", "--quiet", "--local", "--no-hardlinks", str(src), str(self.pristine)
        )
        if not done.ok:
            raise CouldNotRun(f"git clone failed: {done.text()}")
        self.git(self.pristine, "checkout", "--quiet", "--detach", commit.out)
        self.git(self.pristine, "checkout", "--quiet", "-b", "dryrun")
        shards = shard_bytes(self.pristine)
        if not shards:
            return self.add(
                1,
                "clone at the installed version's commit",
                False,
                "the clone has no event log (is .ddflow/events committed?)",
            )
        st = fold(EventLog(self.pristine, "dry-digest").read_all(), strict=False)
        self.report.facts.update(
            events=st.event_count,
            stamps=sorted(st.ddflow_versions),
            highest=st.highest_version,
            shards=sorted(shards),
        )
        return self.add(
            1,
            "clone at the installed version's commit",
            True,
            f"{commit.out[:10]}: {st.event_count} events in {len(shards)} shard(s); stamps "
            f"{', '.join(sorted(st.ddflow_versions)) or 'none (predates stamps)'}",
        )

    def step_build_old(self) -> bool | None:
        """Install the clone, as it stands at the installed version's commit, in a venv of its
        own: the ddflow the operator runs now. Needs `uv` and the package index or its cache."""
        venv = self.opt.work / "old-venv"
        env = {
            **self.env(Cmd([])),
            "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(Path.home() / ".cache" / "uv")),
        }
        made = P.capture(["uv", "venv", "--quiet", str(venv)], timeout=TIMEOUT_S, env=env)
        if not made.ran or made.returncode != 0:
            return self.add(
                1,
                "build the old ddflow from the clone",
                None,
                f"uv venv failed: {(made.stderr or str(made.error)).strip()[-160:]}",
            )
        python = venv / "bin" / "python"
        built = P.capture(
            ["uv", "pip", "install", "--quiet", "--python", str(python), str(self.pristine)],
            timeout=TIMEOUT_S * 3,
            env=env,
        )
        if not built.ran or built.returncode != 0:
            return self.add(
                1,
                "build the old ddflow from the clone",
                None,
                f"install failed: {(built.stderr or str(built.error)).strip()[-160:]}",
            )
        self.opt.old = Cmd(
            [str(python), "-m", "ddflow"],
            "",
            f"built from the clone at {self.report.from_commit[:10]}",
        )
        self.report.old_label = self.opt.old.label
        return self.add(1, "build the old ddflow from the clone", True, self.opt.old.label)

    def step_read(self) -> bool:
        repo = self.clone("read")
        before = state_digests(repo)
        bad: list[str] = []
        for args in READS:
            done = self.ddflow(self.new, repo, *args, agent="dry-read")
            if not done.ran or done.returncode not in (0, 1, 2) or "Traceback" in done.stderr:
                bad.append(f"{' '.join(args)} -> {done.returncode} {done.stderr.strip()[-120:]}")
        st = fold(EventLog(repo, "dry-digest").read_all(), strict=False)
        for item in sorted(st.items)[: self.opt.max_show]:
            done = self.ddflow(self.new, repo, "show", item, agent="dry-read")
            if not done.ran or done.returncode not in (0, 1, 2) or "Traceback" in done.stderr:
                bad.append(f"show {item} -> {done.returncode}")
        after = state_digests(repo)
        moved = [k for k in users_part(before) if before[k] != after.get(k)]
        if moved:
            bad.append(f"reading changed {', '.join(moved)}")
        if st.skipped_kinds:
            bad.append(f"events the new code skipped: {st.skipped_kinds}")
        self.report.facts["digest"] = D.of_obj(users_part(before))
        return self.add(
            2,
            "read with the new code",
            not bad,
            "; ".join(bad)
            or f"{len(READS)} read commands and {min(len(st.items), self.opt.max_show)} item(s), state unchanged",
        )

    def step_plan(self) -> dict[str, Any]:
        repo = self.clone("plan")
        done = self.ddflow(self.new, repo, "upgrade", "--plan", "--json", agent="dry-plan")
        if not done.ran or done.returncode not in (0, 1):
            raise CouldNotRun(
                f"upgrade --plan exited {done.returncode}: {done.stderr.strip()[-200:]}"
            )
        plan = json.loads(done.stdout)
        text = self.ddflow(self.new, repo, "upgrade", "--plan", agent="dry-plan").stdout
        self.report.preview = text
        items = [i for v in plan["categories"].values() for i in v]
        asked = [i for i in items if i["action"] == "needs operator confirmation"]
        odd = [
            i for i in items if i["action"] not in ("agent", "note", "needs operator confirmation")
        ]
        detail = f"{plan['total']} item(s)"
        if asked:
            detail += "; the operator would be asked to confirm: " + ", ".join(
                _key(i) for i in asked
            )
        if odd:
            detail += "; unknown action on: " + ", ".join(i["id"] for i in odd)
        self.confirms = [_key(i) for i in asked]
        self.add(3, "upgrade --plan", not ((asked and self.opt.asked_fails) or odd), detail)
        return plan

    def step_apply(self) -> bool:
        repo = self.clone("apply")
        before_users = users_part(state_digests(repo))
        before_tree, before_shards = tree_digest(repo), shard_bytes(repo)
        done = self.ddflow(self.new, repo, *self.apply_args("snapshot"), agent="dry-apply")
        problems: list[str] = []
        if done.returncode != 0:
            problems.append(
                f"apply exited {done.returncode}: {(done.stderr or done.stdout).strip()[-200:]}"
            )
        problems += self._after_apply(repo, before_users)
        self.commit_all(repo, "applied")
        again = self.ddflow(
            self.new, repo, "upgrade", "--apply", "--backup", "none", "--json", agent="dry-apply"
        )
        applied = _applied(again)
        if again.returncode != 0 or not applied.get("noop"):
            problems.append(f"a second apply was not a no-op (exit {again.returncode})")
        problems += self._restore(repo, before_tree, before_shards)
        self.report.facts["apply"] = "ok" if not problems else "problems"
        return self.add(
            4,
            "apply with a snapshot, again, restore",
            not problems,
            "; ".join(problems)
            or "users' records unchanged, doctor clean, second apply a no-op, restore exact",
        )

    def _after_apply(self, repo: Path, before_users: dict[str, str]) -> list[str]:
        out: list[str] = []
        after = users_part(state_digests(repo))
        moved = [k for k in before_users if before_users[k] != after.get(k)]
        if moved:
            out.append(f"the apply changed {', '.join(moved)}")
        doctor = self.ddflow(self.new, repo, "doctor", agent="dry-apply")
        if doctor.returncode != 0:
            out.append(
                f"doctor after the apply exited {doctor.returncode}: {doctor.stdout.strip()[-160:]}"
            )
        plan = self.ddflow(self.new, repo, "upgrade", "--plan", agent="dry-apply")
        if plan.returncode != 0:
            out.append("the plan after the apply is not empty")
        return out

    def _restore(self, repo: Path, tree: str, shards: dict[str, bytes]) -> list[str]:
        out: list[str] = []
        restored = self.ddflow(self.new, repo, "upgrade", "--restore", agent="dry-apply")
        if restored.returncode != 0:
            return [
                f"restore exited {restored.returncode}: {(restored.stderr or restored.stdout).strip()[-160:]}"
            ]
        if tree_digest(repo) != tree:
            dirty = self.git(
                repo,
                "--no-optional-locks",
                "status",
                "--porcelain",
                "--",
                ".",
                ":!.ddflow/events",
                ":!.ddflow/local",
                ":!.ddflow/backups",
            ).out
            out.append(
                "the files after restore are not the files before: "
                + (dirty.replace("\n", ", ")[:160] or "untracked content differs")
            )
        if lost := prefixes_kept(shards, shard_bytes(repo)):
            out.append(f"restore lost bytes of the shard(s) {', '.join(lost)}")
        return out

    def step_old_reader(self) -> bool | None:
        if self.opt.old is None:
            return self.add(
                5,
                "old ddflow on the upgraded copy",
                None,
                "no old ddflow given (--old-cmd, --build-old or --simulate-old)",
            )
        old = self.opt.old
        repo = self.clone("old")
        self.ddflow(self.new, repo, *self.apply_args("none"), agent="dry-apply")
        self.commit_all(repo, "applied")
        shards = shard_bytes(repo)
        bad: list[str] = []
        for args in (("status", "--json"), ("doctor",), ("brief",)):
            done = self.ddflow(old, repo, *args, agent="dry-old")
            if not done.ran or "Traceback" in done.stderr:
                bad.append(f"old {' '.join(args)} crashed: {done.stderr.strip()[-120:]}")
        write = self.ddflow(
            old,
            repo,
            "lesson",
            "add",
            "--title",
            "written by the old ddflow",
            "--new",
            agent="dry-old",
        )
        refused = write.returncode == O.REFUSED and "pgrade" in (write.stderr + write.stdout)
        landed = write.returncode == 0
        if not (refused or landed):
            bad.append(
                f"the old writer exited {write.returncode} without the remedy: {(write.stderr or write.stdout).strip()[-160:]}"
            )
        if lost := prefixes_kept(shards, shard_bytes(repo)):
            bad.append(f"the old ddflow disturbed the shard(s) {', '.join(lost)}")
        after = fold(EventLog(repo, "dry-digest").read_all(), strict=False)
        if after.fold_problems or after.skipped_kinds:
            bad.append("the new code cannot fold what the old one left")
        how = "refused its write with the remedy" if refused else "wrote without disturbing a byte"
        return self.add(
            5, "old ddflow on the upgraded copy", not bad, "; ".join(bad) or f"read fine, {how}"
        )

    def step_connect(self) -> bool:
        repo = self.clone("connect")
        before_tree = tree_digest(repo)
        msgs = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "dry-run", "version": "0"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "ddflow_brief", "arguments": {}},
            },
        ]
        first = self._serve(repo, msgs)
        problems: list[str] = []
        text = (first.get(1) or {}).get("instructions", "")
        if "newer than this project" not in text or "Ask the operator" not in text:
            problems.append("the handshake did not propose the upgrade and ask the operator")
        tools = (first.get(2) or {}).get("tools") or []
        size, bad = tools_problems(tools, self.opt.tools_budget)
        problems += bad
        if (
            any(e.get("kind") in ("upgrade.applied", "repair.applied") for e in events_of(repo))
            or tree_digest(repo) != before_tree
        ):
            problems.append("something was applied before the operator consented")
        keys = self.confirms
        consent = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "ddflow_upgrade",
                "arguments": {
                    "apply": "all",
                    "backup": "none",
                    "confirm": keys,
                    "reason": self.opt.reason,
                },
            },
        }
        done = self._serve(repo, [msgs[0], msgs[1], consent])
        body = _tool_body(done.get(4))
        if (body.get("applied") or {}).get("failed", 1):
            problems.append(
                f"the confirmed apply over MCP did not finish: {json.dumps(body)[:160]}"
            )
        doctor = self.ddflow(self.new, repo, "doctor", agent="dry-connect")
        if doctor.returncode != 0:
            problems.append(f"doctor after the consent exited {doctor.returncode}")
        status = _tool_body(
            self._serve(
                repo,
                [
                    msgs[0],
                    msgs[1],
                    {
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "tools/call",
                        "params": {"name": "ddflow_upgrade", "arguments": {"status": True}},
                    },
                ],
            ).get(5)
        )
        stale = [b["agent"] for b in status.get("behind", [])]
        self.report.facts["restart"] = stale
        told = (
            f"{len(stale)} older agent(s) told to restart"
            if stale
            else "no older-version agent in the log to tell"
        )
        return self.add(
            6,
            "MCP connect: propose, wait for consent, apply",
            not problems,
            "; ".join(problems)
            or f"proposed on connect, nothing applied until consent, then applied; tools/list {size} bytes; {told}",
        )

    def _serve(self, repo: Path, msgs: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        """Talk to the new MCP server over stdio for these messages; replies by request id."""
        argv = [sys.executable, "-m", "ddflow.surfaces.mcp", "--repo", str(repo)]
        done = P.capture(
            argv,
            timeout=TIMEOUT_S,
            cwd=repo,
            env=self.env(self.new),
            input="\n".join(json.dumps(m) for m in msgs) + "\n",
        )
        replies: dict[int, dict[str, Any]] = {}
        for line in done.stdout.splitlines():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and isinstance(msg.get("id"), int):
                replies[msg["id"]] = msg.get("result") or {"error": msg.get("error")}
        return replies

    def step_agents(self) -> bool:
        a, b = self.opt.agents
        repo = self.clone("agents")
        up = self.ddflow(self.new, repo, *self.apply_args("none"), agent=a)
        self.commit_all(repo, "applied")
        problems: list[str] = (
            [] if up.returncode in (O.OK, O.REFUSED) else [f"apply exited {up.returncode}"]
        )
        phase = _first_phase(repo)
        add = self.ddflow(
            self.new,
            repo,
            "task",
            "add",
            "DRY-1",
            "--title",
            "dry run task",
            "--phase",
            phase,
            "--globs",
            "dry/a.txt",
            agent=a,
        )
        claimed = self.ddflow(self.new, repo, "claim", "DRY-1", agent=a)
        refused = self.ddflow(self.new, repo, "claim", "DRY-1", agent=b)
        if add.returncode != 0 or claimed.returncode != 0:
            problems.append(
                f"{a} could not add and claim a task ({add.returncode}, {claimed.returncode})"
            )
        if refused.returncode != O.REFUSED:
            problems.append(f"{b} was not refused a task {a} holds (exit {refused.returncode})")
        wt = Path(repo) / ".ddflow" / "worktrees" / "DRY-1"
        if wt.is_dir():
            (wt / "dry").mkdir(exist_ok=True)
            fsio.replace_text(wt / "dry" / "a.txt", "dry run\n")
            self.commit_all(wt, "dry run edit")
        shown = self.ddflow(self.new, repo, "gate", "status", "DRY-1", "--json", agent=a)
        for gate in _pipeline(shown):
            self.ddflow(
                self.new,
                repo,
                "gate",
                "skip",
                "DRY-1",
                gate,
                "--reason",
                "dry run: a rehearsal, not evidence",
                agent=a,
            )
        merged = self.ddflow(self.new, repo, "merge", "DRY-1", agent=a)
        done = self.ddflow(self.new, repo, "complete", "DRY-1", "--model", "dry-run", agent=a)
        if merged.returncode != 0:
            problems.append(
                f"merge exited {merged.returncode}: {(merged.stderr or merged.stdout).strip()[-120:]}"
            )
        if done.returncode not in (O.OK, O.REFUSED):
            problems.append(f"complete exited {done.returncode}")
        # a refusal (no independent reviewer ran) is the design: release, never force
        left = (
            self.ddflow(self.new, repo, "release", "DRY-1", agent=a)
            if done.returncode == O.REFUSED
            else done
        )
        took = self.ddflow(self.new, repo, "claim", "DRY-1", agent=b)
        if done.returncode == O.REFUSED and (left.returncode != 0 or took.returncode != 0):
            problems.append(
                f"after the honest release {b} could not take the task ({left.returncode}, {took.returncode})"
            )
        doctor = self.ddflow(self.new, repo, "doctor", agent=a)
        if doctor.returncode != 0:
            problems.append(f"doctor exited {doctor.returncode}")
        how = (
            "completed"
            if done.returncode == 0
            else "complete refused (no independent reviewer), released honestly"
        )
        return self.add(
            7,
            "two agents, one task",
            not problems,
            "; ".join(problems) or f"claim, refusal of the second agent, edit, gates, merge; {how}",
        )

    def step_shards(self) -> bool | None:
        if self.opt.old is None:
            return self.add(8, "shards from the old version merge", None, "no old ddflow given")
        old = self.opt.old
        one, two = self.clone("shard-one"), self.clone("shard-two")
        for repo, title in ((one, "from clone one"), (two, "from clone two")):
            self.ddflow(old, repo, "lesson", "add", "--title", title, "--new", agent="dry-shared")
            self.commit_all(repo, title)
        attrs = Path(one) / ".gitattributes"
        if not attrs.is_file() or "merge=union" not in attrs.read_text("utf-8"):
            return self.add(
                8,
                "shards from the old version merge",
                False,
                "the project has no merge=union for its shards",
            )
        written = _lines(shard_bytes(one)) + _lines(shard_bytes(two))
        self.git(one, "remote", "add", "two", str(two))
        self.git(one, "fetch", "--quiet", "two")
        merged = self.git(one, "merge", "--no-edit", "--quiet", "two/dryrun")
        if not merged.ok:
            return self.add(
                8,
                "shards from the old version merge",
                False,
                f"merge failed: {merged.text()[-160:]}",
            )
        want = _digest_of(written)
        got = D.of_obj(
            users_part(
                {
                    k: D.of_obj(v)
                    for k, v in _fields(
                        fold(EventLog(one, "dry-digest").read_all(), strict=False)
                    ).items()
                }
            )
        )
        ok = want == got
        return self.add(
            8,
            "shards from the old version merge",
            ok,
            "the merged shards fold to what both clones wrote"
            if ok
            else "the merged log folds differently from the union of what both clones wrote",
        )


# -- small readers --------------------------------------------------------------------------


def tools_problems(tools: list[dict[str, Any]], budget: int) -> tuple[int, list[str]]:
    """`(compact bytes of tools/list, what is wrong with it)`: empty, over ``budget`` (when one
    is set), or carrying output schemas (`[mcp].output_schemas` ships off)."""
    size = len(json.dumps({"tools": tools}, separators=(",", ":")))
    out = []
    if not tools:
        out.append("tools/list answered nothing")
    if budget and size > budget:
        out.append(f"tools/list is {size} bytes, over the {budget} budget")
    if any("outputSchema" in t for t in tools):
        out.append("tools/list carries output schemas ([mcp].output_schemas should be off)")
    return size, out


def _key(item: dict[str, Any]) -> str:
    return str(
        item.get("key")
        or item.get("repair")
        or item.get("migration")
        or item.get("path")
        or item["id"]
    )


def _applied(done: P.Captured) -> dict[str, Any]:
    try:
        return dict(json.loads(done.stdout).get("applied") or {})
    except (ValueError, AttributeError):
        return {}


def _tool_body(result: dict[str, Any] | None) -> dict[str, Any]:
    try:
        return dict(json.loads(((result or {}).get("content") or [{}])[0].get("text") or "{}"))
    except (ValueError, TypeError):
        return {}


def _pipeline(shown: P.Captured) -> list[str]:
    """The gates of an item's pipeline that are skipped by hand (`merge` is done by ddflow)."""
    try:
        return [g for g in json.loads(shown.stdout)["pipeline"] if g != "merge"]
    except (ValueError, KeyError, TypeError):
        return []


def _first_phase(repo: Path) -> str:
    st = fold(EventLog(repo, "dry-digest").read_all(), strict=False)
    for item in st.items.values():
        if item.kind == "phase":
            return item.id
    return "P1"


def _event_lines(lines: list[str]) -> list[Event]:
    """The events these lines hold; one that does not parse is skipped, as the log skips it."""
    out = []
    for ln in lines:
        try:
            out.append(Event.from_json(ln))
        except (ValueError, KeyError, TypeError):
            continue
    return out


def _lines(shards: dict[str, bytes]) -> list[str]:
    return [
        ln
        for raw in shards.values()
        for ln in raw.decode("utf-8", "replace").splitlines()
        if ln.strip()
    ]


def _digest_of(lines: list[str]) -> str:
    """The digest of what raw event lines fold into, read the way the log reads them: sorted,
    each event once."""
    by_id = {e.id: e for e in _event_lines(lines)}
    st = fold(sorted(by_id.values(), key=Event.sort_key), strict=False)
    return D.of_obj(users_part({k: D.of_obj(v) for k, v in _fields(st).items()}))


def run_all(opt: Options) -> Report:
    """Run the whole rehearsal and clean up (unless ``opt.keep``). A scratch directory that
    already held something is never cleaned: the rehearsal refuses it, and it is not ours."""
    work = Path(opt.work)
    ours = not work.exists() or (work.is_dir() and not any(work.iterdir()))
    try:
        return Rehearsal(opt).run()
    finally:
        if ours and not opt.keep:
            shutil.rmtree(work, ignore_errors=True)


# -- the command line (scripts/ci/upgrade-dry-run is this, started from a checkout) -----------

COULD_NOT_RUN = 2


def tools_budget(tree: Path) -> int:
    """The compact tools/list budget the suite ratchets, read from the tree under test; 0 when
    that tree carries no test of it (an installed package)."""
    path = tree / "tests" / "test_mcp_tool_budget.py"
    text = path.read_text("utf-8") if path.is_file() else ""
    found = re.search(r"^TOOLS_LIST_BUDGET\s*=\s*([\d_]+)", text, re.M)
    return int(found.group(1).replace("_", "")) if found else 0


def parser(doc: str = __doc__ or "") -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="upgrade-dry-run",
        description=doc,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--source", default="", help="the project to rehearse (default: this one)")
    which = ap.add_argument_group("the version the installed ddflow was built from (one of)")
    which.add_argument("--from-commit", default="", help="that commit")
    which.add_argument(
        "--installed-version", default="", help="that release: its tag, else its `release X` commit"
    )
    which.add_argument(
        "--previous-release",
        action="store_true",
        help="the newest release below this tree's version",
    )
    old = ap.add_argument_group("the old ddflow (steps 5 and 8)")
    old.add_argument("--old-cmd", default="", help="a ddflow to run as the old one, e.g. a venv's")
    old.add_argument(
        "--build-old", action="store_true", help="install the clone in a venv (uv) as the old one"
    )
    old.add_argument(
        "--simulate-old", default="", metavar="VERSION", help="this code declaring VERSION"
    )
    ap.add_argument(
        "--confirmations-ok",
        action="store_true",
        help="name the items the operator would be asked to confirm in the preview without "
        "failing (the release check: that is this project's own config, not the release's)",
    )
    ap.add_argument("--work", default="", help="scratch directory (default: a new temporary one)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch directory")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--report", default="", help="also write the report as Markdown to this file")
    ap.add_argument("--preview", default="", help="also write the upgrade preview to this file")
    return ap


def choose_commit(args: argparse.Namespace, source: Path) -> str:
    """The commit the installed ddflow was built from, from exactly one way of naming it."""
    named = [bool(args.from_commit), bool(args.installed_version), args.previous_release]
    if sum(named) != 1:
        raise CouldNotRun(
            "name the commit the installed ddflow was built from: --from-commit, "
            "--installed-version or --previous-release (exactly one)"
        )
    if args.from_commit:
        return str(args.from_commit)
    if args.installed_version:
        commit = release_commit(source, args.installed_version)
        if not commit:
            raise CouldNotRun(f"no tag or release commit for ddflow {args.installed_version}")
        return commit
    running = V.running()
    commit = previous_release(source, running)
    if not commit:
        raise CouldNotRun(f"no release tag below {running}")
    return commit


def old_from(args: argparse.Namespace) -> Cmd | None:
    if args.old_cmd:
        return Cmd(shlex.split(args.old_cmd), "", f"installed: {args.old_cmd}")
    return simulated_old(args.simulate_old) if args.simulate_old else None


def main(argv: list[str] | None = None, *, source: Path | None = None, doc: str = "") -> int:
    """The command line: exit 0 every check passed, 1 one failed, 2 it could not run."""
    args = parser(doc or __doc__ or "").parse_args(argv)
    project = Path(args.source or source or Path.cwd()).resolve()
    try:
        commit = choose_commit(args, project)
    except CouldNotRun as exc:
        print(f"upgrade-dry-run: {exc}", file=sys.stderr)
        return COULD_NOT_RUN
    opt = Options(
        source=project,
        work=Path(args.work) if args.work else fsio.unused_dir("ddflow-dry-run-"),
        from_commit=commit,
        old=old_from(args),
        keep=args.keep,
        tools_budget=tools_budget(Path(source_root())),
        build_old=args.build_old,
        asked_fails=not args.confirmations_ok,
    )
    report = run_all(opt)
    print(json.dumps(report.as_data(), indent=2) if args.json else report.table())
    if args.report:
        fsio.replace_text(args.report, report.markdown())
    if args.preview and report.preview:
        fsio.replace_text(args.preview, report.preview)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
