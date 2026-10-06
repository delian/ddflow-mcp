"""Proving a check CAN fail: gate mutation verification and the regression-test check."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import Config
from ...core.model import Item, State
from .defs import GateDef, load_gates
from .runner import run_command_gate


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
    if gdef.is_human_gate:
        # Named for what it IS. Calling it an agent gate tells the reader its honesty
        # rests on the evidence contract, which is the wrong advice: a human gate rests
        # on a person having looked, and there is no mutation that could demonstrate
        # that.
        return [], (
            f"{gate_id} is a HUMAN-APPROVAL gate — there is no command to mutate, and "
            f"no test could show that a person's judgement can go the other way. It is "
            f"cleared by `ddflow approve` and nothing else."
        )
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
        from ...infra import worktree as W

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


#: What `verify_regression_test` returns. `verified` is the only pass; the caller REFUSES
#: `passed-on-prefix`/`failed-on-fix` and records `could-not-run` as the gap it is.
REGRESSION_VERIFIED = "verified"
REGRESSION_PASSES_ON_PREFIX = "passed-on-prefix"
REGRESSION_FAILS_ON_FIX = "failed-on-fix"
REGRESSION_COULD_NOT_RUN = "could-not-run"


def verify_regression_test(
    repo: Path,
    cfg: Config,
    *,
    tree: Path,
    base: str,
    tests: list[str],
    gates: dict[str, GateDef] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run the named test on the PRE-FIX source (it must FAIL) and on the fixed tree (it
    must PASS). Returns (status, evidence).

    The rule a bug cannot escape -- "not closed without a regression test that fails
    against the unfixed code" -- was prose: `bug fixed` only checked the flag was
    non-empty (B-bugfix-verified). This RUNS the test, through the project's own unit_tests
    command and `run_command_gate` (B15's machinery), not a second runner.

    The pre-fix tree is the item's BASE with the NEW test file overlaid: the base ref in a
    throwaway worktree (`ci.merge_tree`), then the test file(s) the node ids name, copied
    from `tree`. A run that could not happen -- no pytest runner, no base ref, a tree git
    would not materialise -- is `could-not-run`, RECORDED as such and never a pass: the
    caller closes the bug with the gap on the record rather than locking it open on an
    environment that cannot test.
    """
    from ...infra import worktree as W
    from .. import testselect as TS

    defn = (gates or load_gates(repo, cfg)).get("unit_tests")
    command = TS.run_command(defn.command if defn else "", tests, tree)
    if not command:
        return REGRESSION_COULD_NOT_RUN, {
            "reason": "the project runs no pytest suite (no unit_tests command names a "
            "runner), so the named test could not be run here."
        }
    run = GateDef(id="regression_check", title="regression test", command=command)
    fix_outcome, fix_ev = run_command_gate(run, tree)
    if fix_outcome == "unavailable":
        return REGRESSION_COULD_NOT_RUN, {
            "command": command,
            "tree": str(tree),
            "reason": f"the fixed tree could not run the test: {fix_ev.get('reason', '')}",
        }
    evidence: dict[str, Any] = {
        "command": command,
        "tree": str(tree),
        "base": base,
        "fix_outcome": fix_outcome,
    }
    if fix_outcome != "passed":
        return REGRESSION_FAILS_ON_FIX, {
            **evidence,
            "reason": f"the named test does not pass on the fixed tree ({fix_outcome})",
        }

    try:
        base_sha = W.rev(repo, base)
    except W.GitError as exc:
        return REGRESSION_COULD_NOT_RUN, {**evidence, "reason": f"no base ref {base!r}: {exc}"}

    from .. import ci as CI  # local: imported lazily, so gates has no module-level cycle

    paths = sorted({e.split("::", 1)[0] for e in tests})
    with CI.merge_tree(repo, base_sha, "") as (ptree, why):
        if ptree is None:
            return REGRESSION_COULD_NOT_RUN, {**evidence, "reason": why}
        for rel in paths:
            src = tree / rel
            if not src.is_file():
                return REGRESSION_COULD_NOT_RUN, {
                    **evidence,
                    "reason": f"{rel} is not in {tree}, so the pre-fix tree cannot hold the test",
                }
            dst = ptree / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        # Rebuild the command FOR THE PRE-FIX TREE. Any root-relative resolution the
        # builder does must resolve against `ptree`, not `tree`, or the "pre-fix" run
        # would collect the fixed source and a genuinely failing-first test would read
        # as passing on the pre-fix tree (rubber_duck finding #2).
        pre_command = TS.run_command(defn.command if defn else "", tests, ptree)
        if not pre_command:
            return REGRESSION_COULD_NOT_RUN, {
                **evidence,
                "reason": "the pre-fix tree has no runnable pytest command",
            }
        pre_run = GateDef(id="regression_check", title="regression test", command=pre_command)
        # Record the command ACTUALLY run on the pre-fix tree: it is rebuilt for `ptree`
        # and can differ from the fixed-tree `command` (roborev LOW), so evidence that
        # kept only `command` would misreport what the pre-fix run was.
        evidence["prefix_command"] = pre_command
        pre_outcome, pre_ev = run_command_gate(pre_run, ptree)
    evidence["prefix_outcome"] = pre_outcome
    if pre_outcome == "unavailable":
        return REGRESSION_COULD_NOT_RUN, {
            **evidence,
            "reason": f"the pre-fix tree could not run the test: {pre_ev.get('reason', '')}",
        }
    if pre_outcome == "passed":
        return REGRESSION_PASSES_ON_PREFIX, {
            **evidence,
            "reason": "the named test PASSES on the pre-fix tree (the base with the test "
            "file), so it does not catch this bug and cannot guard it",
        }
    return REGRESSION_VERIFIED, evidence
