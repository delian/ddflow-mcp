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
    ("services/review/engine.py", "ensure_running"),  # the reviewer subprocess it spawned
    ("infra/proc.py", "stop_group"),
    ("infra/proc.py", "_shell_group"),
    ("services/review/transport.py", "_abort"),
    ("services/companions/probe.py", "_stop"),  # the install subprocess it spawned
    (
        "services/lane_headless.py",
        "interrupt",
    ),  # SIGINT to the process group the lane launched (process-headless)
    ("services/lane_headless.py", "stop"),  # SIGTERM then the final group kill, then reap
    (
        "services/ci/scratch.py",
        "free_then_die",
    ),  # SIG_DFL, then the handled signal re-raised on itself
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


def _bound_modules(tree: ast.AST) -> dict[str, str]:
    # name -> the top-level module the name is bound to, from every import form. `import
    # os.path` binds the top-level name `os` (the `path` attribute rides on it), and `from
    # platform import system` binds `system` to the `platform` module, so `os.system()` (the
    # process launcher) is told apart from `platform.system()` / `system()` (the OS-name
    # read) by the module it reads, not the receiver's spelling.
    bound: dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                bound[a.asname or a.name.split(".")[0]] = a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom):
            top = n.module.split(".")[0] if n.module else ""
            for a in n.names:
                bound[a.asname or a.name] = top
    return bound


def _call_name(f: ast.expr, bound: dict[str, str], aliases: dict[str, str]) -> str:
    """`os.system` is a process launcher; `platform.system` reports the OS name. Only a
    receiver bound to `platform` by an import is the read -- an `os` receiver stays the
    launcher, so an aliased launcher (or `import os as platform`) is still flagged."""
    if isinstance(f, ast.Attribute):
        name = f.attr
        if name == "system":
            recv = f.value.id if isinstance(f.value, ast.Name) else ""
            name = "platform.system" if bound.get(recv) == "platform" else "system"
    else:
        name = getattr(f, "id", "")
        if name == "system" and bound.get(name) == "platform":
            name = "platform.system"
    return aliases.get(name, name)  # `from os import killpg as _kp`; `_kp(...)`


def _calls(path: Path):
    tree = ast.parse(path.read_text())
    aliases = {
        a.asname: a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module in {"os", "signal", "subprocess"}
        for a in n.names
        if a.asname and a.name in KILLERS
    }
    bound = _bound_modules(tree)
    stack: list[str] = []

    def walk(node):
        named = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        if named:
            stack.append(node.name)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "getattr":
            arg = node.args[1] if len(node.args) > 1 else None
            if isinstance(arg, ast.Constant) and arg.value in KILLERS:  # getattr(os, "kill")
                yield str(arg.value), node, (stack[-1] if stack else "")
        if isinstance(node, ast.Call):
            name = _call_name(node.func, bound, aliases)
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


def _kill_names(source: str, tmp_path: Path) -> set[str]:
    """The killer names `_calls` yields for a snippet: an OS-name read is told apart from a
    process launcher by what the receiver is bound to, not its spelling."""
    snippet = tmp_path / "snippet.py"
    snippet.write_text(source)
    return {name for name, _c, _fn in _calls(snippet) if name in KILLERS}


def test_platform_system_is_the_os_name_read_not_a_killer(tmp_path: Path):
    assert _kill_names("import platform\nplatform.system()\n", tmp_path) == set()


def test_os_system_is_a_killer_even_when_imported_as_platform(tmp_path: Path):
    assert _kill_names("import os as platform\nplatform.system('pkill foo')\n", tmp_path) == {
        "system"
    }


def test_os_system_stays_a_killer_after_import_os_path(tmp_path: Path):
    assert _kill_names("import os.path\nos.system('pkill foo')\n", tmp_path) == {"system"}


def test_from_platform_import_system_is_the_read(tmp_path: Path):
    assert _kill_names("from platform import system\nsystem()\n", tmp_path) == set()


def test_from_os_import_system_is_the_launcher(tmp_path: Path):
    assert _kill_names("from os import system\nsystem('pkill foo')\n", tmp_path) == {"system"}


def test_from_platform_import_system_as_uname_is_not_a_killer(tmp_path: Path):
    assert _kill_names("from platform import system as uname\nuname()\n", tmp_path) == set()


def test_from_os_star_import_bare_system_is_the_launcher(tmp_path: Path):
    assert _kill_names("from os import *\nsystem('pkill foo')\n", tmp_path) == {"system"}


def test_assigned_os_system_is_the_launcher(tmp_path: Path):
    assert _kill_names("import os\nsystem = os.system\nsystem('pkill foo')\n", tmp_path) == {
        "system"
    }


def test_assigned_platform_alias_os_system_is_the_launcher(tmp_path: Path):
    assert _kill_names("import os\nplatform = os\nplatform.system('pkill foo')\n", tmp_path) == {
        "system"
    }
