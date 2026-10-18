"""A hard-coded agent name or harness path outside ``ddflow/harnesses/`` fails this test.

Everything ddflow knows about one agent lives in its descriptor (``ddflow/harnesses/<id>.toml``,
``services/harnessreg``); code that names an agent, or the directory an agent keeps its files
in, is the parallel table the descriptors replaced. A string constant (not a docstring or a
comment) that equals a descriptor id, or sits under a directory a descriptor names, is a site.

It is a ratchet, like the project's other pattern counters: ``BASELINE`` lists each file that
still holds sites, how many, and why that one is not a descriptor read. The count may only go
down; a new file with a site, or one more site in a listed file, fails.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ddflow.services import harnessreg as HR

PKG = Path(__file__).resolve().parents[1] / "ddflow"

#: file (relative to the repo) -> (sites, why they are not descriptor reads)
BASELINE: dict[str, tuple[int, str]] = {
    "ddflow/services/hookio.py": (
        20,
        "the dialect names: keys of NORMALIZERS and EMITTERS, which the descriptors' "
        "[hooks] normalizer/emitter name",
    ),
    "ddflow/surfaces/commands/setup.py": (
        11,
        "the --claude/--gemini aliases of `hooks install --harness` and the per-event "
        "`hooks` subcommands, kept until 1.0 (D-compat-r2)",
    ),
    "ddflow/services/claudehooks.py": (
        9,
        "the legacy hook table: the identities of hook entries already written into "
        "operators' settings files, kept byte for byte",
    ),
    "ddflow/config_sections/agent.py": (
        6,
        "model vendors of the agent roster (anthropic, google): a different domain",
    ),
    "ddflow/services/harnessreg.py": (
        6,
        "the hook-style vocabulary, the legacy session variables and the title doc",
    ),
    "ddflow/services/compat_refs.py": (
        5,
        "the reference checker's table of known agent paths; read by `doctor`",
    ),
    "ddflow/services/review.py": (3, "reviewer provider kinds (gemini) are not harnesses"),
    "ddflow/api/setup.py": (2, "the --claude/--gemini flag parameters of `hooks`"),
    "ddflow/services/adopt.py": (2, "the CLAUDE.md pointer written when claude is adopted"),
    "ddflow/services/hookwriters/__init__.py": (2, "the descriptor styles claude and gemini"),
    "ddflow/services/hookwriters/flatlist.py": (2, "the descriptor styles cursor and copilot"),
    "ddflow/services/mcpconfig.py": (2, "the MCP file shapes named after their first user"),
    "ddflow/surfaces/declared/hooks.py": (2, "the MCP `claude` flag argument of ddflow_hooks"),
    "ddflow/core/bookkeeping.py": (1, "bookkeeping paths ddflow itself never writes"),
    "ddflow/services/harness.py": (1, "Claude-only onboarding, replaced by B-hx-onboard-generic"),
    "ddflow/services/legacy.py": (1, "the legacy command-file glob an import reads"),
    "ddflow/surfaces/parsers/workflow.py": (1, "the default of `hooks run --harness` (compat)"),
}


def _roots() -> set[str]:
    roots: set[str] = set()
    for h in HR.load():
        paths = [h.mcp.path, h.hooks.file if h.hooks else ""]
        for name in ("commands", "skills", "subagents", "worktrees"):
            sec = getattr(h, name)
            paths.append(getattr(sec, "dir", "") if sec else "")
        for p in paths:
            p = p.removeprefix("~/")
            if p.startswith(".") and "/" in p:
                roots.add(p.split("/")[0] + "/")
    return roots


def _docstrings(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = n.body[0] if n.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def sites(source: str, ids: set[str], roots: set[str]) -> list[int]:
    """Line numbers of the string constants that name an agent or sit under an agent's directory."""
    tree = ast.parse(source)
    skip = _docstrings(tree)
    found = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip:
            v = n.value
            if v in ids or any(v.startswith(r) or "/" + r in v for r in roots):
                found.append(n.lineno)
    return found


def _counts() -> dict[str, int]:
    ids = {h.id for h in HR.load()}
    roots = _roots()
    out = {}
    for f in sorted(PKG.rglob("*.py")):
        if "harnesses" in f.relative_to(PKG).parts[:1]:
            continue
        n = len(sites(f.read_text("utf-8"), ids, roots))
        if n:
            out[f.relative_to(PKG.parent).as_posix()] = n
    return out


def test_no_new_agent_literal():
    counts = _counts()
    grew = {
        f: (n, BASELINE.get(f, (0, ""))[0])
        for f, n in counts.items()
        if n > BASELINE.get(f, (0, ""))[0]
    }
    assert not grew, (
        f"hard-coded agent names or harness paths (file: found, allowed): {grew}. Read the "
        "descriptor (services/harnessreg) instead of naming the agent."
    )


def test_the_baseline_only_shrinks():
    counts = _counts()
    stale = {f: (n, counts.get(f, 0)) for f, (n, _why) in BASELINE.items() if counts.get(f, 0) < n}
    assert not stale, f"lower BASELINE (file: listed, now): {stale}"


def test_every_baseline_entry_has_a_reason():
    assert all(len(why) > 15 for _n, why in BASELINE.values())


def test_the_detector_can_fail():
    ids, roots = {"claude"}, {".claude/"}
    assert sites('x = "claude"\n', ids, roots) == [1]
    assert sites('x = ".claude/settings.json"\n', ids, roots) == [1]
    assert sites('"""claude"""\ndef f():\n    """.claude/x"""\n', ids, roots) == []
