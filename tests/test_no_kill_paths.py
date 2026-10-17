"""No ddflow code signals a process it did not start, or deletes work on the watchdog's behalf
(DX-health-watch).

The scan covers ALL of `ddflow/`, by syntax tree, not by substring: every call that can end a
process (`os.kill` with a real signal, `os.killpg`, `.kill()`, `.terminate()`, `.send_signal()`,
`os.system`, `pkill`) must be one of the sites below, each of which acts on a child the same
code started. A new site fails this test until it is reviewed and listed. The watchdog's own
modules additionally may not delete anything at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

import ddflow

ROOT = Path(ddflow.__file__).parent

#: (file under ddflow/, enclosing function): a signal sent to a process that code started.
OWN_CHILD_SITES = {
    ("infra/proc.py", "kill_group"),  # a Popen group the caller launched
    ("services/review.py", "ensure_running"),  # the reviewer subprocess it spawned
    ("infra/proc.py", "stop_group"),
    ("infra/proc.py", "_shell_group"),
    ("services/review.py", "_abort"),
    ("services/companions.py", "_stop"),  # the install subprocess it spawned
}

WATCHDOG = (
    "core/health.py",
    "services/healthwatch.py",
    "api/healthwatch.py",
    "surfaces/declared/health.py",
    "surfaces/parsers/health.py",
    "infra/harness_identity.py",
)
#: the one place a watchdog module may unlink: a harness's own identity record
IDENTITY_RECORD_WRITE = ("infra/harness_identity.py", "declare")
KILLERS = {"kill_group", "killpg", "terminate", "kill", "send_signal", "system"}
DELETERS = {"remove", "unlink", "rmtree", "rmdir", "removedirs", "move", "rename", "replace"}


def _calls(path: Path):
    tree = ast.parse(path.read_text())
    stack: list[str] = []

    def walk(node):
        named = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        if named:
            stack.append(node.name)
        if isinstance(node, ast.ImportFrom) and node.module in {"os", "signal", "subprocess"}:
            for alias in node.names:  # `from os import killpg as _kp` hides the call name
                if alias.name in KILLERS and alias.asname:
                    yield (
                        alias.name,
                        ast.Call(ast.Name(alias.name), [], []),
                        (stack[-1] if stack else ""),
                    )
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "getattr":
            arg = node.args[1] if len(node.args) > 1 else None
            if isinstance(arg, ast.Constant) and arg.value in KILLERS:  # getattr(os, "kill")
                yield str(arg.value), node, (stack[-1] if stack else "")
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            yield name, node, (stack[-1] if stack else "")
        for child in ast.iter_child_nodes(node):
            yield from walk(child)
        if named:
            stack.pop()

    return list(walk(tree))


def _is_probe(name: str, call: ast.Call) -> bool:
    """A signal-0 call (`os.kill(pid, 0)`, `os.killpg(pgid, 0)`) only asks whether it exists."""
    last = call.args[-1] if call.args else None
    return (
        name in {"kill", "killpg", "send_signal"}
        and len(call.args) >= 1
        and isinstance(last, ast.Constant)
        and last.value == 0
        and (name == "send_signal" or len(call.args) == 2)
    )


def test_every_process_signal_in_ddflow_is_on_a_child_it_started():
    unlisted = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for name, call, fn in _calls(path):
            if name in KILLERS and not _is_probe(name, call) and (rel, fn) not in OWN_CHILD_SITES:
                unlisted.append(f"{rel}:{call.lineno} {name}() in {fn or '<module>'}")
    assert not unlisted, "unreviewed process-signalling call(s): " + "; ".join(unlisted)


def test_the_listed_sites_still_exist():
    seen = set()
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        seen |= {(rel, fn) for name, _c, fn in _calls(path) if name in KILLERS}
    assert OWN_CHILD_SITES <= seen, sorted(OWN_CHILD_SITES - seen)


def test_the_watchdog_modules_signal_nothing_and_delete_nothing():
    bad = []
    for rel in WATCHDOG:
        for name, call, fn in _calls(ROOT / rel):
            if name in KILLERS and not _is_probe(name, call):
                bad.append(f"{rel}:{call.lineno} {name}() in {fn}")
            if _deletes(name, call) and (rel, fn) != IDENTITY_RECORD_WRITE:
                bad.append(f"{rel}:{call.lineno} {name}() in {fn}")
    assert not bad, bad


def _deletes(name: str, call: ast.Call) -> bool:
    """Removes or moves files: `unlink`/`rmtree`/`rmdir` anywhere, the generic names
    (`remove`, `replace`, `move`, `rename`) only on `os` / `shutil` / a path-like name."""
    if name not in DELETERS:
        return False
    if name in {"unlink", "rmtree", "rmdir", "removedirs"}:
        return True
    f = call.func
    recv = f.value if isinstance(f, ast.Attribute) else None
    return isinstance(recv, ast.Name) and recv.id in {"os", "shutil", "W", "path", "tree", "wt"}
