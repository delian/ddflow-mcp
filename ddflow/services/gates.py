"""Gates — the quality pipeline every task and every phase passes through.

A gate is one checkpoint with one outcome. Two kinds exist and the difference matters:

* a **command gate** has a shell command; ddflow runs it and the exit code decides;
* an **agent gate** has none, because the work is judgement an LLM does (research,
  a rubber-duck review, a bug hunt). ddflow cannot perform it, so it *demands the
  evidence* and records the agent's answer.

Agent gates are where a workflow usually rots, because "I reviewed it" costs nothing
to say. Three rules push back:

1. **UNAVAILABLE is an outcome, not a synonym for pass.** A reviewer whose endpoint was
   down did not approve anything. It gets its own outcome, its own exit code, and it
   shows up in the completion report as a gap.
2. **Evidence is required for the gates listed in ``gates.evidence_required``.** A
   record with no command, no exit code and no output digest is rejected at the API
   boundary, so a gate cannot be passed by assertion.
3. **A different-family reviewer is checked, not trusted.** Same-family reviewers share
   the author's blind spots, so their agreement is not independent evidence. The runner
   knows which family produced each review and reports when they all match the author.

Exit-code vocabulary, uniform across every ddflow command:
``0`` healthy · ``1`` real failure · ``2`` could not run / nothing to do · ``3``
coordination refused. ``2`` is never collapsed into ``0``; "no data" is reported, never
treated as "no problem".
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.model import GATE_OUTCOMES, Item, State
from ..infra import proc as P
from ..infra.log import EventLog

# Exit vocabulary lives in ONE place: `cli.py`. It used to be declared here too, with a
# different third name for the same code, and nothing imported this copy.


@dataclass
class GateDef:
    id: str
    title: str = ""
    description: str = ""
    command: str = ""
    required: bool = False
    evidence: bool = False
    timeout_s: int = 1800
    reviewer: str = ""  # "" | "different_family" | "same_family_ok"
    applies_to: str = "task"  # task | phase | both
    cwd: str = "worktree"  # worktree | repo
    env: dict[str, str] = field(default_factory=dict)
    prompt: str = ""  # instruction handed to the agent for an agent gate
    #: Edits that MUST make this gate fail. Each `{file, old, new}` is applied to the
    #: worktree, the gate is run, and a non-zero exit is required before the file is
    #: restored. A gate with no registered mutation is a gate nobody has shown can go
    #: red — see `verify`.
    mutations: list[dict[str, str]] = field(default_factory=list)

    @property
    def is_command_gate(self) -> bool:
        return bool(self.command.strip())


#: The shipped default pipeline. It is the operator's ten steps, in their order, with
#: the phase-level pass expressed in the same vocabulary. Every field is overridable
#: from `.ddflow/gates.toml`; nothing here is compiled in.
DEFAULT_GATES: dict[str, GateDef] = {
    "research": GateDef(
        id="research",
        title="Research",
        applies_to="both",
        evidence=True,
        description="State a falsifiable claim before writing code, and probe it.",
        prompt=(
            "Before implementing: state the claim, its mechanism, the single "
            "observation that would REFUTE it, and the cheapest test that could. "
            "Run that test and paste its command AND output. Record with "
            "`ddflow research add --verdict CONFIRMED|REFUTED|THEORETICAL`. "
            "A pass with no CONFIRMED/REFUTED label is a literature summary, "
            "not research."
        ),
    ),
    "rules": GateDef(
        id="rules",
        title="Rules, lessons and memory",
        applies_to="both",
        description="Load project rules and the lessons that bear on THIS task.",
        prompt=(
            "Run `ddflow brief --item <id>`. It returns the project rules digest "
            "and the handful of past lessons ranked against this task's text. Do "
            "not read the whole lessons corpus; that is what the ranking is for."
        ),
    ),
    "implement": GateDef(
        id="implement",
        title="Implement",
        required=True,
        applies_to="task",
        description="Write the change in the task's own worktree.",
        prompt=(
            "Implement in the worktree ddflow created. Touch only files inside "
            "this task's declared globs; if you must widen them, run "
            "`ddflow item update <id> --globs ...` FIRST so the conflict detector "
            "can see it."
        ),
    ),
    "rubber_duck": GateDef(
        id="rubber_duck",
        title="Rubber-duck review",
        evidence=True,
        reviewer="different_family",
        applies_to="task",
        description="An independent model tries to REFUTE the change.",
        prompt=(
            "Dispatch a reviewer on a DIFFERENT model family than the author. Ask "
            "it to refute, not to review: 'find the input that makes this wrong'. "
            "When uncertain it must report nothing — a reviewer rewarded for "
            "finding things finds things."
        ),
    ),
    "critic": GateDef(
        id="critic",
        title="Cross-family critic",
        evidence=True,
        reviewer="different_family",
        applies_to="task",
        description="A second, differently-trained critic on the diff and the intent.",
        prompt=(
            "Run the configured critic over the diff WITH the task's intent. It "
            "flags where the diff and the stated intent disagree. Exit 2 means "
            "UNAVAILABLE and must be recorded as such — never as a pass."
        ),
    ),
    "standards": GateDef(
        id="standards",
        title="Coding standards",
        evidence=True,
        applies_to="task",
        description="Automated standards/architecture review (roborev, codeguide, linters).",
        prompt=(
            "Run the project's standards tools. Apply their FINDINGS; verify their "
            "FIXES by running the tests — a suggested fix reasoning from general "
            "language rules does not know your types' operator overloads."
        ),
    ),
    "unit_tests": GateDef(
        id="unit_tests",
        title="Unit tests",
        required=True,
        evidence=True,
        applies_to="both",
        command="",
        timeout_s=3600,
        description="The project's test command must pass.",
        prompt=(
            "Set `[gate.unit_tests].command` in .ddflow/gates.toml to your test "
            "command so this gate runs itself."
        ),
    ),
    "bug_hunt": GateDef(
        id="bug_hunt",
        title="Bug hunt",
        applies_to="both",
        evidence=True,
        description="Hunt the recurring bug classes across everything this unit touched.",
        prompt=(
            "Hunt: off-by-one, empty-collection/vacuous-truth, silently dropped "
            "config, torn-tail resume, unavailable-treated-as-clean, dead knobs. "
            "A finding may only change source if a runnable probe demonstrates it "
            "AND ships as the regression test in the same commit — then MUTATE the "
            "fix and watch the probe fail. A probe that passes both ways proves "
            "nothing. No probe -> file it as THEORETICAL, change nothing."
        ),
    ),
    "dedupe": GateDef(
        id="dedupe",
        title="Deduplication",
        applies_to="both",
        evidence=True,
        description="Did this work re-implement something the project already has?",
        prompt=(
            "Search by OPERATION, not by the name you invented ('retry with "
            "backoff', 'atomic write'). Start from a mechanical clone report if "
            "the project has one. Second occurrence: reuse or extract. Third: "
            "extraction is mandatory."
        ),
    ),
    "live_test": GateDef(
        id="live_test",
        title="Live smoke run",
        applies_to="phase",
        evidence=True,
        description="Actually run the feature end to end, not just its tests.",
        prompt=(
            "Run the real thing on a small input and paste the output. A green "
            "unit suite and a working feature are different claims."
        ),
    ),
    "corrections": GateDef(
        id="corrections",
        title="Corrections",
        applies_to="phase",
        description="Apply what the phase-level passes surfaced.",
        prompt="Fix what the phase gates found; each fix carries its own regression test.",
    ),
    "tasks": GateDef(
        id="tasks",
        title="Member tasks",
        required=True,
        applies_to="phase",
        description="Fan-out point: every task in the phase reaches done.",
        prompt=(
            "Not performed by hand. `ddflow next --phase <id>` offers the ready "
            "set; independent tasks run in parallel worktrees."
        ),
    ),
    "merge": GateDef(
        id="merge",
        title="Merge",
        required=True,
        applies_to="both",
        description="Land the work on the base branch.",
        prompt="`ddflow merge <id>` — merges from the primary checkout without a checkout.",
    ),
}


def load_gates(root: Path, cfg: Config) -> dict[str, GateDef]:
    """Defaults, overlaid by ``[gate.*]`` from the config.

    Read from ``.ddflow/config.toml`` first, then ``.ddflow/gates.toml`` if it
    exists. **Both**, because a project should have ONE place to configure and the
    obvious place is the config file — but an operator who prefers to split the gate
    definitions out should not be told they cannot. `gates.toml` wins on a conflict,
    being the more specific file.

    This read used to look at `gates.toml` ALONE, which meant `ddflow configure` (and
    the `ddflow_configure` MCP tool) accepted a `[gate.unit_tests]` block, wrote it to
    `config.toml`, reported success, and changed nothing. A config write that silently
    does nothing is worse than one that errors.

    Overlay rather than replace: a project that only wants to set
    ``unit_tests.command`` writes three lines and still inherits every prompt and
    policy. A file that had to restate all thirteen gates to change one would be copied
    once and then drift.
    """
    from ..infra import tomlcfg

    gates = {k: GateDef(**{**v.__dict__}) for k, v in DEFAULT_GATES.items()}
    for gid, spec in tomlcfg.overlay_table(
        tomlcfg.config_paths(root, "gates.toml"), "gate", GateDef
    ).items():
        base = gates.get(gid) or GateDef(id=gid)
        for k, v in spec.items():
            setattr(base, k, v)
        base.id = gid
        gates[gid] = base
    for gid in cfg.gates.required:
        if gid in gates:
            gates[gid].required = True
    for gid in cfg.gates.evidence_required:
        if gid in gates:
            gates[gid].evidence = True
    return gates


def pipeline_for(item: Item, cfg: Config) -> list[str]:
    return list(cfg.gates.phase_pipeline if item.kind == "phase" else cfg.gates.task_pipeline)


@dataclass
class GateStatus:
    item: str
    pipeline: list[str]
    done: list[str]
    current: str
    blocked_by: list[str]
    unavailable: list[str]
    skipped: list[str]
    complete: bool
    #: Pipeline gates with NO recorded outcome at all. Silence is its own state: it is
    #: neither a pass nor a failure, and `gates.require_outcome` decides whether it
    #: blocks completion.
    silent: list[str] = field(default_factory=list)

    def render(self) -> str:
        marks = {
            "passed": "[x]",
            "failed": "[!]",
            "unavailable": "[?]",
            "partial": "[~]",
            "skipped": "[-]",
            "": "[ ]",
        }
        return "\n".join(f"  {marks.get(o, '[ ]')} {g}" for g, o in self.rows)

    rows: list[tuple[str, str]] = field(default_factory=list)


def status(state: State, cfg: Config, item_id: str) -> GateStatus:
    """Where is this item in its pipeline, and what is the next thing to do?"""
    it = state.items.get(item_id)
    if it is None:
        raise KeyError(item_id)
    gates = pipeline_for(it, cfg)
    rows = [(g, it.gate_outcome(g)) for g in gates]
    done = [g for g, o in rows if o in ("passed", "skipped")]
    blocked = [g for g, o in rows if o == "failed"]
    unavail = [g for g, o in rows if o in ("unavailable", "partial")]
    skipped = [g for g, o in rows if o == "skipped"]
    current = ""
    for g, o in rows:
        if o not in ("passed", "skipped"):
            current = g
            break
    required = set(cfg.gates.required)
    complete = (
        all(o == "passed" or (o == "skipped" and g not in required) for g, o in rows)
        and not blocked
    )
    return GateStatus(
        item=item_id,
        pipeline=gates,
        done=done,
        current=current,
        blocked_by=blocked,
        unavailable=unavail,
        skipped=skipped,
        complete=complete,
        rows=rows,
        silent=[g for g, o in rows if not o],
    )


def stale_evidence(state: State, cfg: Config, item_id: str, cwd: Path) -> list[str]:
    """Gates whose evidence describes a tree that has since changed.

    The hazard B21 names, and the ordinary way it happens: run the tests, edit one more
    thing, complete. The recorded pass is then true about source nobody is shipping —
    and it is indistinguishable, in the log, from a pass about the code that shipped.

    Only gates in `gates.evidence_required` are checked. The others legitimately record
    before the work is finished: `implement` is *supposed* to precede the edits that
    follow it, and flagging that would make this noise, which is how a real warning
    stops being read.

    Returns [] when the tree cannot be fingerprinted — a check that cannot run says so
    by finding nothing, and `run_command_gate` records "" in exactly that case.
    """
    it = state.items.get(item_id)
    now = tree_fingerprint(cwd)
    if it is None or not now:
        return []
    stale = []
    for gid in cfg.gates.evidence_required:
        rec = it.gates.get(gid)
        was = (rec.evidence or {}).get("tree_sha", "") if rec else ""
        if rec and rec.outcome == "passed" and was and was != now:
            stale.append(gid)
    return sorted(stale)


def inert_requirements(cfg: Config) -> list[str]:
    """Gates named in ``gates.required`` that no pipeline actually runs.

    A required gate is enforced by intersecting it with the item's pipeline, which
    means naming one that is in neither pipeline makes the requirement quietly
    **disappear** rather than raise — the vacuous-truth class applied to the very
    mechanism that exists to stop vacuous passes. An operator who sets
    ``required = ["critic"]`` and trims `critic` out of `task_pipeline` gets a project
    where nothing is required at all, reported as fully compliant.

    Reported rather than raised at load time, because a config that is wrong in one
    field should still let `ddflow doctor` run and explain itself.
    """
    pipelines = set(cfg.gates.task_pipeline) | set(cfg.gates.phase_pipeline)
    return sorted(set(cfg.gates.required) - pipelines)


@dataclass
class MutationResult:
    gate: str
    file: str
    applied: bool
    detected: bool
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.applied and self.detected


def verify(
    state: State,
    cfg: Config,
    gates: dict[str, GateDef],
    gate_id: str,
    repo: Path,
    item: Item | None = None,
) -> tuple[list[MutationResult], str]:
    """Break what a gate guards and require it to notice. Returns (results, reason).

    **The anti-vacuous-pass check, turned on ddflow's own checks.** The pipeline has
    ten gates and nothing anywhere proved a single one of them was capable of going
    red. A gate that cannot fail is worse than no gate: it reports success on every
    change, and everyone downstream reads that as evidence.

    Two rules make this honest, and the first is the one that is usually got wrong:

    1. **A mutation that did not apply is not a passed mutation test.** If `old` is not
       found — or is found more than once, so the edit is ambiguous — the check FAILS
       rather than skipping. Silently skipping turns "the mutation never happened" into
       a green run, which reads as "the gate cannot detect this": the exact opposite of
       the truth, delivered confidently.
    2. **The file is restored whatever happens**, including on exception, or a failed
       verification leaves the worktree broken and the next gate reports a failure that
       is the verifier's fault.

    3. **A green baseline is required first.** A gate that is already red — one
       pre-existing failing test, a tool that stopped being installed, a flake — reports
       `failed` for every mutation, so every `detected` comes back True and this
       function certifies it as able to fail. It cannot: it was red before anything was
       touched. That is the vacuous-pass class, inside the check written to catch the
       vacuous-pass class, and it was CONFIRMED by the cross-family critic on
       2026-09-24 with the probe now in `tests/test_gate_verify.py`.

    A gate with zero registered mutations is itself reported as a failure. Declaring a
    check you have never shown can fail is the thing this exists to catch.

    **Callers must check BOTH components.** A non-empty `reason` is a pre-flight
    failure and `results` is then empty — and `all([])` is True, so scoring on
    `results` alone turns every pre-flight failure into a pass. The rule is
    `verified = bool(results) and not reason and all(r.ok for r in results)`.
    """
    gdef = gates.get(gate_id)
    if not gdef:
        return [], f"unknown gate {gate_id!r}; known: {', '.join(sorted(gates))}"
    if not gdef.is_command_gate:
        return [], (
            f"{gate_id} is an agent gate — it has no command to run, so there is "
            f"nothing to mutate. Its honesty rests on the evidence contract instead."
        )
    if not gdef.mutations:
        return [], (
            f"{gate_id} has NO registered mutations, so nothing has ever shown it can "
            f"fail. Add `mutations = [{{ file = '...', old = '...', new = '...' }}]` to "
            f"[gate.{gate_id}]: an edit that this gate must catch."
        )

    cwd = repo
    if item is not None and gdef.cwd == "worktree" and item.worktree:
        from ..infra import worktree as W

        # No `or repo` fallback. An item that HAS a worktree whose path no longer
        # resolves is a broken state, and falling back writes the mutation into the
        # primary checkout and then reports a verdict about the wrong tree. Refused,
        # named, and nothing is touched.
        resolved = W.load_path(repo, item.worktree)
        if resolved is None:
            return [], (
                f"{item.id} records a worktree ({item.worktree}) that no longer "
                f"resolves. Refusing to mutate the primary checkout in its place — "
                f"run `ddflow recover` or re-claim the item first."
            )
        cwd = resolved

    # The baseline. Everything below compares against it, and without it a red gate
    # "detects" every mutation.
    baseline, base_ev = run_command_gate(gdef, cwd)
    if baseline != "passed":
        return [], (
            f"{gate_id} does not pass on the UNMUTATED source — it reported "
            f"{baseline!r} before anything was changed, so there is no green baseline "
            f"to compare against and a 'detected' result would prove nothing. "
            f"{base_ev.get('reason', '')}".strip()[:400]
        )

    results: list[MutationResult] = []
    for mut in gdef.mutations:
        target = cwd / mut.get("file", "")
        old, new = mut.get("old", ""), mut.get("new", "")
        if not target.is_file():
            results.append(
                MutationResult(gate_id, mut.get("file", ""), False, False, "file not found")
            )
            continue
        src = target.read_text("utf-8")
        count = src.count(old)
        if count != 1:
            results.append(
                MutationResult(
                    gate_id,
                    mut.get("file", ""),
                    False,
                    False,
                    f"`old` appears {count} time(s); the edit must be unambiguous. "
                    f"A mutation that did not apply is NOT a passed mutation test.",
                )
            )
            continue
        try:
            target.write_text(src.replace(old, new, 1), "utf-8")
            outcome, ev = run_command_gate(gdef, cwd)
            detected = outcome == "failed"
            results.append(
                MutationResult(
                    gate_id,
                    mut.get("file", ""),
                    True,
                    detected,
                    ""
                    if detected
                    else f"the gate reported {outcome!r} on mutated source — it cannot "
                    f"see this class of change. {ev.get('reason', '')}"[:300],
                )
            )
        finally:
            target.write_text(src, "utf-8")
    return results, ""


#: How many untracked files `tree_fingerprint` will hash before giving up on content
#: and falling back to their names alone. `--exclude-standard` already drops anything
#: gitignored, so a repository normally has a handful; a run that has just dumped ten
#: thousand artifacts into an un-ignored directory should not turn every gate into a
#: full read of them. Configurable because the right number depends on the project --
#: raise it if your work genuinely spans more new files than this.
MAX_UNTRACKED_HASHED = 512


#: Paths the fingerprint must IGNORE. `.ddflow/` is this tool's own bookkeeping, and
#: recording a gate's outcome writes an event into it — so including it made the
#: fingerprint move as a DIRECT RESULT of taking it, and every completion warned that
#: the tree had changed since the gate ran. The evidence a gate produces is about the
#: SOURCE; the fact that recording it appended to a log is not a reason to distrust it.
#:
#: Expressed as git pathspecs so the exclusion happens inside git rather than by
#: filtering its output afterwards — a post-filter has to re-implement pathspec
#: matching, and would drift from what git itself considers inside the directory.
FINGERPRINT_EXCLUDE: tuple[str, ...] = (":(exclude).ddflow", ":(exclude).ddflow/**")


def _untracked_digest(cwd: Path) -> str:
    """Content ids for untracked files, or their names when there are too many.

    `git hash-object` WITHOUT `-w`: it computes the object ids and writes nothing to
    the object store, so this stays an observation. Binary content is covered here for
    free, because hash-object hashes bytes and does not care what they are.
    """
    from ..infra import worktree as W

    listed = W.git(
        cwd, "ls-files", "--others", "--exclude-standard", "--", ".", *FINGERPRINT_EXCLUDE
    )
    if not listed.ok or not listed.out.strip():
        return ""
    paths = listed.out.splitlines()
    if len(paths) > MAX_UNTRACKED_HASHED:
        # Names only. Degraded, and SAID so in the digest rather than silently: a
        # fingerprint that quietly stopped covering content would make `stale_evidence`
        # go quiet for the repositories that need it most.
        return f"names-only:{len(paths)}:" + digest("\n".join(sorted(paths)))
    hashed = W.git(cwd, "hash-object", *paths)
    ids = hashed.out.splitlines()
    # A short or long reply must not be zipped silently: `zip` would truncate to the
    # shorter list, pairing hashes with the wrong paths and producing a fingerprint
    # that looks entirely plausible and means nothing. Fall back to names, which is
    # weaker but honest.
    if not hashed.ok or len(ids) != len(paths):
        return digest("\n".join(sorted(paths)))
    return digest("\n".join(f"{h} {p}" for h, p in zip(ids, paths, strict=True)))


#: `git diff --numstat` emits three tab-separated fields per changed file:
#: additions, deletions, path.
_NUMSTAT_FIELDS = 3


def diff_stat(cwd: Path) -> dict[str, int]:
    """Files and lines changed against HEAD, plus untracked files. Read-only.

    Deliberately NOT part of `tree_fingerprint`: a fingerprint answers "is this the
    same tree", and two different trees can share a line count. Mixing a human-readable
    magnitude into an identity would make the identity weaker and the magnitude
    unavailable on its own.

    Uses the same `.ddflow/` exclusion as the fingerprint, for the same reason: a
    number that grows every time ddflow records an event describes ddflow's bookkeeping
    rather than the work.

    Returns zeros rather than raising outside a repository. A gate can legitimately run
    where git does not reach, and a missing statistic is honest where a fabricated one
    is not -- but the keys are always present, so a reader never has to distinguish
    "no change" from "the field did not exist yet".
    """
    from ..infra import worktree as W

    out = {"files": 0, "insertions": 0, "deletions": 0, "untracked": 0}
    if not W.git(cwd, "rev-parse", "HEAD").ok:
        return out
    r = W.git(cwd, "diff", "HEAD", "--numstat", "--", ".", *FINGERPRINT_EXCLUDE)
    if r.ok:
        for line in r.out.splitlines():
            # `--numstat` is exactly: additions, deletions, path. A rename emits the
            # path as `old => new`, which still lands in field three, so splitting on
            # tab and requiring three fields is correct for every form.
            parts = line.split("\t")
            if len(parts) < _NUMSTAT_FIELDS:
                continue
            add, rem = parts[0], parts[1]
            out["files"] += 1
            # `-` for a binary file, which has no line count. Counted as a changed
            # FILE with zero lines rather than skipped, so a commit of nothing but
            # binaries does not report "0 files changed".
            out["insertions"] += int(add) if add.isdigit() else 0
            out["deletions"] += int(rem) if rem.isdigit() else 0
    u = W.git(cwd, "ls-files", "--others", "--exclude-standard", "--", ".", *FINGERPRINT_EXCLUDE)
    if u.ok and u.out.strip():
        out["untracked"] = len(u.out.splitlines())
    return out


def tree_fingerprint(cwd: Path) -> str:
    """What the working tree looked like, committed and uncommitted.

    `HEAD` alone is not enough -- the interesting state during a gate run is almost
    always dirty -- so this is the commit, plus the CONTENT of the tracked changes,
    plus the LIST of everything else git reports.

    Porcelain alone was not enough either, and that was a real bug. `git status
    --porcelain` is two status letters and a path: no content, no size, no mtime. So
    once a file was modified, every further edit to that same file produced
    byte-identical output and the digest did not move. That is not an edge case, it is
    the normal one -- at gate time the agent has been editing all along, so the tree is
    already dirty when the fingerprint is taken and the clean->dirty transition has
    already happened. `stale_evidence`'s own documented scenario ("run the tests, edit
    one more thing, complete") was therefore the case it could not see, and a gate that
    passed on superseded source read as fresh evidence.

    So `git diff HEAD` goes in as well: content-sensitive for tracked files, staged or
    not, and it adds no new noise because untracked paths were already in the porcelain
    listing. Cost is proportional to the SIZE OF THE CHANGES, not to the tree.

    `.ddflow/` is EXCLUDED throughout. Recording a gate outcome appends an event to it,
    so counting it made the fingerprint move as a direct consequence of taking it, and
    every completion then warned that the tree had changed since the gate ran. A
    warning that always fires is one nobody reads — and it would have fired on the
    ordinary path, not an edge case.

    UNTRACKED files are hashed separately, and that is not an optional extra: `git diff
    HEAD` never shows them and porcelain shows only `?? path`, so without this a brand
    new module -- untracked until its first commit, which is the ORDINARY state of
    agent work -- could be rewritten completely between the gate and the completion
    with the fingerprint unmoved. `git hash-object` without `-w` computes the ids and
    writes nothing, so the observer still does not change what it observes.

    **Known limit, stated rather than papered over:** `git diff` renders a TRACKED
    binary file as "Binary files ... differ" with no content, so a re-edit of an
    already-modified tracked binary is invisible. Untracked binaries are covered (they
    are hashed bytewise). The fix for the remaining case -- `--binary`, base85-encoding
    whole blobs into the digest -- costs far more than it buys here. Filed as B95.

    Not `write-tree` or `stash create`: both WRITE, and a function whose job is to
    observe must not change what it observes. Outside a repository it returns ""
    rather than raising, because a gate can legitimately run somewhere git does not
    reach, and a missing fingerprint is honest where a fabricated one is not.
    """
    from ..infra import worktree as W

    head = W.git(cwd, "rev-parse", "HEAD")
    if not head.ok:
        return ""
    status = W.git(cwd, "status", "--porcelain", "--", ".", *FINGERPRINT_EXCLUDE)
    # `HEAD` and not `--cached`: staged and unstaged changes are equally "not what is
    # committed", and a gate cares about the files on disk it just ran against.
    diff = W.git(cwd, "diff", "HEAD", "--", ".", *FINGERPRINT_EXCLUDE)
    parts = [status.out if status.ok else "", diff.out if diff.ok else ""]
    parts.append(_untracked_digest(cwd))
    body = "\x00".join(parts)
    dirt = digest(body) if body.strip() else "clean"
    return f"{head.out.strip()[:12]}+{dirt}"


def digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8", "replace"), digest_size=8).hexdigest()


def run_command_gate(
    gdef: GateDef, cwd: Path, *, env: dict[str, str] | None = None
) -> tuple[str, dict[str, Any]]:
    """Execute a command gate. Returns (outcome, evidence).

    The distinction this function exists to preserve: a command that *ran and failed*
    is ``failed``; a command that *could not run* — binary missing, cwd gone, timed out —
    is ``unavailable``. Collapsing them lets a tool that quietly stopped being installed
    read as a suite that quietly started passing.
    """
    if not gdef.is_command_gate:
        return "unavailable", {"reason": "no command configured for this gate"}
    if not cwd.exists():
        return "unavailable", {"reason": f"working directory {cwd} does not exist"}

    # Pre-flight the executable. Under `shell=True` a missing binary exits 127, which
    # is indistinguishable from a test suite that chose to exit 127 -- so a tool that
    # quietly stopped being installed would read as a suite that ran and failed.
    # Resolving it BEFORE running removes the ambiguity at the source. Only attempted
    # for a simple leading token; anything with shell syntax up front is the operator
    # deliberately writing shell, and is left alone.
    missing = _missing_executable(gdef.command)
    if missing:
        return "unavailable", {
            "reason": f"executable {missing!r} is not on PATH -- the gate could not run. "
            f"This is NOT a failing check; install it or reconfigure the gate.",
            "command": gdef.command,
            "missing_executable": missing,
        }
    # PYTHONDONTWRITEBYTECODE, before the gate's own env so an operator can still
    # override it deliberately. CPython invalidates a `.pyc` on the source's mtime and
    # SIZE -- and an agent editing in a loop produces same-second, same-size edits by
    # accident, which leaves a stale cache that looks valid. The run AFTER a patch then
    # executes the code from BEFORE it and reports a pass about source that is no
    # longer there: the worst possible failure for a verification step.
    full_env = {"PYTHONDONTWRITEBYTECODE": "1", **os.environ, **gdef.env, **(env or {})}
    start = time.time()
    try:
        p = P.run(
            gdef.command,
            shell=True,
            cwd=str(cwd),
            env=full_env,
            capture_output=True,
            text=True,
            timeout=gdef.timeout_s,
        )
    except subprocess.TimeoutExpired:
        return "unavailable", {
            "reason": f"timed out after {gdef.timeout_s}s",
            "command": gdef.command,
            "elapsed_s": round(time.time() - start, 1),
        }
    except (OSError, ValueError) as exc:
        return "unavailable", {"reason": f"could not execute: {exc}", "command": gdef.command}
    out = (p.stdout or "") + (p.stderr or "")
    # Belt and braces for the compound-command case the pre-flight cannot inspect
    # (pipes, &&, subshells): POSIX reserves 127 for "command not found" and 126 for
    # "found but not executable", and the shell says so on stderr.
    if p.returncode in (126, 127) and _looks_like_not_found(p.stderr or ""):
        return "unavailable", {
            "reason": f"shell reported exit {p.returncode} (command not found / not "
            f"executable): {(p.stderr or '').strip()[:200]}",
            "command": gdef.command,
            "exit": p.returncode,
        }
    ev = {
        "command": gdef.command,
        "exit": p.returncode,
        "elapsed_s": round(time.time() - start, 1),
        "output_digest": digest(out),
        "output_bytes": len(out),
        "tail": out[-2000:],
        # WHICH tree this is evidence about. Without it "the tests passed" names
        # nothing: a concurrent agent can move the tree underneath a running probe, and
        # one agent editing between two gates makes the earlier gate's evidence describe
        # source that no longer exists.
        "tree_sha": tree_fingerprint(cwd),
        # HOW MUCH this gate was looking at. `tree_sha` answers "which tree" and is
        # opaque; this answers "how big was the change", which is what makes a recorded
        # pass auditable after the fact. A review gate that passed over 4,000 changed
        # lines in two minutes is a different claim from one that passed over 12, and
        # without this the log cannot tell them apart.
        "diff_stat": diff_stat(cwd),
    }
    return ("passed" if p.returncode == 0 else "failed"), ev


_SHELL_META = set(";|&<>()$`\n")


def _missing_executable(command: str) -> str:
    """The leading executable of ``command`` if it is simple and absent, else "".

    Returns "" (meaning "no opinion") for anything that is not a bare leading token:
    an assignment prefix, a pipeline, a subshell, a builtin. Being sure only about
    the easy case is the point -- a heuristic that guessed at compound shell would
    produce false UNAVAILABLEs, which stall a pipeline as surely as a false pass
    corrupts one.
    """
    cmd = command.strip()
    if not cmd or cmd[0] in _SHELL_META:
        return ""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return ""
    if not tokens:
        return ""
    head = tokens[0]
    if any(ch in _SHELL_META for ch in head) or "=" in head:
        return ""
    if head in _SHELL_BUILTINS or "/" in head:
        return ""  # builtins have no PATH entry; paths are checked by exec
    return "" if shutil.which(head) else head


#: Builtins that legitimately have no PATH entry. `exit`/`true`/`false` appear in real
#: gate configs and in this project's own tests.
_SHELL_BUILTINS = frozenset(
    {
        "exit",
        "true",
        "false",
        "cd",
        "echo",
        "test",
        "[",
        ":",
        "set",
        "unset",
        "export",
        "eval",
        "source",
        ".",
        "read",
        "wait",
        "trap",
        "shift",
        "return",
    }
)


def _looks_like_not_found(stderr: str) -> bool:
    low = stderr.lower()
    return "not found" in low or "no such file or directory" in low or "permission denied" in low


def record(
    log: EventLog,
    cfg: Config,
    item_id: str,
    gate: str,
    outcome: str,
    *,
    by: str = "",
    reason: str = "",
    evidence: dict[str, Any] | None = None,
    gates: dict[str, GateDef] | None = None,
) -> None:
    """Write a gate outcome to the log, enforcing the evidence contract.

    Rejecting a bare pass at the API boundary is deliberate. If the only thing standing
    between "I ran the tests" and a recorded pass is the agent's honesty, then over a
    long run the record measures honesty rather than testing.
    """
    if outcome not in GATE_OUTCOMES:
        raise ValueError(f"bad outcome {outcome!r}; expected one of {GATE_OUTCOMES}")
    gdef = (gates or {}).get(gate)
    if outcome == "skipped":
        if not cfg.gates.allow_skip_with_reason:
            raise ValueError("skipping is disabled ([gates].allow_skip_with_reason)")
        if not reason:
            raise ValueError("a skip must carry --reason; an unexplained skip is invisible")
    if outcome == "passed" and gdef and gdef.evidence and not evidence:
        raise ValueError(
            f"gate {gate!r} requires evidence to pass (it is in gates.evidence_required). "
            f"Attach the command, its exit code and its output — or record "
            f"`unavailable` with a reason, which is an honest result."
        )
    if outcome in ("unavailable", "partial", "failed") and not reason:
        raise ValueError(f"outcome {outcome!r} must carry a --reason")
    log.append(
        f"gate.{outcome}",
        item_id,
        {"gate": gate, "by": by or log.agent_id, "reason": reason, "evidence": evidence or {}},
    )


def family_of(model: str, cfg: Config) -> str:
    """This project's view of a model's family: `[agent].families`, which defaults to
    the shipped map. ``""`` means "not recognised" — see `config.family_for`."""
    from ..config import family_for

    return family_for(model, cfg.agent.families)


def reviewer_independence(
    state: State, cfg: Config, item_id: str, author_model: str
) -> tuple[bool, str]:
    """Did at least one reviewer come from a different pretraining family?

    Returns (satisfied, explanation). A same-family panel is reported as unsatisfied
    even when every reviewer passed, because agreement among models trained on the same
    distribution measures shared priors, not correctness.
    """
    it = state.items.get(item_id)
    if not it:
        return False, f"no such item {item_id}"
    author_fam = family_of(author_model, cfg)
    fams: list[tuple[str, str]] = []
    anonymous: list[str] = []
    for gname in ("rubber_duck", "critic", "standards"):
        rec = it.gates.get(gname)
        if not rec or rec.outcome not in ("passed", "failed", "partial"):
            continue
        m = str(rec.evidence.get("model", rec.by) or "").strip()
        fam = family_of(m, cfg)
        # An UNIDENTIFIED reviewer cannot establish independence — see `family_of`.
        if not fam:
            anonymous.append(f"{gname}={m or 'no model'}")
            continue
        fams.append((gname, fam))
    if not author_fam:
        return False, (
            f"the author's model {author_model!r} is not in [agent].families, so no "
            f"reviewer can be shown to differ from it. Add it to the map, or pass "
            f"`--model` with a name the map recognises."
        )
    if not fams:
        if anonymous:
            return False, (
                f"no reviewer named a model this project recognises "
                f"({', '.join(anonymous)}), so nothing shows the review came from a "
                f"different family than the author ({author_fam}). Re-record with "
                f"`--model <the reviewer's model>`, or teach [agent].families the name."
            )
        return False, "no reviewer ran at all"
    different = [(g, f) for g, f in fams if f != author_fam]
    if different:
        return True, f"{different[0][0]} was {different[0][1]} vs author {author_fam}"
    return False, (
        f"every identified reviewer ({', '.join(g for g, _ in fams)}) was family "
        f"{author_fam!r}, the same as the author. Same-family agreement is not "
        f"independent evidence."
        + (f" ({', '.join(anonymous)} named no model at all.)" if anonymous else "")
    )
