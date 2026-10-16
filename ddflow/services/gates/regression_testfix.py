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
from typing import Any, NamedTuple

from ...config import Config
from ...core import globs as GL
from ...infra import fsio
from .. import changes as CH
from .defs import GateDef
from .regression_collect import tree_provides
from .runner import run_command_gate

#: What `verify_regression_test` returns. `verified` is the only plain pass; the caller
#: REFUSES `passed-on-prefix`/`failed-on-fix` and records `could-not-run` as the gap it is.
REGRESSION_VERIFIED = "verified"
REGRESSION_PASSES_ON_PREFIX = "passed-on-prefix"
REGRESSION_FAILS_ON_FIX = "failed-on-fix"
REGRESSION_COULD_NOT_RUN = "could-not-run"

#: The status a close made by a mutation records (`regression_verified`).
VERIFIED_BY_MUTATION = "verified-by-mutation"

#: pytest exit codes that mean it ran no test: usage error (4), nothing collected (5).
_NOT_A_TEST_FAILURE = (4, 5)

_KEEP = ("exit", "elapsed_s", "summary", "tail", "output_digest")


class Tools(NamedTuple):
    """What the caller (`mutation.verify_regression_test`) hands over: `ci` and
    `testselect` import this package back, so they are passed in, not imported here."""

    merge_tree: Any  # ci.merge_tree(repo, ref, base) -> context manager of (path, why)
    command_for: Any  # testselect.run_command(configured, tests, root) -> command


def _is_test_file(path: str, test_globs: list[str]) -> bool:
    return any(GL.match(path, g, bare_any_depth=True) for g in test_globs)


def _ran(ev: dict[str, Any]) -> dict[str, Any]:
    return {k: ev[k] for k in _KEEP if k in ev}


def verify_test_only_fix(
    repo: Path,
    cfg: Config,
    *,
    tree: Path,
    base_sha: str,
    tests: list[str],
    defn: GateDef | None,
    mutation: dict[str, str] | None,
    tools: Tools,
) -> tuple[str, dict[str, Any]] | None:
    """The verdict for a fix confined to test files whose named test passed on the
    overlaid pre-fix tree; None when the fix is NOT confined to test files (the caller's
    'passes on the pre-fix tree' refusal stands). Statuses are those of
    `verify_regression_test`, plus VERIFIED_BY_MUTATION."""
    UNRUN, PASSES, VERIFIED = (
        REGRESSION_COULD_NOT_RUN,
        REGRESSION_PASSES_ON_PREFIX,
        REGRESSION_VERIFIED,
    )
    changed = CH.changed_since(tree, base_sha)
    globs = list(cfg.lessons.test_file_globs)
    if not changed or not all(_is_test_file(p, globs) for p in changed):
        return None
    ev: dict[str, Any] = {"test_only_fix": True, "changed_files": changed}

    restored = "not run"
    with tools.merge_tree(repo, base_sha, "") as (ptree, why):
        if ptree is None:
            return UNRUN, {**ev, "reason": why}
        if not tree_provides(tools.command_for, defn.command if defn else "", ptree, tests):
            restored = "not run (the test is not in the base's test files)"
        else:
            command = tools.command_for(defn.command if defn else "", tests, ptree)
            if not command:
                restored = "not run (no runnable pytest command)"
            else:
                run = GateDef(id="regression_check", title="regression test", command=command)
                outcome, rev = run_command_gate(run, ptree)
                restored = outcome
                if outcome == "unavailable":
                    restored = f"unavailable ({rev.get('reason', 'no reason given')})"
                # Exit 4/5 is pytest finding nothing to run (usage error, no tests
                # collected): not a failure OF THE TEST, so not evidence it fails.
                if outcome == "failed" and rev.get("exit") in _NOT_A_TEST_FAILURE:
                    restored = f"not run (pytest exited {rev.get('exit')}: no test collected)"
                if outcome == "failed" and rev.get("exit") not in _NOT_A_TEST_FAILURE:
                    return VERIFIED, {
                        **ev,
                        "verified_by": "pre-fix-test-files",
                        "prefix_tests_restored": True,
                        "restored_outcome": outcome,
                        "restored_run": _ran(rev),
                    }

    ev["restored_outcome"] = restored
    if not mutation:
        return PASSES, {
            **ev,
            "reason": "the named test PASSES on the pre-fix tree, and the fix is confined to "
            f"test files ({', '.join(changed)}), so the pre-fix tree has no old behaviour to "
            f"fail on; run with the pre-fix test files restored: {restored}. Show it "
            "guards the fix with a mutation (--mutation-file/--mutation-old/--mutation-new: an "
            "edit to a changed test file that disables the fix, which must make it FAIL), "
            "or close with --skip-regression-verify --verify-reason (recorded)",
        }
    return _by_mutation(repo, cfg, tree, base_sha, tests, defn, changed, mutation, ev, tools)


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
    tools: Tools,
) -> tuple[str, dict[str, Any]]:
    UNRUN, PASSES = REGRESSION_COULD_NOT_RUN, REGRESSION_PASSES_ON_PREFIX

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
    with tools.merge_tree(repo, base_sha, "") as (ptree, why):
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
        if not target.is_file():
            return PASSES, {
                **ev,
                "reason": f"{rel} does not exist in the fixed tree (the fix deletes or "
                "renames it), so the mutation cannot be applied",
            }
        text = target.read_text("utf-8")
        if text.count(old) != 1:
            return PASSES, {
                **ev,
                "reason": f"`old` appears {text.count(old)} time(s) in {rel}; the mutation "
                "must be unambiguous (exactly once)",
            }
        fsio.replace_text(target, text.replace(old, new, 1), fsync=False)
        command = tools.command_for(defn.command if defn else "", tests, ptree)
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
    if outcome == "failed" and rev.get("exit") in _NOT_A_TEST_FAILURE:
        return PASSES, {
            **ev,
            "reason": f"the mutated tree ran no test (pytest exited {rev.get('exit')}): a "
            "mutation that removes or renames the test does not show it guards the fix",
        }
    if outcome == "passed":
        return PASSES, {
            **ev,
            "reason": "the named test still PASSES with the fix disabled by the supplied "
            "mutation, so it does not guard the fix",
        }
    return VERIFIED_BY_MUTATION, ev
