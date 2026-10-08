"""Whether an item may be completed — the rule-set, callable without argv.

These rules were the body of `cli.cmd_complete`: required gates, open descendants,
silence under `require_outcome`, inert requirements, unavailable gates, reviewer
independence. Real domain policy, reachable only through `main(argv)` — so the only way
to ask "would this complete, and why not" was to run the CLI and read its stderr, and
the only way to test a rule was to drive a subprocess.

Policy belongs where it can be called. `verdict()` answers the question and writes
nothing; the surfaces decide what to print, which exit code to use, and whether
`--force` overrides. That split is also what lets `--force` be honest: the blockers are
computed either way and recorded on the completion event, so a forced completion says
in the log exactly what it overrode.

The one thing deliberately NOT a blocker is stale evidence. A gate that passed on a
different tree still passed; what is no longer true is that it passed on *this* tree.
Refusing on a change that might be a comment is how a check gets switched off, so it
comes back as a `warning` and the surfaces show it before the verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..core.model import State
from ..core.schedule import is_shared
from . import gates as G


@dataclass
class Verdict:
    """Everything the decision depends on, computed once.

    `blockers` is the complete list, never the first one found: reporting one at a time
    turns a single refusal into a round-trip per problem, and an agent that cannot see
    how many more are coming reaches for `--force`. A refusal is only actionable if it
    is complete.
    """

    item: str
    blockers: list[str] = field(default_factory=list)
    #: Pre-decision WARNINGS, addressed to whoever is about to complete: a gate whose
    #: evidence describes a tree that has since moved. Shown before the verdict, and on
    #: stderr, because they are about the decision rather than part of its result.
    warnings: list[str] = field(default_factory=list)
    #: Part of the RESULT, not a warning about it: the coverage gap, which is recorded
    #: on the completion event and must reach both surfaces. Kept separate from
    #: `warnings` because merging them put it on stderr, where the one surface that
    #: needed it -- an agent reading JSON -- could not see it. That is the exact bug
    #: this field's comment in `cmd_complete` was written about.
    coverage_note: str = ""
    #: Gates in the pipeline that could not run. Recorded on the event as a coverage
    #: gap, so a completion never silently implies they passed.
    coverage_gaps: list[str] = field(default_factory=list)
    independence: str = ""
    kind: str = "task"

    @property
    def may_complete(self) -> bool:
        return not self.blockers


#: How the open-bug blocker begins; `api.complete` lifts exactly this one when
#: `--regression-test` is about to close those bugs.
OPEN_BUG_BLOCKER = "fixes open bug(s) "


def fixes_of(state: State, item_id: str, cfg: Config | None = None) -> set[str]:
    """The bugs ``item_id`` was filed to fix: its `fixes` list, plus the bug a hand-filed
    fix task names (`fix-<bug>`, read back through the `[ids].fix_task` template by the
    id service, never by a prefix written here). NOT every bug whose `fix_task` points
    here: `bug found --item <open fix task>` links a mere report to the task it was filed
    against, and completing fix-B297ede2447 closed four such reports nobody had fixed
    (B7bdcc6b212)."""
    from ..core import ids as IDS

    it = state.items.get(item_id)
    named = set(it.fixes) if it is not None else set()
    named.update(IDS.bugs_named_by_fix_task(cfg, item_id))
    return {b for b in named if b in state.bugs}


def open_bugs_of(state: State, item_id: str, cfg: Config | None = None) -> list[str]:
    """The open bugs ``item_id`` was filed to fix (`fixes_of`), sorted: what its completion
    must close, and what `--regression-test` closes."""
    return sorted(b for b in fixes_of(state, item_id, cfg) if state.bugs[b].open)


def reported_against(state: State, item_id: str, cfg: Config | None = None) -> list[str]:
    """Open bugs linked to ``item_id`` (`fix_task`) that it was NOT filed to fix: its
    completion leaves them open, and says so."""
    mine = fixes_of(state, item_id, cfg)
    return sorted(
        b.id for b in state.bugs.values() if b.open and b.fix_task == item_id and b.id not in mine
    )


def _open_bug_blockers(state: State, item_id: str, cfg: Config | None = None) -> list[str]:
    """The bugs ``item_id`` is the fix task of that are still open, as one blocker."""
    fixing = open_bugs_of(state, item_id, cfg)
    if not fixing:
        return []
    return [
        f"{OPEN_BUG_BLOCKER}{', '.join(fixing)}: close each with `ddflow bug fixed <bug> "
        f"--regression-test <test>` (a test that FAILS on the unfixed code), or pass "
        f"`--regression-test <test>` to complete, which closes them; a false finding is "
        f"`ddflow bug invalid <bug> --reason ...`."
    ]


def _reported_bug_warnings(state: State, cfg: Config, item_id: str) -> list[str]:
    """The bugs reported against ``item_id`` that its completion leaves open, as one
    warning (`reported_against`)."""
    left = reported_against(state, item_id, cfg)
    if not left:
        return []
    then = (
        "files each a fix task of its own"
        if cfg.bugs.file_task
        else "files no fix task for them (`[bugs] file_task = false`): each needs one"
    )
    return [
        f"open bug(s) {', '.join(left)} were reported against {item_id} but are not what "
        f"it was filed to fix: completing it leaves them open and {then}."
    ]


def verdict(state: State, cfg: Config, item_id: str, *, repo: Path, model: str = "") -> Verdict:
    """Why this item may or may not complete. Reads only; writes nothing."""
    it = state.items.get(item_id)
    if it is None or it.removed:
        return Verdict(item=item_id, blockers=[f"no such item {item_id!r}"])

    s = G.status(state, cfg, item_id)
    v = Verdict(item=item_id, coverage_gaps=list(s.unavailable), kind=it.kind)
    required = set(cfg.gates.required)

    missing = [g for g in s.pipeline if g in required and not it.gate_satisfied(g, True)]
    if missing:
        v.blockers.append(
            f"required gate(s) not passed: {', '.join(missing)} (current outcome: "
            + ", ".join(f"{g}={it.gate_outcome(g) or 'not run'}" for g in missing)
            + ")"
        )

    # ANY item with work beneath it, not only a phase: a task split into sub-tasks is
    # an umbrella too, and completing it while its children are open marks work
    # finished that nobody has done. `abandoned` counts as settled alongside `done`, or
    # an item you decided against holds its parent open forever.
    open_children = state.open_descendants(item_id)
    if open_children:
        noun = "task(s) in this phase" if it.kind == "phase" else "sub-task(s)"
        v.blockers.append(
            f"{len(open_children)} {noun} unfinished: {', '.join(k.id for k in open_children[:8])}"
        )

    # Silence is not a pass. Without this an agent could complete having recorded
    # implement/unit_tests/merge and never touched research, the rubber-duck, the
    # critic, the standards pass, the bug hunt or the dedupe check — six of ten steps
    # omitted with no trace. An explicit `gate skip --reason` is still an outcome, so
    # the escape hatch is the auditable one rather than the invisible one.
    if cfg.gates.require_outcome and s.silent:
        gdefs = G.load_gates(repo, cfg)
        human = [g for g in s.silent if g in gdefs and gdefs[g].is_human_gate]
        other = [g for g in s.silent if g not in human]
        if other:
            v.blockers.append(
                f"gate(s) never run and never skipped: {', '.join(other)}. "
                f"Record an outcome (`ddflow gate run|record`) or skip it on the record "
                f"(`ddflow gate skip <id> <gate> --reason ...`); "
                f"set [gates].require_outcome = false to make the pipeline advisory."
            )
        if human:
            # Named separately, because all three commands the sentence above suggests
            # REFUSE a human gate. Telling an agent to run them is telling it to
            # collect an exit 3 and conclude something is broken.
            v.blockers.append(
                f"awaiting a person: {', '.join(human)}. Ask the operator to run "
                f"`ddflow approve {item_id} {human[0]}` (or `--reject --reason ...`). "
                f"You cannot record this one — `gate record` and `gate skip` both "
                f"refuse it, and there is no MCP tool for it."
            )

    # A fix task closes its bug or does not complete (B-bugs-as-items): the bug record is
    # what carries the regression test, and `bug fixed` is what refuses to close without
    # one. Asked of the task's `fixes` (B7bdcc6b212), not of every bug whose `fix_task`
    # names it: a report filed against the task is not what the task fixes, so it neither
    # blocks the completion nor is closed by it -- it is named as left open.
    v.blockers += _open_bug_blockers(state, item_id, cfg)
    v.warnings += _reported_bug_warnings(state, cfg, item_id)

    if it.kind == "phase":
        # Periodic passes counted in phases (architecture review, mutation tests, lessons)
        # are the ones a phase close exists to run; task-counted ones stay advisory. Here,
        # not in `api.complete`, so the PR-merge settle path enforces it too.
        from .cadence import phase_overdue

        v.blockers += phase_overdue(state, cfg)

    inert = G.inert_requirements(cfg)
    if inert:
        v.blockers.append(
            f"[gates].required names {', '.join(inert)}, which no pipeline runs — "
            f"so that requirement enforces nothing. Add it to a pipeline (task_pipeline, "
            f"phase_pipeline, or promotion_pipeline with flow.environments set), or drop "
            f"it from required."
        )

    if cfg.gates.unavailable_is_failure and s.unavailable:
        v.blockers.append(
            f"gate(s) could not run: {', '.join(s.unavailable)} "
            f"([gates].unavailable_is_failure is on, so a gap blocks like a failure)"
        )

    # The project's own reviewer gates count, as their definitions declare (B0e1330bf74).
    ok, why = G.reviewer_independence(state, cfg, item_id, model, G.load_gates(repo, cfg))
    v.independence = why
    # Not for a PROMOTION: it authors nothing -- it moves work that was reviewed, with this
    # very check, as the tasks that produced it. Demanding an independent reviewer of a
    # merge between environment branches blocked every promotion, since its pipeline
    # (rightly) has no review gate to satisfy it with.
    if cfg.agent.reviewer_family_must_differ and not ok and it.kind == "task" and not it.promote_to:
        v.blockers.append(f"reviewer independence not satisfied: {why}")

    if s.unavailable:
        v.coverage_note = _coverage_note(it, s.unavailable)

    readme = readme_report(state, cfg, item_id, repo=repo)
    if readme:
        # "Could not run" is never a blocker: only a README that is known to be missing is.
        blocks = cfg.enforce.readme_with_code == "block" and readme != README_UNKNOWN
        (v.blockers if blocks else v.warnings).append(readme)

    cwd, landed = _tree_being_completed(repo, it)
    for note in G.stale_evidence_detail(state, cfg, item_id, cwd, landed=landed):
        if note.unverified:
            v.warnings.append(
                f"{note.gate} passed, but whether on the tree you are completing could "
                f"not be checked — {note.why}. Re-run it if that matters."
            )
            continue
        v.warnings.append(
            f"{note.gate} passed on a different tree than the one you are completing — "
            f"{note.why}. Re-run it if the change was not cosmetic."
        )
    return v


README_UNKNOWN = (
    "README check could not run: git could not report this task's diff (no worktree, "
    "branch or landed commit to read, or its base is unreadable), so whether the README "
    "moved is unknown."
)
README_REMEDY = (
    "README not updated: record the section you changed, or "
    "`ddflow gate skip <id> docs --reason ...`"
)


def readme_report(state: State, cfg: Config, item_id: str, *, repo: Path) -> str:
    """The "README not updated" report when a task's diff changed code and not the README;
    `README_UNKNOWN` when git cannot say what changed; else "".

    Decision D-readme-current: a change a user or agent can see updates README.md in the
    same task. A completion CHECK rather than a pipeline gate: a gate in `task_pipeline`
    would be demanded of every task (test-only, docs-only, housekeeping) and its only
    evidence would be an assertion, while whether the README moved is a fact about the
    diff. The recorded reason is the existing `docs` gate outcome -- `gate skip <id> docs
    --reason` or `gate record <id> docs --outcome passed --evidence <section>`.

    Silent when the mode is `off`, the item is not a task, or a `docs` outcome with a
    reason is on record. When git cannot say what changed it returns `README_UNKNOWN`
    (the caller makes that a warning, never a blocker): "nobody looked" is not "no".
    """
    mode = cfg.enforce.readme_with_code
    it = state.items.get(item_id)
    if mode == "off" or it is None or it.removed or it.kind != "task" or it.promote_to:
        return ""
    docs = it.gates.get("docs")
    if docs and (
        (docs.outcome == "skipped" and docs.reason)
        or (docs.outcome == "passed" and (docs.evidence or {}).get("note"))
    ):
        return ""
    changed = changed_paths(repo, it)
    if changed is None:
        return README_UNKNOWN
    code = [
        p
        for p in changed
        if is_shared(p, cfg.enforce.readme_code_globs) and not _is_test_or_doc_path(p)
    ]
    if not code or any(_is_readme(p, cfg.enforce.readme_files) for p in changed):
        return ""
    more = f" (+{len(code) - 3} more)" if len(code) > 3 else ""  # noqa: PLR2004
    return f"{README_REMEDY}. Changed: {', '.join(code[:3])}{more}."


def _is_readme(path: str, files: list[str]) -> bool:
    """Anchored at the root: git's slashless pattern matches at any depth, which made
    `docs/README.md` count as the project's README."""
    return is_shared(path, [f if f.startswith("/") else f"/{f}" for f in files])


