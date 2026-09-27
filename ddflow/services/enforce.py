"""Enforcement — the part of the workflow that does not depend on the agent agreeing.

There are three layers between "here is a workflow" and "the workflow happened", and
they are not equally strong. Being honest about which is which is the whole point of
this module:

1. **The MCP ``instructions`` field and the tool descriptions.** The server tells the
   model what to do on connect and at every call. This is *persuasion*: it works most of
   the time and fails silently the rest.
2. **The rules file** (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/*.mdc`). Always in
   context, higher weight than a tool description, still persuasion. A model under
   pressure to finish will skip it, and nothing notices.
3. **Git hooks.** The only layer that is *enforcement*, because it is the repository
   refusing rather than the model choosing. A commit touching files no live lease covers
   is rejected, with the command to fix it.

The hook is deliberately narrow. It answers exactly one question — *does a live lease
held by this agent cover the paths being committed?* — because that is the invariant
everything else rests on: if two agents can commit to the same file without claiming it,
no amount of gate bookkeeping upstream means anything.

**It is honest about being bypassable.** `git commit --no-verify` skips every hook, and
nothing in a local repository can prevent that. So the hook does not pretend: bypasses
are detectable after the fact (`ddflow doctor` reports commits whose paths no lease
covered), and in a setting where it has to be unbypassable the same check belongs in CI,
where the agent is not the one running it.
"""

from __future__ import annotations

import os
import shlex
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..core.model import fold
from ..core.schedule import globs_overlap
from ..infra import proc as P
from ..infra import worktree as W
from ..infra.log import EventLog

HOOK_MARKER = "# DDFLOW-HOOK v1 — managed by `ddflow hooks install`"

#: How many offending paths the refusal lists before summarising. Enough to see the
#: shape of the problem (one stray file vs a whole directory) without burying the
#: remedy underneath the list.
MAX_LISTED_PATHS = 12

_PRE_COMMIT = """#!/bin/sh
{marker}
# Refuses a commit touching paths that no live ddflow lease held by this agent covers.
#
# This is the one mechanical guarantee in the workflow: everything else asks the agent
# nicely. Remove it with `ddflow hooks uninstall`, or set
# [enforce].commit_without_lease = "warn" (or "off") in .ddflow/config.toml.
#
# `git commit --no-verify` skips this, as it skips every hook. That is a property of
# git, not a hole in this check; `ddflow doctor` reports the bypasses after the fact.
{invocation}
"""


def _invocation() -> str:
    """The shell line the hook uses to reach ddflow.

    Resolved AT INSTALL TIME and baked in, because a hook runs with git's environment,
    not the agent's: no virtualenv activated, no PYTHONPATH inherited, and a `cwd` that
    is the repo rather than wherever ddflow lives. An earlier version embedded only
    the interpreter path and produced `No module named ddflow` on every commit from a
    source checkout -- a hook that fails is indistinguishable from a hook that refuses,
    so the commit was blocked for entirely the wrong reason.
    """
    import shutil

    script = shutil.which("ddflow")
    if script and not _running_from_source():
        return f'exec "{script}" hooks check-commit "$@"'
    from ..infra.paths import package_parent

    pkg_parent = str(package_parent())
    return (
        f'PYTHONPATH="{pkg_parent}${{PYTHONPATH:+:$PYTHONPATH}}" '
        f'exec "{sys.executable}" -m ddflow hooks check-commit "$@"'
    )


def _running_from_source() -> bool:
    here = Path(__file__).resolve()
    return not any(part in ("site-packages", "dist-packages") for part in here.parts)


def hooks_dir(repo: Path) -> Path:
    """The hooks directory, resolved through worktrees and `core.hooksPath`.

    A linked worktree's `.git` is a FILE pointing into the primary checkout, and a repo
    may relocate hooks entirely. Reading the resolved path from git rather than assuming
    `.git/hooks` is what makes this work inside the worktrees ddflow itself creates.
    """
    r = P.run(
        ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"not a git repository: {repo}")
    path = Path(r.stdout.strip())
    return path if path.is_absolute() else (Path(repo) / path).resolve()


