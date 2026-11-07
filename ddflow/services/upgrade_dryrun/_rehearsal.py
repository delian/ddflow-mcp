"""The rehearsal: one dry run's scratch state, its command runner, and its top-level run."""

from __future__ import annotations

import os
import secrets
import shlex
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ...core import clock
from ...infra import git as G
from ...infra import proc as P
from ..backups import SNAPSHOT_PREFIX
from ._model import (
    FAIL,
    LIVE_CHANGED,
    MAX_COMMANDS,
    PASS,
    STREAM_BOUND,
    TIMEOUT_S,
    UNAVAILABLE,
    Check,
    Cmd,
    CouldNotRun,
    Options,
    Report,
    bounded,
)
from ._read import (
    Footprint,
    Live,
    backup_versions,
    live_leaks,
    live_moved,
    new_cmd,
    shard_bytes,
    snapshot_live,
)
from ._steps import _RehearsalSteps
from ._support import _asked, _key, doctor_problems, new_problems


class Rehearsal(_RehearsalSteps):
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
        #: Signs this rehearsal's git commits and tags, so its traces in the live project can
        #: be told from other agents' (a snapshot tag carries it as its tagger).
        self.run_id = secrets.token_hex(4)
        self.started_stamp = clock.compact_at()
        #: Every command the rehearsal ran, with its bounded output: the transcript a failure
        #: is explained from.
        self.commands: list[dict[str, Any]] = []
        #: `"decision D-x"`-style names of the records the plan said are CONTESTED (set by
        #: `step_plan`): the operator's to resolve, so doctor failing on them alone is expected.
        self.contested: set[str] = set()
        #: The problem identities doctor shows on the untouched clone (see `doctor_baseline`).
        self._baseline: set[str] | None = None
        self._baseline_read = False

    # recording

    def add(self, step: int, name: str, ok: bool | None, detail: str = "") -> bool:
        status = UNAVAILABLE if ok is None else (PASS if ok else FAIL)
        self.report.checks.append(Check(step, name, status, detail))
        return bool(ok)

    def unrunnable(self, name: str) -> None:
        """Note that a step could not run (as opposed to an old ddflow that was not offered):
        a run with one leaves its transcript."""
        self.report.facts.setdefault("could_not_run", []).append(name)

    def guard(self, step: int, name: str, fn: Callable[[], Any]) -> Any:
        """Run one step; whatever it raises becomes a failed row, never a crash."""
        try:
            return fn()
        except CouldNotRun as exc:
            self.unrunnable(name)
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
            "GIT_COMMITTER_NAME": f"dry-run-{self.run_id}",
            "GIT_COMMITTER_EMAIL": "dry-run@example.invalid",
        }
        if cmd.pythonpath:
            env["PYTHONPATH"] = cmd.pythonpath
        return env

    def ddflow(
        self, cmd: Cmd, repo: Path, *args: str, agent: str = "", stdin: str | None = None
    ) -> P.Captured:
        flags = ["--repo", str(repo), *([f"--agent={agent}"] if agent else [])]
        return self.capture(
            [*cmd.argv, *flags, *args],
            timeout=TIMEOUT_S,
            cwd=repo,
            env=self.env(cmd),
            input=stdin,
        )

    def capture(self, argv: list[str], **kw: Any) -> P.Captured:
        """`proc.capture`, with the command and its (bounded) output kept in the transcript."""
        began = time.monotonic()
        done = P.capture(argv, **kw)
        self.note(
            shlex.join(str(a) for a in argv),
            kw.get("cwd"),
            done.returncode,
            done.stdout,
            done.stderr,
            time.monotonic() - began,
            str(done.error) if done.error else "",
        )
        return done

    def note(
        self,
        command: str,
        cwd: Any,
        code: int | None,
        out: str,
        err: str,
        seconds: float,
        error: str = "",
    ) -> None:
        # a command that worked keeps little; one that did not keeps what explains it
        limit = STREAM_BOUND if code != 0 else STREAM_BOUND // 10  # 600
        self.commands.append(
            {
                "run": self.run_id,
                "argv": command,
                "cwd": str(cwd or ""),
                "returncode": code,
                "error": error,
                "seconds": round(seconds, 2),
                "stdout": bounded(out, limit),
                "stderr": bounded(err, limit),
            }
        )
        del self.commands[:-MAX_COMMANDS]

    def git(self, repo: Path, *args: str) -> G.GitResult:
        began = time.monotonic()
        done = G.run(repo, *args, timeout=TIMEOUT_S, env=self.env(self.new))
        shown = f"git -C {shlex.quote(str(repo))} {shlex.join(args)}"
        self.note(shown, repo, done.code, done.out, done.err, time.monotonic() - began)
        return done

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

    def apply_args(self, backup: str, keys: Iterable[str] = ()) -> list[str]:
        """`upgrade --apply` with the confirmations the plan asked for (the rehearsal gives them:
        what the operator would be asked is judged by the plan step, not by stopping here)."""
        asked = [x for k in sorted({*self.confirms, *keys}) for x in ("--confirm", k)]
        return ["upgrade", "--apply", "--backup", backup, *asked, "--reason", self.opt.reason]

    def apply_in(self, repo: Path, backup: str, agent: str) -> P.Captured:
        """`upgrade --apply` in ``repo`` with the confirmations ITS plan asks for, plus the
        earlier plan's (the clone may differ: a hand edit, another history)."""
        planned = self.ddflow(self.new, repo, "upgrade", "--plan", "--json", agent=agent)
        keys = [_key(i) for i in _asked(planned)]
        return self.ddflow(self.new, repo, *self.apply_args(backup, keys), agent=agent)

    def doctor_baseline(self) -> set[str] | None:
        """The problems the previous release's own project already has, as the new doctor reads
        them before anything is applied (taken once, on a fresh clone); None when it cannot be
        read. The upgrade is judged by what it ADDS to them, not by the old queue's hygiene."""
        if not self._baseline_read:
            repo = self.clone("doctor-baseline")
            found = doctor_problems(
                self.ddflow(self.new, repo, "--json", "doctor", agent="dry-baseline")
            )
            self._baseline = None if found is None else set(found)
            self._baseline_read = True
        return self._baseline

    def doctor_verdict(self, repo: Path, agent: str) -> str:
        """Return "" when doctor in ``repo`` shows nothing the project did not already have before the
        upgrade (the records the plan announced as contested aside); else what went wrong.
        A doctor that crashed or printed no JSON is a failure; the pre-existing count is a note."""
        base = self.doctor_baseline()
        done = self.ddflow(self.new, repo, "--json", "doctor", agent=agent)
        found = doctor_problems(done)
        if found is None:
            return f"doctor exited {done.returncode} and printed no readable report"
        if base is None:
            return "doctor could not be read on the untouched clone, so there is no baseline"
        added = new_problems(base, found, self.contested)
        if added:
            return f"doctor reports {len(added)} NEW problem(s): " + "; ".join(
                a[:160] for a in added[:3]
            )
        self.report.facts["doctor_preexisting"] = len(base)
        return ""

    def commit_all(self, repo: Path, message: str) -> None:
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "--quiet", "--no-verify", "--allow-empty", "-m", message)

    def shards_of(self, repo: Path) -> dict[str, bytes]:
        return shard_bytes(repo)

    # the top-level run

    def run(self) -> Report:
        self.report.started = clock.now_iso(timespec="seconds")
        self.report.facts["new"] = self.report.new_label
        before = self.guard(
            1, "read the live project first", lambda: snapshot_live(self.opt.source)
        )
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
            self.guard(9, "an event upcaster over the old log", self.step_upcaster)
        after = self.guard(
            10, "read the live project afterwards", lambda: snapshot_live(self.opt.source)
        )
        if before is not None and after is not None:
            self.judge_live(before, after)
        return self.report

    def footprint(self) -> Footprint:
        """What the rehearsal's own backups and snapshot tags (in the clones) are called."""
        versions: set[str] = set()
        for clone in sorted(self.opt.work.iterdir()) if self.opt.work.is_dir() else []:
            kept = clone / ".ddflow" / "backups"
            if kept.is_dir():
                versions |= {backup_versions(b.name) for b in kept.iterdir()}
            if (clone / ".git").exists():
                tags = G.run(clone, "tag", "--list", f"{SNAPSHOT_PREFIX}*")
                versions |= {
                    backup_versions(t.removeprefix(SNAPSHOT_PREFIX)) for t in tags.out.splitlines()
                }
        versions.discard("")
        return Footprint(self.run_id, self.started_stamp, clock.compact_at(), frozenset(versions))

    def judge_live(self, before: Live, after: Live) -> None:
        """Step 10. What of the rehearsal is in the live project fails; what only other agents
        did (their snapshot tags and backups, a shard rewritten or still being written) is
        `LIVE_CHANGED`: not a pass, and not the rehearsal's fault either, so `unavailable`."""
        name = "live project untouched"
        mine = self.footprint()
        leaks = live_leaks(before, after, mine)
        moved = live_moved(before, after, mine)
        if leaks:
            self.add(10, name, False, "; ".join([*leaks, *moved]))
            return
        if moved:
            self.report.facts["live_changed"] = moved
            self.add(
                10,
                LIVE_CHANGED,
                None,
                "; ".join(moved) + ": nothing of the rehearsal is in it, but the project moved "
                "under the run, so it cannot be said to be untouched",
            )
            return
        others = [f"snapshot tag {t}" for t in sorted(set(after.tags) - set(before.tags))] + [
            f"backup {b}" for b in sorted(set(after.backups) - set(before.backups))
        ]
        note = "; ".join(
            [
                "nothing of the live project changed"
                if after.shards == before.shards
                else "no shard of the rehearsal; its log only grew",
                *([f"made by others meanwhile and ignored: {', '.join(others)}"] if others else []),
            ]
        )
        self.add(10, name, True, note)