_DOC_SUFFIXES = (".md", ".rst", ".adoc", ".txt")
_TEST_DOC_DIRS = ("tests", "test", "__tests__", "spec", "specs", "docs", "doc")


def _is_test_or_doc_path(path: str) -> bool:
    """Never user-visible code, even inside a code glob: a test file (`pkg/tests/x.py`,
    `__tests__/`, `foo.test.ts`, `foo_spec.rb`, conftest.py) or documentation (a docs/ or
    doc/ directory, or a .md/.rst/.adoc/.txt file)."""
    parts = path.split("/")
    name = parts[-1]
    stem = name.rsplit(".", 1)[0]
    return (
        any(d.lower() in _TEST_DOC_DIRS or d.endswith("_tests") for d in parts[:-1])
        or name.startswith("test_")
        or stem.endswith(("_test", "_spec", "_tests"))
        or re.search(r"[a-z0-9](Test|Tests|Spec)$", stem) is not None  # FooTest.java, widgetSpec.js
        or ".test." in name
        or ".spec." in name
        or name == "conftest.py"
        or name.lower().endswith(_DOC_SUFFIXES)
    )


def changed_paths(repo: Path, it) -> list[str] | None:
    """The paths the item changed: what landed (`landed_before..landed_after`), or before
    it lands its worktree (or, with none, its branch) against its base. None when git cannot say -- never an empty \"nothing changed\"."""
    from ..infra import worktree as W
    from . import testselect as TS

    if it.landed_before and it.landed_after:
        out = W.git_paths(
            repo, "diff", "--name-only", "--no-renames", it.landed_before, it.landed_after
        )
        return None if out is None else sorted(out)
    base = it.base or W.default_branch(repo)
    path = W.load_path(repo, it.worktree) if it.worktree else None
    if path and path.is_dir():
        return TS.changed_files(path, base)
    # Claimed --no-worktree: the work is a branch in a tree that is not ours to read.
    if it.branch and W.rev(repo, it.branch):
        out = W.git_paths(repo, "diff", "--name-only", "--no-renames", f"{base}...{it.branch}")
        # Empty is not "nothing changed" here: a branch already merged into its base, or
        # the base itself, diffs to nothing whatever the task did.
        return sorted(out) if out else None
    return None