def install(repo: Path, *, force: bool = False) -> str:
    d = hooks_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    hook = d / "pre-commit"
    if hook.exists():
        existing = hook.read_text("utf-8", errors="replace")
        if HOOK_MARKER in existing:
            hook.write_text(
                _PRE_COMMIT.format(marker=HOOK_MARKER, invocation=_invocation()), "utf-8"
            )
            _chmod_x(hook)
            return f"updated the ddflow hook at {hook}"
        if not force:
            # Never clobber someone else's hook. A workflow tool that silently replaces
            # a project's existing pre-commit checks has done more damage than the
            # discipline it was installing is worth.
            return (
                f"REFUSED: {hook} already exists and is not managed by ddflow. "
                f"Add this line to it yourself:\n"
                f"    {_invocation().replace('exec ', '')} || exit 1\n"
                f"or re-run with --force to replace it."
            )
    hook.write_text(_PRE_COMMIT.format(marker=HOOK_MARKER, invocation=_invocation()), "utf-8")
    _chmod_x(hook)
    return f"installed the ddflow pre-commit hook at {hook}"


def uninstall(repo: Path) -> str:
    hook = hooks_dir(repo) / "pre-commit"
    if not hook.exists():
        return "no pre-commit hook installed"
    if HOOK_MARKER not in hook.read_text("utf-8", errors="replace"):
        return f"REFUSED: {hook} is not managed by ddflow; leaving it alone"
    hook.unlink()
    return f"removed {hook}"


def installed(repo: Path) -> bool:
    try:
        hook = hooks_dir(repo) / "pre-commit"
    except RuntimeError:
        return False
    return hook.exists() and HOOK_MARKER in hook.read_text("utf-8", errors="replace")


