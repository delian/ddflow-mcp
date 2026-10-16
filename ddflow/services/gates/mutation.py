"""Proving a check CAN fail: gate mutation verification and the regression-test check."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import Config
from ...core.model import Item, State
from ...infra import fsio
from ...infra import worktree as W
from .defs import GateDef, load_gates
from .regression_testfix import verify_test_only_fix
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


def _branch_mutations(
    repo: Path, cfg: Config, tree: Path | None, gdef: GateDef
) -> list[dict[str, str]]:
    """The registered mutations, plus any the item's own tree adds (B-verify-ci-blind).

    The registry used to be read from the primary alone, so a mutation committed on a
    branch could not be verified before it merged. Entries the tree repeats are not
    doubled."""
    muts = list(gdef.mutations)
    if tree is None or tree.resolve() == Path(repo).resolve():
        return muts
    try:
        mine = load_gates(tree, cfg).get(gdef.id)
    except Exception:  # an unreadable branch config must not hide the primary's registry
        return muts
    return muts + [m for m in (mine.mutations if mine else []) if m not in muts]


def _mutated_commit(cwd: Path, rel: str, text: str) -> tuple[str, str]:
    """(sha, "") of a throwaway commit: HEAD with ``rel`` holding ``text``; or ("", why).

    Built with a temporary index, so neither the branch, the real index nor the working
    tree moves; the commit is unreferenced and left to git's gc."""
    head = W.git(cwd, "rev-parse", "--verify", "HEAD")
    if not head.ok:
        return "", f"no HEAD commit in {cwd}: {head.err.strip()[:200]}"
    entry = W.git(cwd, "ls-tree", "HEAD", "--", rel).out.split("\t")[0].split()
    mode = entry[0] if entry else "100644"
    with fsio.scratch_dir("ddflow-mut-", ignore_cleanup_errors=True) as tmp:
        env = {
            "GIT_INDEX_FILE": str(tmp / "index"),
            "GIT_AUTHOR_NAME": "ddflow",
            "GIT_AUTHOR_EMAIL": "ddflow@localhost",
            "GIT_COMMITTER_NAME": "ddflow",
            "GIT_COMMITTER_EMAIL": "ddflow@localhost",
        }
        steps = [
            W.git(cwd, "read-tree", "HEAD", env=env),
            W.git(cwd, "hash-object", "-w", "--stdin", input=text, env=env),
        ]
        if not all(r.ok for r in steps):
            return "", f"could not build the mutated commit: {(steps[0].err or steps[1].err)[:200]}"
        blob = steps[1].out.strip()
        upd = W.git(cwd, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{rel}", env=env)
        tree = W.git(cwd, "write-tree", env=env) if upd.ok else upd
        if not tree.ok:
            return "", f"could not build the mutated commit: {tree.err[:200]}"
        commit = W.git(
            cwd,
            "commit-tree",
            tree.out.strip(),
            "-p",
            head.out.strip(),
            "-m",
            "ddflow gate verify: throwaway mutation",
            env=env,
        )
    if not commit.ok:
        return "", f"could not build the mutated commit: {commit.err[:200]}"
    return commit.out.strip(), ""


def _run_committed(
    gdef: GateDef, cwd: Path, rel: str, text: str | None
) -> tuple[str, dict[str, Any]]:
    """Run a gate that reads a COMMITTED ref in a scratch checkout of the commit under
    test: a throwaway commit holding the mutation, or HEAD itself for the baseline
    (``text`` None). Nothing of ``cwd`` is touched."""
    # ``text`` None: the unmutated HEAD, the baseline in the SAME kind of checkout the
    # mutated runs get (a bare checkout has no untracked or ignored files to lean on).
    if text is None:
        head = W.git(cwd, "rev-parse", "--verify", "HEAD")
        sha, why = head.out.strip(), head.err.strip()[:200]
    else:
        sha, why = _mutated_commit(cwd, rel, text)
    if not sha:
        return "unavailable", {"reason": why}
    with W.scratch_tree(cwd, sha, detach=True, prefix="ddflow-mut.", quiet=True) as s:
        if s.path is None:
            return "unavailable", {
                "reason": f"could not check out the commit under test: {s.add.err[:200]}"
            }
        return run_command_gate(gdef, s.path)


def _try_mutation(gdef: GateDef, cwd: Path, mut: dict[str, str]) -> MutationResult:
    """Apply one registered mutation, run the gate, and restore whatever was touched."""
    gate_id, rel = gdef.id, mut.get("file", "")
    target = cwd / rel
    old, new = mut.get("old", ""), mut.get("new", "")
    if not target.is_file():
        return MutationResult(gate_id, rel, False, False, "file not found")
    src = target.read_text("utf-8")
    count = src.count(old)
    if count != 1:
        return MutationResult(
            gate_id,
            rel,
            False,
            False,
            f"`old` appears {count} time(s); the edit must be unambiguous. "
            f"A mutation that did not apply is NOT a passed mutation test.",
        )
    mutated = src.replace(old, new, 1)
    try:
        if gdef.reads_ref:
            # The gate reads a commit (ci), so an uncommitted edit is invisible to it
            # and it would report PASSED on mutated source (B-verify-ci-blind).
            outcome, ev = _run_committed(gdef, cwd, rel, mutated)
        else:
            fsio.replace_text(target, mutated, fsync=False)
            outcome, ev = run_command_gate(gdef, cwd)
    finally:
        if not gdef.reads_ref:
            fsio.replace_text(target, src, fsync=False)
    detected = outcome == "failed"
    detail = ""
    if not detected:
        detail = (
            f"the gate reported {outcome!r} on mutated source — it cannot "
            f"see this class of change. {ev.get('reason', '')}"
        )[:300]
        if re.search(r"\bHEAD\b", gdef.command) and not gdef.reads_ref:
            detail += (
                " (its command reads HEAD: declare `reads_ref = true` so the "
                "mutation is committed on a throwaway commit)"
            )
    return MutationResult(gate_id, rel, True, detected, detail)


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
    cwd = repo
    if item is not None and gdef.cwd == "worktree" and item.worktree:
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

    own = W.load_path(repo, item.worktree) if item is not None and item.worktree else None
    mutations = _branch_mutations(repo, cfg, own, gdef)
    if not mutations:
        return [], (
            f"{gate_id} has NO registered mutations, so nothing has ever shown it can "
            f"fail. Add `mutations = [{{ file = '...', old = '...', new = '...' }}]` to "
            f"[gate.{gate_id}]: an edit that this gate must catch."
        )

    # The baseline. Everything below compares against it, and without it a red gate
    # "detects" every mutation.
    baseline, base_ev = (
        _run_committed(gdef, cwd, "", None) if gdef.reads_ref else run_command_gate(gdef, cwd)
    )
    if baseline != "passed":
        return [], (
            f"{gate_id} does not pass on the UNMUTATED source — it reported "
            f"{baseline!r} before anything was changed, so there is no green baseline "
            f"to compare against and a 'detected' result would prove nothing. "
            f"{base_ev.get('reason', '')}".strip()[:400]
        )

    return [_try_mutation(gdef, cwd, mut) for mut in mutations], ""


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
    after: str = "",
    mutation: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run the named test on the PRE-FIX source (it must FAIL) and on the fixed tree (it
    must PASS). Returns (status, evidence).

    ``after``, a commit: the fixed tree is a scratch checkout of it and ``tree`` is
    unused -- for a fix that already landed, checked against ``base`` = the target as it
    was just before the landing (B0d5253d31f).

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

    A fix confined to test files has no pre-fix tree the new test fails on (B Bc262739f62):
    see `regression_testfix`. ``mutation`` ({file, old, new}) is the agent's edit that
    disables such a fix; ddflow applies it and the test must then fail.
    """
    # Function-level, not top-level: ci and testselect import gates back.
    from .. import ci as CI
    from .. import testselect as TS

    if after:
        with CI.merge_tree(repo, after, "") as (landed, why):
            if landed is None:
                return REGRESSION_COULD_NOT_RUN, {"reason": why}
            status, ev = verify_regression_test(
                repo, cfg, tree=landed, base=base, tests=tests, gates=gates, mutation=mutation
            )
        # The scratch checkout is gone once this returns: record the commit it was.
        return status, {**ev, "tree": after}
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
        testfix = verify_test_only_fix(
            repo, cfg, tree=tree, base_sha=base_sha, tests=tests, defn=defn, mutation=mutation
        )
        if testfix is not None:
            return testfix[0], {**evidence, **testfix[1]}
        return REGRESSION_PASSES_ON_PREFIX, {
            **evidence,
            "reason": "the named test PASSES on the pre-fix tree (the base with the test "
            "file), so it does not catch this bug and cannot guard it",
        }
    return REGRESSION_VERIFIED, evidence