def _tree_being_completed(repo: Path, it) -> tuple[Path, str]:
    """(where to look, the landed commit or "") for the stale-evidence check.

    Once the item has landed, the commit that landed -- even when its worktree was kept,
    since what is completed is what landed, not what the tree holds now -- and NOT the
    primary checkout, whose HEAD is the target branch and whose files
    are everyone's (bugs Bd86b05a8f8, Ba84119f707, B613cb67194). Of a merge commit, its
    second parent: the branch head that was merged, which is what the gates ran on;
    the merge commit itself also holds whatever the target gained meanwhile, and that
    is not a reason to distrust the item's gates. A fast-forward or squash lands one
    parent, and is itself the branch's content. Before it lands, the item's worktree.

    `merged_sha` is only the fallback for an event without `landed_after`: `pr.merged`
    with no merge sha, which records the branch's HEAD -- so it is taken as it is, with
    no second-parent rule (that rule needs `landed_before` to tell OUR merge commit from
    a branch head that is itself a merge). Every `worktree.merged` in this project's
    log carries `landed_after`.
    """
    from ..infra import worktree as W

    for ref in (it.landed_after, it.merged_sha):
        sha = W.rev(repo, ref) if ref else ""
        if not sha:
            continue
        parents = W.git(repo, "rev-list", "--parents", "-n", "1", sha).out.split()
        # OUR merge commit only: its first parent is the target before it. A branch
        # head that is itself a merge (main merged into it), fast-forwarded, is not.
        # The forge path records `landed_before` as the landed commit's own first
        # parent, so there a fast-forward is told apart by the PR's head instead.
        head = W.rev(repo, it.pr.head_sha) if it.pr and it.pr.head_sha else ""
        ours = (
            ref == it.landed_after
            and parents[1:2] == [W.rev(repo, it.landed_before)]
            and sha != head
        )
        if ours and len(parents) > 2:  # noqa: PLR2004 -- self + 2 parents
            return repo, parents[2]
        return repo, sha
    path = W.load_path(repo, it.worktree) if it.worktree else None
    return path or repo, ""


def _coverage_note(it, gaps: list[str]) -> str:
    """Say what each gap IS. `GateStatus.unavailable` holds `partial` gates as well, and
    one sentence for both called a critic that reviewed 3 of 6 chunks one that "never
    ran" -- erasing the half it did (B72dd4dde17). A reviewer's partial evidence carries
    its `coverage`; a `partial_exits` gate has none, and says "coverage unknown"."""
    never = [g for g in gaps if it.gate_outcome(g) != "partial"]
    parts = [f"{', '.join(never)} never ran"] if never else []
    for g in gaps:
        if it.gate_outcome(g) == "partial":
            # `is None`/`== ""`, not falsiness: a coverage of 0 is a figure, and the
            # most important one to show.
            coverage = it.gates[g].evidence.get("coverage")
            figure = " unknown" if coverage is None or coverage == "" else f": {coverage}"
            parts.append(f"{g} ran only partially (coverage{figure})")
    return "; ".join(parts) + " — recorded as a coverage gap, not as a pass."
