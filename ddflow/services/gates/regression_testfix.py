"""`bug fixed` for a fix that lives in the TEST FILES themselves (bug Bc262739f62).

The regression check overlays the new test file on the item's base and expects the test to
fail there. When the fix IS a change to the test file (a fixture, a conftest), the overlay
already carries the fix, so the test passes "on the pre-fix tree" and the close is refused,
leaving only `--skip-regression-verify`. Two honest ways to show the test guards the fix:

1. the named test, run with the PRE-FIX test files (the base's own), fails -- meaningful
   only when the test already exists there;
2. the agent supplies a MUTATION -- `file`, `old`, `new`: an edit to a changed test file
   that disables the fix -- and ddflow applies it to a scratch copy of the fixed tree and
   watches the test fail. The evidence is recorded in a structured, reviewable form, and
   ddflow ran it: the agent's claim is never taken on trust.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ...config import Config
from ...core import globs as GL
from ...infra import worktree as W
from .defs import GateDef
from .runner import run_command_gate

#: The status a close made by a mutation records (`regression_verified`).
VERIFIED_BY_MUTATION = "verified-by-mutation"

_KEEP = ("exit", "elapsed_s", "summary", "tail", "output_digest")


def changed_files(tree: Path, base_sha: str) -> list[str] | None:
    """Paths the tree changes against ``base_sha`` (committed, uncommitted and untracked),
    or None when git cannot say -- a diff nobody could read is not 'confined to tests'."""
    diff = W.git(tree, "diff", "--name-only", "--no-renames", base_sha)
    if not diff.ok:
        return None
    names = {n for n in diff.out.splitlines() if n.strip()}
    names.update(W.untracked_files(tree))
    return sorted(names)


def is_test_file(path: str, test_globs: list[str]) -> bool:
    return any(GL.match(path, g, bare_any_depth=True) for g in test_globs)


def _ran(ev: dict[str, Any]) -> dict[str, Any]:
    return {k: ev[k] for k in _KEEP if k in ev}


def _exists_at_base(ptree: Path, tests: list[str]) -> bool:
    """Do the named tests exist in the base's own test files? Only then does running
    them there say anything: an absent test 'fails' by not being collected."""
    for spec in tests:
        rel, _, rest = spec.partition("::")
        src = ptree / rel
        if not src.is_file():
            return False
        name = rest.split("::")[-1].split("[")[0]
        if name and name not in src.read_text("utf-8", errors="replace"):
            return False
    return True


def _command_for(defn: GateDef | None, tests: list[str], root: Path) -> str:
    from .. import testselect as TS  # function-level: testselect imports gates back

    return TS.run_command(defn.command if defn else "", tests, root)


def verify_test_only_fix(
    repo: Path,
    cfg: Config,
    *,
    tree: Path,
    base_sha: str,
    tests: list[str],
    defn: GateDef | None,
    mutation: dict[str, str] | None,
) -> tuple[str, dict[str, Any]] | None:
    """The verdict for a fix confined to test files whose named test passed on the
    overlaid pre-fix tree; None when the fix is NOT confined to test files (the caller's
    'passes on the pre-fix tree' refusal stands). Statuses are those of
    `verify_regression_test`, plus VERIFIED_BY_MUTATION."""
    from .. import ci as CI
    from .mutation import REGRESSION_COULD_NOT_RUN as UNRUN
    from .mutation import REGRESSION_PASSES_ON_PREFIX as PASSES
    from .mutation import REGRESSION_VERIFIED as VERIFIED

    changed = changed_files(tree, base_sha)
    globs = list(cfg.lessons.test_file_globs)
    if not changed or not all(is_test_file(p, globs) for p in changed):
        return None
    ev: dict[str, Any] = {"test_only_fix": True, "changed_files": changed}

    with CI.merge_tree(repo, base_sha, "") as (ptree, why):
        if ptree is None:
            return UNRUN, {**ev, "reason": why}
        if _exists_at_base(ptree, tests):
            command = _command_for(defn, tests, ptree)
            if command:
                run = GateDef(id="regression_check", title="regression test", command=command)
                outcome, rev = run_command_gate(run, ptree)
                if outcome == "failed":
                    return VERIFIED, {
                        **ev,
                        "verified_by": "pre-fix-test-files",
                        "prefix_tests_restored": True,
                        "restored_outcome": outcome,
                        "restored_run": _ran(rev),
                    }

    if not mutation:
        return PASSES, {
            **ev,
            "reason": "the named test PASSES on the pre-fix tree, and the fix is confined to "
            f"test files ({', '.join(changed)}), so the pre-fix tree has no old behaviour to "
            "fail on; the test also passes with the pre-fix test files restored. Show it "
            "guards the fix with a mutation (--mutation-file/--mutation-old/--mutation-new: an "
            "edit to a changed test file that disables the fix, which must make it FAIL), "
            "or close with --skip-regression-verify --verify-reason (recorded)",
        }
    return _by_mutation(repo, cfg, tree, base_sha, tests, defn, changed, mutation, ev)


def _by_mutation(
    repo: Path,
    cfg: Config,
    tree: Path,
    base_sha: str,
    tests: list[str],
    defn: GateDef | None,
    changed: list[str],
    mutation: dict[str, str],
    ev: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    from .. import ci as CI
    from .mutation import REGRESSION_COULD_NOT_RUN as UNRUN
    from .mutation import REGRESSION_PASSES_ON_PREFIX as PASSES

    # A mutation that cannot be applied is the agent's mistake, not the environment's:
    # refused (PASSES), never a close with a recorded gap.
    rel, old, new = (mutation.get(k, "") for k in ("file", "old", "new"))
    ev = {**ev, "verified_by": "mutation", "mutation": {"file": rel, "old": old, "new": new}}
    if rel not in changed:
        return PASSES, {
            **ev,
            "reason": f"the mutation edits {rel!r}, which the fix does not change "
            f"(changed: {', '.join(changed)}); a mutation outside the fix proves nothing "
            "about it",
        }
    if not old:
        return PASSES, {**ev, "reason": "the mutation names no `old` text to replace"}
    with CI.merge_tree(repo, base_sha, "") as (ptree, why):
        if ptree is None:
            return UNRUN, {**ev, "reason": why}
        # The fixed tree, as a scratch copy: the base plus every file the fix changes.
        for path in changed:
            src, dst = tree / path, ptree / path
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
            else:
                dst.unlink(missing_ok=True)
        target = ptree / rel
        text = target.read_text("utf-8")
        if text.count(old) != 1:
            return PASSES, {
                **ev,
                "reason": f"`old` appears {text.count(old)} time(s) in {rel}; the mutation "
                "must be unambiguous (exactly once)",
            }
        target.write_text(text.replace(old, new, 1), "utf-8")
        command = _command_for(defn, tests, ptree)
        if not command:
            return UNRUN, {**ev, "reason": "the mutated tree has no runnable pytest command"}
        run = GateDef(id="regression_check", title="regression test", command=command)
        outcome, rev = run_command_gate(run, ptree)
    ev = {**ev, "mutated_command": command, "mutated_outcome": outcome, "mutated_run": _ran(rev)}
    if outcome == "unavailable":
        return UNRUN, {
            **ev,
            "reason": f"the mutated tree could not run the test: {rev.get('reason', '')}",
        }
    if outcome == "passed":
        return PASSES, {
            **ev,
            "reason": "the named test still PASSES with the fix disabled by the supplied "
            "mutation, so it does not guard the fix",
        }
    return VERIFIED_BY_MUTATION, ev
