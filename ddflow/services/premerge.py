"""The pre-merge check: the project's pre-commit hooks on the files an item changed, run on the
item's tree right before a merge (D-premerge-precommit-gate-2026-11-12).

Steps, each ending ``passed``, ``fixed``, ``failed``, ``unavailable`` or ``skipped``:

* ``hooks`` -- the hooks of ``.pre-commit-config.yaml`` for each of `[premerge].stages`, run as
  ``pre-commit run --files <changed> --hook-stage <stage>``; a project without that file gets
  the built-in generic set (`_generic`).
* ``tests`` -- the item's changed or added test files, never the whole suite.

FIX MODE: a hook that rewrites a file is not a failure when the file is one the item itself
changed. The rewrite is committed to the item's branch (``git add`` of exactly those files)
and the hooks run again, at most `[premerge].max_fix_rounds` times. A file the item did not
change is restored from HEAD and never committed, and the check says so (``Report.outside``).

A tool that is missing or cannot run, a hook repository that cannot be fetched, or a spent
budget is ``unavailable`` and never ``passed``; whether that refuses is
`[premerge].on_unavailable`. A hook that ran and failed always refuses. Nothing is installed
and no network is used by this module (pre-commit itself fetches a hook repository only the
first time; that failing is ``unavailable``).
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters.clock import DEFAULT as _CLOCK
from ..infra import git as G
from ..infra import proc as P
from .ci.ruff import ruff_bin
from .gateenv import child_env

PASSED, FIXED, FAILED, UNAVAILABLE, SKIPPED = "passed", "fixed", "failed", "unavailable", "skipped"
CONFIG = ".pre-commit-config.yaml"
#: pytest's exit status when it collected no test.
_PYTEST_NONE = 5
COMMIT_SUBJECT = "premerge: apply hook fixes"

_HOOK_LINE = re.compile(
    r"^(?P<name>.+?)\.{3,}(?:\(no files to check\))?(?P<res>Passed|Failed|Skipped)\s*$"
)
#: What pre-commit prints when it could not run a hook at all (config, clone, install).
_COULD_NOT_RUN = ("An error has occurred", "Failed to clone", "InvalidConfigError")
_OWN_ERROR_MARK = "Check the log at"
_KEY_HEADER = re.compile("-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?" + "PRIVATE KEY")
_TRAILING = re.compile(rb"[ \t]+(?=\r?\n|\Z)")


@dataclass
class HookResult:
    name: str
    outcome: str
    detail: str = ""


@dataclass
class Report:
    results: list[HookResult] = field(default_factory=list)
    #: the item's own files a hook rewrote and the check committed
    fixed: list[str] = field(default_factory=list)
    #: files the item did not change that a hook rewrote: restored, never committed
    outside: list[str] = field(default_factory=list)
    commit: str = ""
    refuse_unavailable: bool = False
    disabled: bool = False

    @property
    def failing(self) -> list[HookResult]:
        return [r for r in self.results if r.outcome == FAILED]

    @property
    def outcome(self) -> str:
        """``failed`` when a step ran and failed (or, under ``on_unavailable = refuse``, could
        not run); ``unavailable`` when one could not run; else ``passed``."""
        if self.failing:
            return FAILED
        if any(r.outcome == UNAVAILABLE for r in self.results):
            return FAILED if self.refuse_unavailable else UNAVAILABLE
        return PASSED

    @property
    def refused(self) -> bool:
        return self.outcome == FAILED

    def summary(self) -> str:
        if self.disabled:
            return "premerge: disabled ([premerge].enabled = false)"
        lines = ["premerge: " + self.outcome]
        lines += [
            f"  {r.name} {r.outcome}" + (f" ({r.detail})" if r.detail else "") for r in self.results
        ]
        if self.fixed:
            how = f"committed {self.commit[:12]}" if self.commit else "left uncommitted"
            lines.append(f"  fixed, {how}: {', '.join(self.fixed)}")
        if self.outside:
            lines.append(
                f"  hooks rewrote files outside the item (restored, not committed): {', '.join(self.outside)}"
            )
        return "\n".join(lines)


class _Budget:
    def __init__(self, seconds: float, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.end = clock() + seconds

    def left(self) -> float:
        return self.end - self.clock()


def ruff_argv(tree: Path) -> list[str]:
    """The ruff command of this tree (its pinned venv, else PATH); [] when there is none."""
    return ruff_bin(tree)


def _tail(text: str, lines: int = 6) -> str:
    return " | ".join(text.strip().splitlines()[-lines:])[:500]


def _run(argv: list[str], tree: Path, budget: _Budget, env=None) -> tuple[P.Captured, str]:
    """Run ``argv`` in the remaining budget; the second value is why it did not run ('')."""
    if budget.left() <= 0:
        return P.Captured(), "budget spent before it started"
    done = P.capture(argv, timeout=budget.left(), cwd=tree, env=env)
    if done.timed_out:
        return done, "budget ran out"
    if not done.ran:
        return done, f"{argv[0]} could not run: {type(done.error).__name__}"
    return done, ""


def changed_files(tree: Path, base: str) -> list[str] | None:
    """The files the item itself changed since ``base`` (three-dot: from the merge base, so a
    file a merge pulled in is not the item's), still present in the tree; None when git could
    not say."""
    names = G.paths(tree, "diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD")
    if names is None:
        return None
    root = Path(tree).resolve()
    keep = []
    for n in names:
        if n.startswith("-") or Path(n).is_absolute():
            continue
        p = (root / n).resolve()
        if p.is_relative_to(root) and p.is_file():
            keep.append(n)
    return keep


def _modified(tree: Path) -> list[str]:
    """Tracked files that differ from HEAD (the tree starts clean, so these are hook output)."""
    return G.paths(tree, "diff", "--name-only", "HEAD") or []


def _restore(tree: Path, paths: list[str]) -> None:
    if paths:
        G.run(tree, "restore", "--source=HEAD", "--staged", "--worktree", "--", *paths)


def _read(tree: Path, name: str) -> bytes:
    try:
        return (Path(tree) / name).read_bytes()
    except OSError:
        return b""


def _commit(tree: Path, files: list[str]) -> str:
    G.run(tree, "add", "--", *files)
    msg = COMMIT_SUBJECT + "\n\n" + "\n".join(files)
    done = G.run(tree, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", msg)
    return G.head_commit(tree) if done.ok else ""


# -- the project's hooks --------------------------------------------------------------------


def _precommit_round(tree: Path, files: list[str], pm, budget: _Budget) -> list[HookResult]:
    env = child_env()
    if pm.skip_hooks:
        env["SKIP"] = ",".join(pm.skip_hooks)
    base = shlex.split(pm.precommit_command)
    out: list[HookResult] = []
    for stage in pm.stages:
        argv = [*base, "run", "--color", "never", "--hook-stage", stage, "--files", *files]
        done, why = _run(argv, tree, budget, env)
        if why:
            return [*out, HookResult("hooks", UNAVAILABLE, why)]
        text = done.stdout + done.stderr
        found = [m for ln in text.splitlines() if (m := _HOOK_LINE.match(ln))]
        # pre-commit's own failure phrase counts when no hook ran, or when it is followed by its
        # own "Check the log at" line (a hook printing the bare phrase is still just a hook).
        phrase = any(m in text for m in _COULD_NOT_RUN)
        could_not = phrase and (not found or _OWN_ERROR_MARK in text)
        if done.returncode not in (0, 1) or could_not:
            return [
                *out,
                HookResult(
                    "hooks", UNAVAILABLE, f"pre-commit exited {done.returncode}: {_tail(text)}"
                ),
            ]
        for m in found:
            res = {"Passed": PASSED, "Failed": FAILED, "Skipped": SKIPPED}[m["res"]]
            out.append(HookResult(m["name"].strip(), res))
        if done.returncode == 1 and not any(r.outcome == FAILED for r in out):
            out.append(HookResult("hooks", FAILED, _tail(text)))
    return out


# -- the generic set, for a project without a pre-commit config --------------------------------


_GENERIC = ("trailing-whitespace", "end-of-file", "merge-conflict", "private-key", "large-file")


def _scan(data: bytes, large: int) -> tuple[list[str], bytes]:
    """(the generic checks ``data`` trips, the bytes with the whitespace ones fixed)."""
    hit: list[str] = []
    if len(data) > large:
        hit.append("large-file")
    if b"\0" in data:
        return hit, data
    if any(ln.startswith((b"<<<<<<< ", b">>>>>>> ")) for ln in data.splitlines()):
        hit.append("merge-conflict")
    if _KEY_HEADER.search(data.decode("utf-8", "replace")):
        hit.append("private-key")
    new = _TRAILING.sub(b"", data)
    if new != data:
        hit.append("trailing-whitespace")
    if new and not new.endswith(b"\n"):
        new += b"\n"
        hit.append("end-of-file")
    elif new.endswith(b"\n\n") and new.strip():
        new = new.rstrip(b"\n") + b"\n"
        hit.append("end-of-file")
    return hit, new


def _generic(tree: Path, files: list[str], pm, budget: _Budget) -> list[HookResult]:
    """Built-in checks on ``files``; the whitespace ones fix when `[premerge].fix` is on."""
    bad: dict[str, list[str]] = {n: [] for n in _GENERIC}
    for name in files:
        path = Path(tree) / name
        data = path.read_bytes()
        hit, new = _scan(data, pm.large_file_kb * 1024)
        for h in hit:
            bad[h].append(name)
        if new != data and pm.fix:
            path.write_bytes(new)
    out = []
    for name, hit in bad.items():
        fixable = name in ("trailing-whitespace", "end-of-file")
        if hit and fixable and pm.fix:
            out.append(HookResult(name, PASSED, "rewrote " + ", ".join(hit)))
        else:
            out.append(
                HookResult(name, FAILED, ", ".join(hit)) if hit else HookResult(name, PASSED)
            )
    py = [f for f in files if f.endswith((".py", ".pyi"))]
    out.append(_ruff(tree, py, pm, budget))
    return out


def _ruff(tree: Path, py: list[str], pm, budget: _Budget) -> HookResult:
    if not py:
        return HookResult("ruff", SKIPPED, "no changed Python file")
    argv = ruff_argv(tree)
    if not argv:
        return HookResult(
            "ruff", UNAVAILABLE, "no ruff (not a uv project and `ruff` is not on PATH)"
        )
    steps = [["check", "--fix"], ["format"]] if pm.fix else []
    steps += [["check"], ["format", "--check"]]
    for step in steps:
        done, why = _run([*argv, *step, "--", *py], tree, budget)
        if why:
            return HookResult("ruff", UNAVAILABLE, why)
        checking = step in (["check"], ["format", "--check"]) or not pm.fix
        if checking and done.returncode == 1:
            return HookResult(
                "ruff", FAILED, f"ruff {step[0]}: " + _tail(done.stdout + done.stderr)
            )
        if done.returncode not in (0, 1):
            return HookResult("ruff", UNAVAILABLE, f"ruff {step[0]} exited {done.returncode}")
    return HookResult("ruff", PASSED, f"{len(py)} file(s)")


# -- the changed tests -------------------------------------------------------------------------


def _is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") and name.endswith(".py")


def _tests(tree: Path, files: list[str], pm, budget: _Budget) -> HookResult:
    tests = [f for f in files if _is_test(f)]
    if not tests:
        return HookResult("tests", SKIPPED, "no changed test file")
    done, why = _run([*shlex.split(pm.test_command), *tests], tree, budget, child_env())
    if why:
        return HookResult("tests", UNAVAILABLE, why)
    if done.returncode == 0:
        return HookResult("tests", PASSED, f"{len(tests)} file(s)")
    if done.returncode in (1, 2):
        return HookResult("tests", FAILED, _tail(done.stdout + done.stderr))
    if done.returncode == _PYTEST_NONE:
        return HookResult("tests", SKIPPED, "no test collected")
    return HookResult("tests", UNAVAILABLE, f"test command exited {done.returncode}")


# -- the check ---------------------------------------------------------------------------------


def _note_outside(rep: Report, results: list[HookResult], out: list[str]) -> None:
    """Record the files a hook rewrote outside the item (restored) on the report and on every
    failing hook, which may well be the one that wanted them."""
    rep.outside = sorted({*rep.outside, *out})
    for r in results:
        if r.outcome == FAILED:
            r.detail = (
                (r.detail + "; " if r.detail else "")
                + "a hook rewrote a file outside the item: "
                + ", ".join(out)
            )


def _relabel_fixed(results: list[HookResult], prior: set[str], committed: bool) -> None:
    """A hook that passed after its fixes were committed read ``fixed``, not a plain pass."""
    for r in results:
        if r.outcome == PASSED and (
            r.name in prior or (committed and r.detail.startswith("rewrote"))
        ):
            r.outcome = FIXED


def _settle(
    tree: Path,
    own: set[str],
    results: list[HookResult],
    pm,
    rep: Report,
    kept: dict[str, bytes],
    *,
    last: bool,
) -> bool:
    """After a round: restore what a hook rewrote outside the item, commit the item's own
    fixes. True when fixes were committed and the hooks should run again."""
    mod = _modified(tree)
    mine = [m for m in mod if m in own and kept.get(m) != _read(tree, m)]
    out = [m for m in mod if m not in own]
    if out:
        _restore(tree, out)
        _note_outside(rep, results, out)
    if any(r.name == "hooks" and r.outcome == UNAVAILABLE for r in results):
        _restore(tree, mine)  # pre-commit itself could not run: whatever it left is not a fix
        return False
    if not mine:
        return False
    if pm.fix and not last:
        sha = _commit(tree, mine) if pm.commit_fixes else ""
        if sha or not pm.commit_fixes:
            kept.update({m: _read(tree, m) for m in mine} if not pm.commit_fixes else {})
            rep.fixed = sorted({*rep.fixed, *mine})
            rep.commit = sha or rep.commit
            return True
        results.append(HookResult("commit", FAILED, "git could not commit the fixes"))
    else:
        what = "rewrites" if pm.fix else "would rewrite (fix is off)"
        results.append(HookResult("fixes", FAILED, f"a hook still {what}: {', '.join(mine)}"))
    _restore(tree, mine)
    return False


def _hooks_step(tree: Path, files: list[str], pm, budget: _Budget, rep: Report) -> list[HookResult]:
    """Run the hooks, committing the item's own fixes between rounds; the last round's results."""
    has_config = (Path(tree) / CONFIG).is_file()
    if not has_config and not pm.generic_hooks:
        return [HookResult("hooks", SKIPPED, f"no {CONFIG} and [premerge].generic_hooks is off")]
    if not files:
        return [HookResult("hooks", SKIPPED, "the item changed no file")]
    own = set(files)
    prior: set[str] = set()
    kept: dict[str, bytes] = {}  # fixes left uncommitted ([premerge].commit_fixes off)
    results: list[HookResult] = []
    for round_ in range(pm.max_fix_rounds + 1):
        try:
            results = (
                _precommit_round(tree, files, pm, budget)
                if has_config
                else _generic(tree, files, pm, budget)
            )
        except OSError as exc:  # a file unreadable or unwritable: this step's trouble, not a crash
            results = [HookResult("hooks", UNAVAILABLE, f"{type(exc).__name__}: {exc}")]
        again = _settle(tree, own, results, pm, rep, kept, last=round_ >= pm.max_fix_rounds)
        if not again:
            break
        prior |= {r.name for r in results if r.outcome in (FAILED, FIXED)}
    _relabel_fixed(results, prior, bool(rep.fixed))
    return results


def run(tree: Path, base: str, cfg, *, clock: Callable[[], float] = _CLOCK.monotonic) -> Report:
    """Check ``tree`` (the item's worktree, HEAD = its branch) against ``base``; ``cfg`` is the
    project's `Config` (its `[premerge]` section decides). Never raises for a step's own trouble: that is the step's ``unavailable``."""
    pm = cfg.premerge
    rep = Report(refuse_unavailable=pm.on_unavailable == "refuse")
    if not pm.enabled:
        rep.disabled = True
        return rep
    budget = _Budget(pm.budget_s, clock)
    tree = Path(tree)
    files = changed_files(tree, base)
    if files is None:
        rep.results.append(
            HookResult("hooks", UNAVAILABLE, f"git could not list the files changed since {base}")
        )
        return rep
    dirty = G.run(tree, "status", "--porcelain", "--untracked-files=no")
    if not dirty.ok or dirty.out.strip():
        why = "the tree has uncommitted changes: the check commits only a clean tree"
        rep.results.append(HookResult("hooks", UNAVAILABLE, why))
        return rep
    rep.results += _hooks_step(tree, files, pm, budget, rep)
    rep.results.append(_tests(tree, files, pm, budget))
    return rep