def _chmod_x(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _this_worktree(repo: Path) -> Path | None:
    """The git working tree this hook is running in, or None.

    `--show-toplevel` from the cwd, because the hook's cwd IS the tree being committed
    — which is exactly the information needed to decide which lease applies.
    """
    r = P.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return Path(r.stdout.strip()).resolve()


def staged_paths(repo: Path) -> list[str]:
    """Paths this commit will write.

    `--diff-filter=ACMR` over the INDEX, plus `--cached`, because a file the agent just
    created is not in HEAD and a diff against HEAD alone would not see it.
    """
    r = P.run(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        capture_output=True,
        timeout=60,
    )
    # `-z`, or a non-ASCII path comes back C-quoted and matches no lease glob and no view
    # name -- escaping both checks (found in review of 7216f5e, recorded, fixed with the
    # same bug in the log probe).
    return _nul_paths(r.stdout)


def _nul_paths(out: bytes) -> list[str]:
    """Paths from git's `-z` output, as the filesystem names them.

    BYTES, decoded with `os.fsdecode`: `-z` makes git emit raw filename bytes, and text
    mode decoded them strictly as UTF-8 -- one latin-1-named file anywhere in a commit
    raised out of the hook, so the repository could not commit at all. Text mode also
    rewrote a `\r` in a name to `\n`. `fsdecode` (surrogateescape) round-trips any
    name the filesystem holds (roborev on 4f54455).
    """
    return [os.fsdecode(p) for p in out.split(b"\0") if p]


#: Paths ddflow's own bookkeeping writes. Requiring a lease for these would make it
#: impossible to commit the event log that records the lease.
SELF_MANAGED = (".ddflow/", "docs/ddflow/", "AGENTS.md", "CLAUDE.md", ".cursor/rules/")


def check_commit(repo: Path, cfg: Config | None = None, *, agent: str = "") -> tuple[int, str]:
    """(exit_code, message). 0 allows the commit; 1 refuses it.

    Returns a *message*, not a print, so the same logic serves the hook, `doctor`, and
    a CI job without three copies of the wording.
    """
    cfg = cfg or Config.load(repo)
    policy = getattr(cfg, "enforce", None)
    mode = getattr(policy, "commit_without_lease", "warn") if policy else "warn"
    if mode == "off":
        return 0, ""

    paths = [
        p for p in staged_paths(repo) if not any(p.startswith(prefix) for prefix in SELF_MANAGED)
    ]
    if not paths:
        return 0, ""

    log = EventLog(repo, agent or cfg.agent.id or "", log_cfg=cfg.log)
    state = fold(log.read_all(), strict=False)
    now = time.time()
    me = log.agent_id
    here = _this_worktree(repo)
    mine: list[str] = []
    others: dict[str, str] = {}
    for item_id, lease in state.active_leases(now, cfg.lease.grace_s).items():
        # A lease is "mine" if I hold it, OR if it created the very tree this commit is
        # happening in. The second test is the robust one: the hook runs inside a
        # worktree, and the lease that produced that worktree is the relevant claim no
        # matter which process id or identity string made it.
        leased_tree = W.load_path(repo, lease.worktree) if lease.worktree else None
        same_tree = bool(here and leased_tree and leased_tree.resolve() == here)
        if lease.holder == me or same_tree:
            mine.extend(lease.globs)
        else:
            for g in lease.globs:
                others[g] = f"{item_id} ({lease.holder})"

    uncovered = [p for p in paths if not any(globs_overlap(p, g) for g in mine)]
    if not uncovered:
        return 0, ""

    # A path another agent holds is the dangerous case and gets named separately: the
    # remedy is not "claim it", it is "stop".
    stolen = {p: owner for p in uncovered for g, owner in others.items() if globs_overlap(p, g)}

    lines = [
        f"ddflow: {len(uncovered)} staged path(s) are not covered by a lease you hold.",
        "",
    ]
    for p in uncovered[:MAX_LISTED_PATHS]:
        lines.append(f"  {p}" + (f"   <-- held by {stolen[p]}" if p in stolen else ""))
    if len(uncovered) > MAX_LISTED_PATHS:
        lines.append(f"  ... and {len(uncovered) - MAX_LISTED_PATHS} more")
    lines.append("")
    if stolen:
        lines += [
            "STOP: paths marked above are leased by ANOTHER agent. Committing them races",
            "that agent's work. Coordinate, or wait for the lease to be released.",
            "",
        ]
    lines += [
        f"You hold: {', '.join(mine) if mine else '(no live lease)'}",
        "",
        "Fix by claiming the work, or by widening the claim you already hold:",
        "    ddflow next                     # what may be started",
        "    ddflow claim <ID> --globs '...' # lease it and get a worktree",
        "    ddflow update <ID> --globs '...'# widen an existing claim",
        "",
        f'Policy is [enforce].commit_without_lease = "{mode}" in .ddflow/config.toml.',
    ]
    msg = "\n".join(lines)
    if mode == "warn":
        return 0, msg + '\n\n(warning only; set the policy to "block" to refuse)'
    return 1, msg


def staged_bytes(repo: Path, path: str) -> bytes | None:
    """The INDEX copy of ``path`` -- what the commit will actually write.

    Not the working copy: a file fixed on disk but not re-staged would pass a check of
    the disk while the commit carried the broken bytes.
    """
    r = P.run(["git", "-C", str(repo), "show", f":{path}"], capture_output=True, timeout=60)
    return r.stdout if r.returncode == 0 else None


def check_views(repo: Path, cfg: Config | None = None, *, agent: str = "") -> tuple[int, str]:
    """(exit_code, message) for B18: a staged generated view must be byte-identical to
    what the log regenerates now.

    `docs/ddflow/` is SELF_MANAGED, so the lease check waves views through -- rightly,
    since rendering needs no claim -- and until this nothing checked them at all. A view
    that disagrees with the log is worse than none: it is the page people read INSTEAD
    of the log. It goes wrong two ways, both caught here: hand-edited, or stale because
    the queue moved after `ddflow render`.

    A file counts as a view when its name is in `VIEWS` AND its staged content carries
    the GENERATED marker -- so `render --out elsewhere` is still checked, while a
    project's own `QUEUE.md`, or a document that merely quotes the marker, is not.
    Fires only when a view is STAGED: a commit that does not include one is never
    blocked because the queue moved, which would teach everyone to bypass the hook.
    """
    from ..views.markdown import GENERATED, VIEWS, render_views

    cfg = cfg or Config.load(repo)
    mode = cfg.enforce.generated_views
    if mode == "off":
        return 0, ""
    staged: dict[str, bytes] = {}
    names = {name for name, _ in VIEWS}
    for p in staged_paths(repo):
        if Path(p).name not in names:
            continue
        data = staged_bytes(repo, p)
        if data is not None and data.startswith(GENERATED.encode("utf-8")):
            staged[p] = data
    if not staged:
        return 0, ""

    log = EventLog(repo, agent or cfg.agent.id or "", log_cfg=cfg.log)
    # The view is committed WITH a log, and must agree with THAT log -- not with the
    # one on disk. Staging a view rendered from events that are not themselves staged
    # would commit a page describing work its own commit does not record (roborev on
    # 7216f5e, reproduced). Rather than fold shards out of the index, require the log to
    # be fully staged: then the log on disk IS the committed log, and the comparison
    # below is exact.
    probe = _unstaged_under(repo, log.dir)
    if probe.failed:
        return _verdict(
            mode,
            [
                "ddflow: a generated view is staged, but git could not report the state of "
                f"the event log ({_rel(repo, log.dir)}) it must agree with.",
                "",
                "Refusing rather than guessing: an unreadable log state is not a clean one.",
                "Check `git status`; a locked or damaged index is the usual cause.",
            ],
        )
    if probe.paths:
        # Each case gets the command that CLEARS it. A plain `git add` stages nothing for
        # an ignored file and exits 0, so the one remedy for every case left an ignored
        # shard refused forever with identical output (roborev on 8b167e9, reproduced).
        remedy = []
        if probe.ignored:
            remedy += [
                "These are GITIGNORED, so a plain `git add` skips them. Force them in, or",
                "stop ignoring shards (a committed log with some shards ignored cannot",
                "match a view rendered from all of them):",
                "    git add -f " + " ".join(shlex.quote(p) for p in probe.ignored),
            ]
        if set(probe.paths) - set(probe.ignored):
            remedy += [f"    git add {shlex.quote(_rel(repo, log.dir))}"]
        return _verdict(
            mode,
            [
                f"ddflow: a generated view is staged, but the event log it is rendered from "
                f"has {len(probe.paths)} change(s) the commit does not record:",
                "",
                *(
                    f"  {p}" + ("   (gitignored)" if p in probe.ignored else "")
                    for p in probe.paths[:MAX_LISTED_PATHS]
                ),
                "",
                "A committed view must agree with the log committed beside it. Stage both:",
                *remedy,
                "    ddflow render" + _out_hint(sorted(staged)),
                "    git add " + " ".join(shlex.quote(p) for p in sorted(staged)),
            ],
        )
    # Config from the FILES, as `ddflow render` writes with: env overrides belong to
    # whoever typed `git commit`, not to the view.
    want = render_views(fold(log.read_all(), strict=False), Config.load(repo, env={}))
    wrong = sorted(
        p for p, data in staged.items() if _lf(data) != want[Path(p).name].encode("utf-8")
    )
    if not wrong:
        return 0, ""
    return _verdict(
        mode,
        [
            f"ddflow: {len(wrong)} staged generated view(s) differ from what the event log "
            "regenerates now:",
            "",
            *(f"  {p}" for p in wrong),
            "",
            "A view is regenerated from the log, never edited: either it was changed by hand,",
            "or the queue moved after it was rendered. Regenerate it and stage the result:",
            "    ddflow render" + _out_hint(wrong),
            "    git add " + " ".join(shlex.quote(p) for p in wrong),
        ],
    )


def _verdict(mode: str, lines: list[str]) -> tuple[int, str]:
    msg = "\n".join(
        [*lines, "", f'Policy is [enforce].generated_views = "{mode}" in .ddflow/config.toml.']
    )
    if mode == "warn":
        return 0, msg + '\n\n(warning only; set the policy to "block" to refuse)'
    return 1, msg


def _rel(repo: Path, path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(repo).resolve()))
    except ValueError:
        return str(path)


