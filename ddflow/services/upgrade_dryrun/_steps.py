"""The rehearsal's numbered steps, as the base class the concrete `Rehearsal` extends."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from ...core import digest as D
from ...core import outcome as O
from ...core import version as V
from ...core.events import is_older
from ...infra import fsio
from ...infra import git as G
from .. import upgrade_start as US
from ._model import (
    APPLYING,
    CLONE_RETRY_S,
    FORCED_TIMEOUT_S,
    HAND_EDIT,
    READS,
    START_TIMED_OUT,
    START_TIMEOUT_S,
    TIMEOUT_S,
    Cmd,
    CouldNotRun,
    bounded,
)
from ._read import (
    events_of,
    folded_state,
    prefixes_kept,
    shard_bytes,
    state_digests,
    tree_digest,
    upcast_problems,
    users_part,
)
from ._support import (
    _HELLO,
    _TOOLS_LIST,
    _call,
    _edit_kept,
    _first_phase,
    _items,
    _key,
    _pipeline,
    _tool_body,
    state_part_digest,
    tools_problems,
)


class _RehearsalSteps:
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
        # only what is COMMITTED is read, at the commit just resolved (not a moving HEAD); a
        # gc or ref update in the live repo can fail one clone, so it is tried once more
        args = ("clone", "--quiet", "--local", "--no-hardlinks", "--no-checkout", str(src))
        done = self.git(work, *args, str(self.pristine))
        if not done.ok:
            fsio.rmtree_owned(self.pristine)
            time.sleep(CLONE_RETRY_S)
            done = self.git(work, *args, str(self.pristine))
        if not done.ok:
            raise CouldNotRun(f"git clone failed twice: {done.text()}")
        checked = self.git(self.pristine, "checkout", "--quiet", "--detach", commit.out)
        if not checked.ok:
            raise CouldNotRun(f"git could not check out {commit.out[:10]}: {checked.text()}")
        self.git(self.pristine, "checkout", "--quiet", "-b", "dryrun")
        shards = shard_bytes(self.pristine)
        if not shards:
            return self.add(
                1,
                "clone at the installed version's commit",
                False,
                "the clone has no event log (is .ddflow/events committed?)",
            )
        st = folded_state(self.pristine)
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
        made = self.capture(["uv", "venv", "--quiet", str(venv)], timeout=TIMEOUT_S, env=env)
        if not made.ran or made.returncode != 0:
            self.unrunnable("build the old ddflow from the clone")
            return self.add(
                1,
                "build the old ddflow from the clone",
                None,
                f"uv venv failed: {(made.stderr or str(made.error)).strip()[-160:]}",
            )
        python = venv / "bin" / "python"
        built = self.capture(
            ["uv", "pip", "install", "--quiet", "--python", str(python), str(self.pristine)],
            timeout=TIMEOUT_S * 3,
            env=env,
        )
        if not built.ran or built.returncode != 0:
            self.unrunnable("build the old ddflow from the clone")
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
        st = folded_state(repo)
        for item in sorted(st.items)[: self.opt.max_show or None]:
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
            or f"{len(READS)} read commands and {min(len(st.items), self.opt.max_show or len(st.items))} item(s), state unchanged",
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
        detail += self._contested_detail(items)
        self.add(3, "upgrade --plan", not ((asked and self.opt.asked_fails) or odd), detail)
        return plan

    def _contested_detail(self, items: list[dict[str, Any]]) -> str:
        """Remember the records the plan holds CONTESTED and say they are an expected step."""
        held = [i for i in items if i["category"] == "records"]
        self.contested = {f"{i['kind']} {i['record']}" for i in held}
        if not held:
            return ""
        return (
            f"; {len(held)} contested record(s) are an expected plan step (the operator "
            "resolves them, the apply does not): "
            + ", ".join(f"{i['kind']} {i['record']} -> {i['fix'].split('  ')[0]}" for i in held)
        )

    def step_apply(self) -> bool:
        repo = self.clone("apply")
        edited = self._hand_edit(repo)
        before_users = users_part(state_digests(repo))
        before_tree, before_shards = tree_digest(repo), shard_bytes(repo)
        done = self.apply_in(repo, "snapshot", "dry-apply")
        problems: list[str] = []
        if edited and not _edit_kept(repo, edited):
            problems.append(f"the hand edit inside the stamped block of {edited} was lost")
        if done.returncode != 0:
            problems.append(
                f"apply exited {done.returncode}: {(done.stderr or done.stdout).strip()[-200:]}"
            )
        problems += self._after_apply(repo, before_users)
        self.commit_all(repo, "applied")
        # a second apply changes nothing: no file, no event (the notes a plan keeps listing are
        # acknowledged again, which writes nothing)
        events, tree = len(events_of(repo)), tree_digest(repo)
        again = self.ddflow(
            self.new, repo, "upgrade", "--apply", "--backup", "none", agent="dry-apply"
        )
        if again.returncode != 0 or len(events_of(repo)) != events or tree_digest(repo) != tree:
            problems.append(f"a second apply was not a no-op (exit {again.returncode})")
        problems += self._restore(repo, before_tree, before_shards)
        self.report.facts["apply"] = "ok" if not problems else "problems"
        return self.add(
            4,
            "apply with a snapshot, again, restore",
            not problems,
            "; ".join(problems)
            or "users' records unchanged, "
            + (f"hand edit in {edited} kept, " if edited else "no stamped block to hand-edit, ")
            + "doctor adds no new problem, second apply a no-op, restore exact",
        )

    def _hand_edit(self, repo: Path) -> str:
        """Write a line of our own inside a stamped block of a managed instruction file and
        commit it, as an operator's hand edit would sit: the apply must not lose it. The name
        of the file, or "" when the project has no stamped block."""
        for name in ("CLAUDE.md", "AGENTS.md"):
            path = Path(repo) / name
            lines = path.read_text("utf-8").splitlines(keepends=True) if path.is_file() else []
            for at, line in enumerate(lines):
                if "ddflow:begin" in line and "sha=" in line:
                    lines.insert(at + 1, f"{HAND_EDIT}\n")
                    fsio.replace_text(path, "".join(lines))
                    self.commit_all(repo, "a hand edit")
                    return name
        return ""

    def _after_apply(self, repo: Path, before_users: dict[str, str]) -> list[str]:
        out: list[str] = []
        after = users_part(state_digests(repo))
        moved = [k for k in before_users if before_users[k] != after.get(k)]
        if moved:
            out.append(f"the apply changed {', '.join(moved)}")
        wrong = self.doctor_verdict(repo, "dry-apply")
        if wrong:
            out.append(f"doctor after the apply: {wrong}")
        left = self.ddflow(self.new, repo, "upgrade", "--plan", "--json", agent="dry-apply")
        todo = [i["id"] for i in _items(left) if i["action"] != "note"]
        if todo:
            out.append(f"the plan after the apply still holds {', '.join(todo[:5])}")
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
        upgraded = self.apply_in(repo, "none", "dry-apply")
        if upgraded.returncode != O.OK:
            return self.add(
                5,
                "old ddflow on the upgraded copy",
                False,
                f"the copy was not upgraded (exit {upgraded.returncode}): "
                f"{(upgraded.stderr or upgraded.stdout).strip()[-160:]}",
            )
        self.commit_all(repo, "applied")
        shards = shard_bytes(repo)
        bad: list[str] = []
        for args in (("status", "--json"), ("doctor",), ("brief",)):
            done = self.ddflow(old, repo, *args, agent="dry-old")
            if (
                not done.ran
                or done.returncode not in (O.OK, O.FAIL, O.NOTHING)
                or "Traceback" in done.stderr
            ):
                bad.append(
                    f"old {' '.join(args)} exited {done.returncode}: {done.stderr.strip()[-120:]}"
                )
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
        after = folded_state(repo)
        if after.fold_problems or after.skipped_kinds:
            bad.append("the new code cannot fold what the old one left")
        how = "refused its write with the remedy" if refused else "wrote without disturbing a byte"
        return self.add(
            5, "old ddflow on the upgraded copy", not bad, "; ".join(bad) or f"read fine, {how}"
        )

    def step_connect(self) -> bool:
        repo = self.clone("connect")
        before_tree, known = tree_digest(repo), {e.get("id") for e in events_of(repo)}
        forced = self._serve(repo, _HELLO, start_timeout=FORCED_TIMEOUT_S)
        self._forget_start(repo)  # each session runs the start check itself, not a kept answer
        default = self._serve(repo, _HELLO, start_timeout=None)  # as shipped
        self._forget_start(repo)
        first = self._serve(repo, [*_HELLO, _call(3, "ddflow_brief", {}), _TOOLS_LIST])
        if START_TIMED_OUT in (first.get(1) or {}).get("instructions", ""):
            self.unrunnable("MCP connect: propose, wait for consent, apply")
            return self.add(
                6,
                "MCP connect: propose, wait for consent, apply",
                None,
                f"the server's start check ran out of its time bound even with {START_TIMEOUT_S}s "
                "(a very loaded machine, or a log too big for it): it could not propose",
            )
        size, problems = self._proposed(first)
        problems += self._noted(forced) + self._default_path(default)
        applied = [
            e for e in events_of(repo) if e.get("id") not in known and e.get("kind") in APPLYING
        ]
        if applied or tree_digest(repo) != before_tree:
            problems.append("something was applied before the operator consented")
        problems += self._consented(repo)
        stale, more = self._told_to_restart(repo)
        problems += more
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
            or f"proposed on connect, nothing applied until consent, then applied; "
            f"tools/list {size} bytes; {told}",
        )

    @staticmethod
    def _forget_start(repo: Path) -> None:
        """Drop the start report a session kept in the (git-ignored) project marker."""
        (repo / US.MARKER).unlink(missing_ok=True)

    def _noted(self, forced: dict[int, dict[str, Any]]) -> list[str]:
        """A start check given next to no time is a NOTE in the handshake: the upgrade is
        pending, the operator is asked and told where the plan is (B-start-timeout-drops-
        proposal). A log so small that the plan fit in the time gets the proposal instead,
        which is as good: what fails is silence."""
        text = (forced.get(1) or {}).get("instructions", "")
        note = all(w in text for w in ("PENDING", "Ask the operator", "ddflow upgrade --plan"))
        if note or ("newer than this project" in text and "Ask the operator" in text):
            return []
        return ["a start check that ran out of time left no note that the upgrade is pending"]

    def _default_path(self, shipped: dict[int, dict[str, Any]]) -> list[str]:
        """The server with the shipped time bound (a smoke test of the real default, which on an
        idle machine proposes and on a loaded one notes; the forced timeout above is the
        deterministic half): its handshake is never silent about the upgrade."""
        text = (shipped.get(1) or {}).get("instructions", "")
        if "newer than this project" in text and "Ask the operator" in text:
            return []
        return [
            "the handshake with the shipped start_timeout_s neither proposed nor noted the upgrade"
        ]

    def _proposed(self, first: dict[int, dict[str, Any]]) -> tuple[int, list[str]]:
        """What the first session shows: the handshake asks the operator, the tools fit."""
        problems: list[str] = []
        text = (first.get(1) or {}).get("instructions", "")
        if "newer than this project" not in text or "Ask the operator" not in text:
            problems.append("the handshake did not propose the upgrade and ask the operator")
        size, bad = tools_problems((first.get(2) or {}).get("tools") or [], self.opt.tools_budget)
        return size, problems + bad

    def _consented(self, repo: Path) -> list[str]:
        """The operator says yes: the confirmed apply over MCP finishes and doctor is clean."""
        arguments = {
            "apply": "all",
            "backup": "none",
            "confirm": self.confirms,
            "reason": self.opt.reason,
        }
        done = self._serve(repo, [*_HELLO, _call(4, "ddflow_upgrade", arguments)])
        body = _tool_body(done.get(4))
        out: list[str] = []
        if (body.get("applied") or {}).get("failed", 1):
            out.append(f"the confirmed apply over MCP did not finish: {json.dumps(body)[:160]}")
        wrong = self.doctor_verdict(repo, "dry-connect")
        if wrong:
            out.append(f"doctor after the consent: {wrong}")
        return out

    def _told_to_restart(self, repo: Path) -> tuple[list[str], list[str]]:
        """`(the agents `status` says must restart, problems)`: a log holding agents stamped by
        an older ddflow must name them."""
        status = _tool_body(
            self._serve(repo, [*_HELLO, _call(5, "ddflow_upgrade", {"status": True})]).get(5)
        )
        stale = [b["agent"] for b in status.get("behind", [])]
        self.report.facts["restart"] = stale
        folded = folded_state(repo)
        running = V.running()
        if any(is_older(v, running) for v in folded.ddflow_versions) and not stale:
            return stale, ["the log holds older-version agents but none was told to restart"]
        return stale, []

    def _serve(
        self, repo: Path, msgs: list[dict[str, Any]], start_timeout: float | None = START_TIMEOUT_S
    ) -> dict[int, dict[str, Any]]:
        """Talk to the new MCP server over stdio for these messages; replies by request id.
        The start check gets ``start_timeout`` seconds (the rehearsal tests the flow, not the
        speed), or the shipped bound when None. Raises `CouldNotRun` when the server did not
        run to its end."""
        argv = [sys.executable, "-m", "ddflow.surfaces.mcp", "--repo", str(repo)]
        extra = (
            {} if start_timeout is None else {"DDFLOW_UPGRADE_START_TIMEOUT_S": str(start_timeout)}
        )
        done = self.capture(
            argv,
            timeout=TIMEOUT_S,
            cwd=repo,
            env={**self.env(self.new), **extra},
            input="\n".join(json.dumps(m) for m in msgs) + "\n",
        )
        if not done.ran:
            raise CouldNotRun(f"the MCP server did not run: {done.error}")
        replies: dict[int, dict[str, Any]] = {}
        for line in done.stdout.splitlines():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and isinstance(msg.get("id"), int):
                replies[msg["id"]] = msg.get("result") or {"error": msg.get("error")}
        # what the steps judge is in the replies, and a reply that is big is cut in the middle
        # of the command's output: keep each one's head and tail
        self.commands[-1]["replies"] = {
            str(k): bounded(json.dumps(v, default=str), 2500) for k, v in replies.items()
        }
        return replies

    def step_agents(self) -> bool:
        a, b = self.opt.agents
        repo = self.clone("agents")
        up = self.apply_in(repo, "none", a)
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
        # a refusal (no independent reviewer ran) is the design: release, never force. The merge
        # already released the lease (nothing to release is fine, LK-release-at-merge)
        left = (
            self.ddflow(self.new, repo, "release", "DRY-1", agent=a)
            if done.returncode == O.REFUSED
            else done
        )
        took = self.ddflow(self.new, repo, "claim", "DRY-1", agent=b)
        if done.returncode == O.REFUSED and (
            left.returncode not in (O.OK, O.NOTHING) or took.returncode != 0
        ):
            problems.append(
                f"after the honest release {b} could not take the task ({left.returncode}, {took.returncode})"
            )
        wrong = self.doctor_verdict(repo, a)
        if wrong:
            problems.append(wrong)
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

    def union_of(self, one: Path, two: Path) -> Path:
        """A directory holding the shards of both clones side by side, as they were written:
        the log reads it, so a torn line is recovered the way it is in a real log."""
        union = self.opt.work / "union"
        events = union / ".ddflow" / "events"
        events.mkdir(parents=True)
        for tag, repo in (("one", one), ("two", two)):
            for shard in sorted((Path(repo) / ".ddflow" / "events").glob("*.jsonl")):
                shutil.copyfile(shard, events / f"{tag}--{shard.name}")
        return union

    def step_upcaster(self) -> bool:
        kind, problems = upcast_problems(self.pristine)
        return self.add(
            9,
            "an event upcaster over the old log",
            not problems,
            "; ".join(problems)
            or f"every {kind} event upcast with its id kept, the same records folded, "
            "an older reader skips the new version",
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
        written = self.union_of(one, two)
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
        want = state_part_digest(written)
        got = state_part_digest(one)
        ok = want == got
        return self.add(
            8,
            "shards from the old version merge",
            ok,
            "the merged shards fold to what both clones wrote"
            if ok
            else "the merged log folds differently from the union of what both clones wrote",
        )
