"""Which tests an item's `ci run` takes (D-test-economy-r2): the affected ones, or the whole suite.

The default pre-push command is `pre-commit run --hook-stage pre-push --all-files`, whose
`tests` hook runs the whole suite (about 12000 tests). For ONE item the run skips that hook
and runs the tests its change reaches instead -- the same selector the unit_tests gate and
`ddflow tests --item` use (`testselect`), with the same hub cut, budget and whole-suite
fallbacks. The whole suite stays for `--full`, the merge queue integrator (a batch), a ref that
is no item, and every change the selector cannot map. The scope is recorded as one marker line
in the run's output, from which the `ci` gate's evidence takes it (`ci_marker`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..core.bookkeeping import is_state
from ..core.globs import match as globs_match
from .testscope import limits
from .testselect import changed_files, is_test_file, run_command, select

#: The hook ids a selected run skips; the selected tests replace them.
TESTS_HOOKS = "tests,scenarios"
#: Changes no import closure can reason about: the suite's own configuration.
INFRASTRUCTURE = frozenset(
    {"pyproject.toml", "uv.lock", ".pre-commit-config.yaml", ".importlinter", "pytest.ini"}
)
#: File types a change may have without the selector having to know them; anything else
#: (a lockfile, a script, a binary) runs the whole suite.
KNOWN_SUFFIXES = frozenset(
    {".py", ".md", ".txt", ".rst", ".toml", ".json", ".jsonl", ".yaml", ".yml", ".j2", ".jinja",
     ".ambr", ".sh", ".cfg", ".ini", ".csv"}
)  # fmt: skip
#: A test file that asks to run on every item's ci run, whatever the closure reaches.
ALWAYS_DECLARATION = re.compile(r"^ALWAYS\s*=\s*True\b", re.M)
#: Most paths the evidence lists, per list; the counts are always complete.
EVIDENCE_PATHS = 120


@dataclass
class Plan:
    scope: str  #: "selected" | "full"
    why: str
    command: str = ""  #: the pytest command for the selected tests ("" for full)
    skip: str = ""  #: the hook ids the main command skips
    tests: list[str] = field(default_factory=list)
    cut: list[str] = field(default_factory=list)
    cut_at: list[str] = field(default_factory=list)
    #: the always-run tests the selection did not already hold (`[ci].always_tests`)
    always: list[str] = field(default_factory=list)
    always_command: str = ""
    always_s: float = 0.0  #: what they cost; set once they ran
    ok: dict[str, bool] = field(default_factory=dict)  #: stage ("selected"|"always") -> passed

    def commands(self) -> list[str]:
        """The pytest commands of a selected run, in order (the selection, then the guards)."""
        return [c for c in (self.command, self.always_command) if c]

    def evidence(self, sha: str = "") -> dict:
        """The recorded form: small, and complete in its counts."""
        ev: dict = {"scope": self.scope, "why": self.why, "sha": sha}
        if self.scope == "selected":
            ev.update(
                selected=len(self.tests),
                tests=self.tests[:EVIDENCE_PATHS],
                cut=len(self.cut),
                cut_tests=self.cut[:EVIDENCE_PATHS],
                cut_at_hubs=self.cut_at[:EVIDENCE_PATHS],
                always=len(self.always),
                always_tests=self.always[:EVIDENCE_PATHS],
                always_s=round(self.always_s, 1),
            )
        return ev


def tests_entry(root: Path, hook_id: str = "tests") -> str:
    """The `entry:` of the pre-commit hook ``hook_id`` ("" when the config has none or it is
    not a plain or folded scalar). No YAML parser: the entry is a scalar on the lines after
    `entry:` up to the next key."""
    try:
        lines = (Path(root) / ".pre-commit-config.yaml").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    at = next((i for i, ln in enumerate(lines) if re.match(rf"\s*-\s*id:\s*{hook_id}\s*$", ln)), -1)
    if at < 0:
        return ""
    for i in range(at + 1, len(lines)):
        if re.match(r"\s*-\s*id:", lines[i]):
            break
        m = re.match(r"(\s*)entry:\s*(.*)$", lines[i])
        if not m:
            continue
        indent, first = len(m[1]), m[2].strip()
        if first and first not in (">", ">-", "|", "|-"):
            return first.strip("\"'")
        parts: list[str] = []
        for body in lines[i + 1 :]:
            if body.strip() and len(body) - len(body.lstrip()) <= indent:
                break
            parts.append(body.strip())
        return " ".join(p for p in parts if p)
    return ""


def always_tests(cfg: Config, tree: Path) -> list[str]:
    """The test files that run for every item whatever the change reaches: those matching
    `[ci].always_tests` and those declaring `ALWAYS = True`. They are the repo-wide guards and
    pins, whose input is the whole code base, so no import closure can know a change affects
    them (a new constant, a new knob in a pinned output)."""
    tests = Path(tree) / "tests"
    if not tests.is_dir():
        return []
    globs = list(cfg.ci.always_tests)
    found: list[str] = []
    for f in sorted(tests.rglob("*.py")):
        rel = f.relative_to(tree).as_posix()
        if not is_test_file(rel):
            continue
        if any(globs_match(rel, g) for g in globs):
            found.append(rel)
            continue
        try:
            if ALWAYS_DECLARATION.search(f.read_text(encoding="utf-8", errors="replace")):
                found.append(rel)
        except OSError:
            continue
    return found


def _infrastructure(changed: list[str]) -> str:
    """Why a change is beyond the selector (the suite's configuration, an unknown file type)."""
    for c in changed:
        if is_state(c):
            continue
        name = c.rsplit("/", 1)[-1]
        if name == "conftest.py" or (
            c.startswith("tests/") and name.endswith(".py") and not is_test_file(c)
        ):
            return f"{c} is test infrastructure"
        if c in INFRASTRUCTURE or name in INFRASTRUCTURE:
            return f"{c} configures the suite"
        suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        if suffix not in KNOWN_SUFFIXES:
            return f"{c} is a file type the selector does not know"
    return ""


def plan(cfg: Config, tree: Path, base: str, *, requested: str) -> Plan:
    """The scope of one item's run in ``tree`` (HEAD merged with ``base``): ``requested`` is
    "selected" or "full" (the knob, or `--full`). Every refusal to select says why."""
    if requested != "selected":
        return Plan("full", "the whole suite was asked for ([ci].tests_scope or --full)")
    if cfg.ci.command.strip():
        return Plan("full", "an explicit [ci].command runs as written")
    entry = tests_entry(tree)
    if not entry:
        return Plan("full", "the pre-commit config has no `tests` hook to replace")
    changed = changed_files(tree, base)
    if changed is None:
        return Plan("full", f"git could not say what changed since {base}")
    if why := _infrastructure(changed):
        return Plan("full", f"{why}: only the whole suite keeps recall")
    sel = select(tree, base, changed=changed, **limits(cfg))
    if sel is None:
        return Plan("full", f"git could not say what changed since {base}")
    if sel.full:
        return Plan("full", f"{sel.full}: only the whole suite keeps recall")
    paths = [t.path for t in sel.tests]
    if not paths:  # the whole suite holds the always-run guards too
        return Plan("full", "no test reaches the change (running none would pass vacuously)")
    held = set(paths)
    always = [t for t in always_tests(cfg, tree) if t not in held]
    cmd = run_command(entry, paths, tree)
    always_cmd = run_command(entry, always, tree) if always else ""
    if not cmd:
        return Plan("full", "the `tests` hook does not run pytest")
    cut = [t.path for t in sel.cut]
    why = f"{len(paths)} test file(s) its change reaches" + (
        f", {len(cut)} cut at the budget" if cut else ""
    )
    if always:
        why += f", {len(always)} always-run guard file(s)"
    return Plan("selected", why, cmd, TESTS_HOOKS, paths, cut, sel.cut_at, always, always_cmd)
