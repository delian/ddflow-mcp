"""The unit_tests gate's scope (D-gate-economy 1, D-test-economy-r2): the whole suite, or
only the tests the change reaches (`testselect`), and why."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.bookkeeping import STATE_EXCLUDE, is_state
from ..infra import git as GIT
from ..infra import worktree as W
from . import gates as G
from .testselect import Selected, run_command, select


@dataclass
class Scope:
    """What the unit_tests gate runs for one item, and why: the whole suite, or only the
    tests the change reaches. ``command`` is the command to run ("" = the gate's own)."""

    scope: str  #: "selected" | "full"
    why: str
    command: str = ""
    tests: list[Selected] = field(default_factory=list)
    changed_lines: int | None = None
    cut: list[Selected] = field(default_factory=list)  #: over `max_files`, not run
    cut_at: list[str] = field(default_factory=list)  #: hubs the import walk did not pass
    max_files: int = 0
    #: "covered by ci ...": the item's ci gate already ran every test this gate would, on
    #: this very tree, so there is nothing to run (scope is then "covered").
    covered_by: str = ""
    #: The whole suite is what this would run only because the item's ci gate has not passed
    #: on this tree: the gate refuses and says to rerun ci (B9de2a7dc3e).
    needs_ci: bool = False

    def evidence(self) -> dict[str, object]:
        ev: dict[str, object] = {"scope": self.scope, "scope_why": self.why}
        if self.changed_lines is not None:
            ev["changed_lines"] = self.changed_lines
        if self.covered_by:
            ev["covered_by"] = self.covered_by
        if self.scope == "selected":
            ev["selected_tests"] = [{"path": t.path, "reason": t.reason} for t in self.tests]
            ev["max_files"] = self.max_files
            ev["cut_tests"] = [{"path": t.path, "reason": t.reason} for t in self.cut]
            ev["cut_at_hubs"] = self.cut_at
        return ev


def changed_lines(tree: Path, base: str) -> int | None:
    """Added plus removed lines since ``base``'s merge-base: committed, uncommitted and
    untracked, ddflow's own `.ddflow/` bookkeeping excluded. None when git cannot say."""
    mb = W.git(tree, "merge-base", base, "HEAD")
    if mb.code != 0:
        return None
    num = W.git(tree, "diff", "--numstat", "--no-renames", mb.out, "--", ".", *STATE_EXCLUDE)
    if num.code != 0:
        return None
    n = 0
    for row in num.out.splitlines():
        added, removed = [*row.split("\t", 2), "", ""][:2]
        n += int(added) if added.isdigit() else 0
        n += int(removed) if removed.isdigit() else 0
    for p in GIT.files(tree, "untracked") or ():
        if is_state(p):
            continue
        try:
            n += len((tree / p).read_bytes().splitlines())
        except OSError:
            continue
    return n


def limits(cfg) -> dict[str, int]:
    """`select`'s bounds from the project's `[gates]` knobs."""
    return {
        "max_files": cfg.gates.unit_tests_max_files,
        "hub_importers": cfg.gates.unit_tests_hub_importers,
        "max_hops": cfg.gates.unit_tests_max_hops,
        "name_max_tests": cfg.gates.unit_tests_name_max_tests,
    }


def unit_tests_scope(cfg, st, it, command: str, tree: Path) -> Scope:
    """Whether the unit_tests gate runs the selection or the whole suite for item ``it``.

    The selection (D-gate-economy 1) is for a BUG FIX (the task fixes a bug) or a SMALL
    task (fewer changed lines than `[gates].unit_tests_small_lines`), and only once the
    whole suite has PASSED on this very tree: the item's `ci` gate is recorded `passed`
    on the clean commit this tree holds now (ci runs the whole suite on the committed HEAD
    merged with the base). A ci recorded over uncommitted edits, skipped, unavailable,
    failed, run on another commit, or with no tree measured proves nothing about this tree, so the gate runs the whole suite (roborev on
    7da06042: a selection resting on a ci that never passed let a fix merge with the whole
    suite never run). Everything else -- a phase, a promotion, a larger task, a project
    with `[gates].unit_tests_scope = "full"` -- runs the whole suite, and so does a
    selection that cannot be made: no base, git cannot say what changed, no test reaches
    the change (running nothing would pass vacuously), or the command does not run
    pytest -- except after a ci gate whose SELECTED run passed on this tree: that run is the
    evidence (scope "covered"), and the whole suite is never started (B9ec96cb844). Each answer says why. The base is the item's, else the configured base ref,
    else the default branch."""
    if cfg.gates.unit_tests_scope != "selected":
        return Scope("full", f'[gates].unit_tests_scope = "{cfg.gates.unit_tests_scope}"')
    if it.kind == "phase" or it.promote_to:
        what = "phase" if it.kind == "phase" else "promotion"
        return Scope("full", f"a {what} runs the whole suite")
    failed_sel = _ci_failed_selected(it)
    if not failed_sel and (why := _ci_not_passed_here(cfg, it, tree)):
        return Scope("full", why, needs_ci="ci" in G.pipeline_for(it, cfg))
    try:
        base = it.base or cfg.worktree.base_ref or W.default_branch(W.repo_root(tree))
    except W.GitError:
        return Scope("full", "no base to compare the change with")
    lines = changed_lines(tree, base)
    if not failed_sel and (ran := (it.gates["ci"].evidence or {}).get("ci_scope")):
        return _after_ci(cfg, st, it, command, tree, base, lines, ran)
    kind = _selectable(cfg, it, lines)
    if kind.startswith("not "):
        return Scope("full", kind, changed_lines=lines)
    return _selected(cfg, st, it, command, tree, base, kind, lines, ci_failed=failed_sel)


def _ci_failed_selected(it) -> bool:
    """Whether the item's ci gate FAILED on a selected run: the failure is a verdict on the
    tests its change reaches, so this gate runs its own selection and does not widen it to the
    whole suite behind the failure (a second full run for a red selected one). The item still
    cannot complete: its ci gate stays failed."""
    ci = it.gates.get("ci")
    if ci is None or ci.outcome != "failed":
        return False
    return ((ci.evidence or {}).get("ci_scope") or {}).get("scope") == "selected"


def _ci_not_passed_here(cfg, it, tree: Path) -> str:
    """ "" when the item's ci gate PASSED on exactly the commit ``tree`` holds now, clean;
    else why not. `ddflow ci run` tests the committed HEAD merged with the base, never
    uncommitted edits, so a pass counts only when the tree was clean when ci ran and is
    the same clean commit now (roborev on 5ddf11f7: comparing the dirty source let a pass
    on an older commit vouch for edits it never ran)."""
    ci = it.gates.get("ci")
    if ci is None or ci.outcome != "passed":
        state = "has no outcome" if ci is None else f"is {ci.outcome}"
        if "ci" not in G.pipeline_for(it, cfg):
            return "no ci gate in this item's pipeline runs the whole suite, so this gate runs it"
        return (
            f"the ci gate {state}: the whole suite has not passed on this tree, so this "
            "gate will not start it (rerun ci first, and a bug fix or small task then runs the selection)"
        )
    ran = G.normal_fingerprint((ci.evidence or {}).get("tree_sha", ""))
    if not ran:
        return (
            "the ci gate's pass carries no measured tree, so which tree it passed on cannot "
            "be told: rerun ci"
        )
    if not ran.endswith("+clean"):
        return (
            "the ci gate passed on a tree with uncommitted changes, and ci tests only the "
            "committed HEAD: commit, then rerun ci"
        )
    if ran != G.normal_fingerprint(G.tree_identity(tree)):
        return "the tree changed since the ci gate passed (another commit, or uncommitted edits): rerun ci"
    return ""


def _selectable(cfg, it, lines: int | None) -> str:
    """Why ``it`` may run the selection ("a bug fix (...)", "a small task (...)"), or,
    starting "not ", why it may not."""
    small = cfg.gates.unit_tests_small_lines
    if it.fixes:
        return f"a bug fix ({', '.join(it.fixes)})"
    if lines is not None and small > 0 and lines < small:
        return f"a small task ({lines} changed lines < [gates].unit_tests_small_lines = {small})"
    size = "its size is unknown" if lines is None else f"{lines} changed lines"
    return f"not a bug fix, and {size} (small is under {small})"


def _selected(
    cfg, st, it, command: str, tree: Path, base: str, kind: str, lines: int | None,
    ci_failed: bool = False,
) -> Scope:  # fmt: skip
    """The selection for a selectable item, or the whole suite when none can be made."""
    sel = select(tree, base, **limits(cfg))
    if sel is None:
        return Scope(
            "full", f"{kind}, but git could not say what changed since {base}", changed_lines=lines
        )
    if sel.full:
        return Scope(
            "full",
            f"{kind}, but {sel.full}: only the whole suite keeps recall",
            changed_lines=lines,
        )
    picked = {s.path for s in sel.tests}
    # Only what pytest runs: a `scripts/check.sh` regression check is run by no command
    # built here, and listing it would claim a test that never ran.
    regression = sorted(_regression(st, it) - picked)
    tests = [
        *sel.tests,
        *(Selected(t, "a regression test of the bug it fixes") for t in regression),
    ]
    if not tests:
        return Scope("full", f"{kind}, but no test reaches its change", changed_lines=lines)
    cmd = run_command(command, [t.path for t in tests], tree)
    if not cmd:
        return Scope(
            "full", f"{kind}, but the unit_tests command does not run pytest", changed_lines=lines
        )
    return Scope(
        "selected",
        f"{kind}: {len(tests)} test(s) its change reaches"
        + (
            f" ({len(sel.cut)} more cut at the {cfg.gates.unit_tests_max_files}-file budget)"
            if sel.cut
            else ""
        )
        + (
            "; the ci gate failed on its selected scope (it must pass before the item completes), "
            "so this gate does not widen it to the whole suite"
            if ci_failed
            else "; the ci gate passed the whole suite on this tree"
        ),
        command=cmd,
        tests=tests,
        changed_lines=lines,
        cut=sel.cut,
        cut_at=sel.cut_at,
        max_files=cfg.gates.unit_tests_max_files,
    )


def _after_ci(cfg, st, it, command: str, tree: Path, base: str, lines, ran: dict) -> Scope:
    """The scope when the item's ci gate passed HERE and recorded which tests it took
    (D-test-economy-r2): nothing when ci's scope is this gate's or wider, else only the
    difference -- the tests this gate's selection holds that ci did not run."""
    sha = str(ran.get("sha", ""))[:12]
    if ran.get("scope") == "full":
        return Scope(
            "covered", "the ci gate ran the whole suite on this tree", changed_lines=lines,
            covered_by=f"ci full run {sha}",
        )  # fmt: skip
    covered = f"ci selected run {sha}"
    sel = select(tree, base, **limits(cfg))
    if sel is None or sel.full:
        why = "git could not say what changed" if sel is None else sel.full
        # ci passed its selected run on this very tree: that run is the evidence. The whole
        # suite is never started from here (B9ec96cb844, D-agent-test-etiquette).
        return Scope(
            "covered", f"{why}; the ci gate's selected run passed on this tree and is the evidence",
            changed_lines=lines, covered_by=covered,
        )  # fmt: skip
    done = set(ran.get("tests") or ())
    if int(ran.get("selected") or 0) > len(done):  # a truncated list proves nothing about the rest
        return Scope(
            "covered", "the ci gate's test list is cut; its selected run passed on this tree",
            changed_lines=lines, covered_by=covered,
        )  # fmt: skip
    done |= set(ran.get("always_tests") or ())  # the always-run guards ran as well
    rest = _not_run(sel.tests, _regression(st, it), done)
    if not rest:
        return Scope(
            "covered", f"every test this change reaches ran in the ci gate ({len(done)} files)",
            changed_lines=lines, covered_by=covered,
        )  # fmt: skip
    cmd = run_command(command, [t.path for t in rest], tree)
    if not cmd:
        return Scope(
            "covered", "the unit_tests command does not run pytest; ci's selected run stands",
            changed_lines=lines, covered_by=covered,
        )  # fmt: skip
    return Scope(
        "selected", f"{len(rest)} test(s) the ci selected run {sha} did not take",
        command=cmd, tests=rest, changed_lines=lines, cut=sel.cut, cut_at=sel.cut_at,
        max_files=cfg.gates.unit_tests_max_files,
    )  # fmt: skip


def _regression(st, it) -> set[str]:
    """What pytest runs of the regression tests of the bugs ``it`` fixes."""
    return {
        t
        for b in it.fixes
        if (bug := st.bugs.get(b)) is not None
        for t in bug.regression_tests
        if t.endswith(".py") or "::" in t
    }


def _not_run(tests: list[Selected], regression: set[str], done: set[str]) -> list[Selected]:
    """The selected tests and regression tests that are not in ``done``."""
    rest = [t for t in tests if t.path not in done]
    seen = {t.path for t in rest}
    extra = sorted(regression - done - seen)
    return [*rest, *(Selected(t, "a regression test of the bug it fixes") for t in extra)]