@dataclass(frozen=True)
class LogProbe:
    """What the commit would NOT record under the log dir, and why."""

    paths: list[str]  #: not in the index, or modified since staged
    ignored: list[str]  #: the subset a plain `git add` would skip
    failed: bool = False  #: git could not say -- never read as "clean"


def _unstaged_under(repo: Path, d: Path) -> LogProbe:
    """Paths under ``d`` whose working copy is not what the commit will record:
    modified-but-not-staged, or not in the index at all.

    Two cases, decided by whether ANY shard is tracked. If none is, the project does not
    commit its log, no view can be checked against a committed one, and nothing is
    reported -- the check compares against the working log as before. If one is, EVERY
    file there that is not in the index counts, ignored or not: a project ignoring only
    some shards would otherwise render a view from an ignored shard, commit it beside a
    log that lacks it, and pass (roborev on 43c2034).

    A git failure is `failed`, never an empty list: an empty stdout from a command that
    failed is not evidence of a clean log, and reading it as one would silently reopen
    the exact false pass this exists to close (roborev on 43c2034).
    """
    rel = _rel(repo, d)

    def git(*argv: str) -> list[str] | None:
        # `-z`: without it git C-quotes a non-ASCII path (`"caf\303\251.jsonl"`), and a
        # remedy built from that string names a file that does not exist -- `git add -f`
        # fails and the refusal never clears (roborev on 40950c9, reproduced).
        r = P.run(["git", "-C", str(repo), *argv, "-z", "--", rel], capture_output=True, timeout=60)
        if r.returncode != 0:
            return None
        return _nul_paths(r.stdout)

    tracked = git("ls-files")
    modified = git("diff", "--name-only")
    untracked = git("ls-files", "--others")  # ignored ones included, deliberately
    ignored = git("ls-files", "--others", "--ignored", "--exclude-standard")
    if tracked is None or modified is None or untracked is None or ignored is None:
        return LogProbe([], [], failed=True)
    if not tracked:
        return LogProbe([], [])
    return LogProbe(sorted(set(modified + untracked)), sorted(ignored))


def _lf(data: bytes) -> bytes:
    """Line endings are git's and the platform's business, not the view's content.
    `write_text` writes CRLF on Windows and `core.autocrlf=false` stages it as is."""
    return data.replace(b"\r\n", b"\n")


def _out_hint(paths: list[str]) -> str:
    """` --out DIR` when every wrong view lives outside the default directory together."""
    dirs = {str(Path(p).parent) for p in paths}
    if len(dirs) == 1 and (d := dirs.pop()) != "docs/ddflow":
        return f" --out {shlex.quote(d)}"
    return ""


def check_item_trailer(repo: Path) -> tuple[int, str]:
    """Require an ``Item: <id>`` trailer on the commit being made.

    Enabled by ``[enforce].require_item_trailer``. The trailer is what lets an audit
    reconcile shipped commits against the queue with
    ``git log --format='%(trailers:key=Item,valueonly)'`` instead of parsing prose —
    which is the difference between a check that can run on every commit and one that
    needs an agent to read the whole history.

    Off by default: on a repository with human contributors it is noise, and a rule
    that most commits violate is a rule everyone learns to bypass.
    """
    msg_file = Path(repo) / ".git" / "COMMIT_EDITMSG"
    r = P.run(
        ["git", "-C", str(repo), "rev-parse", "--git-path", "COMMIT_EDITMSG"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode == 0 and r.stdout.strip():
        cand = Path(r.stdout.strip())
        msg_file = cand if cand.is_absolute() else (Path(repo) / cand)
    if not msg_file.is_file():
        # No message to inspect yet (e.g. `-m` handled later in the hook order). Do not
        # invent a failure from missing input -- that is the vacuous-FAIL mirror of the
        # vacuous pass.
        return 0, ""
    text = msg_file.read_text("utf-8", errors="replace")
    if any(ln.startswith("Item:") and ln[5:].strip() for ln in text.splitlines()):
        return 0, ""
    return 1, (
        "ddflow: this commit has no `Item: <id>` trailer, and "
        "[enforce].require_item_trailer is on.\n\n"
        "Add a final line to the commit message, e.g.:\n"
        "    Item: P1.T3\n\n"
        "It is what lets an audit match commits to queue items mechanically."
    )
