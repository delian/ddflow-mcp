"""The AST half of the interface census (ADP-census-v2): the call sites in ONE parsed module.

Five kinds of interface that are not Python imports: ``program`` (an external program ddflow
runs), ``protocol`` (a wire protocol spoken by hand), ``managed-file`` (a file another tool
owns), ``env`` (an environment variable read) and ``network`` (a network call or a hard-coded
URL host). Every site comes from the syntax tree, never from a text search: a docstring, a
comment or a string statement can not produce a hit. A site records names and locations only,
never a value (an environment variable's value, a URL's userinfo).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from itertools import pairwise

from . import harnessreg as HR

DYNAMIC = "<dynamic>"
ALL_ENV = "<all>"

# Dotted names (import aliases resolved) of the calls that start a process.
_SUBPROCESS = {
    f"subprocess.{n}" for n in ("run", "Popen", "call", "check_call", "check_output")
} | {"asyncio.create_subprocess_exec"}
_PROC = {
    f"ddflow.infra.proc.{n}" for n in ("run", "popen", "capture", "spawn_stdio", "replace_process")
}
_SHELL = {f"ddflow.infra.proc.{n}" for n in ("run_shell", "spawn_shell")} | {
    "os.system",
    "os.popen",
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
    "asyncio.create_subprocess_shell",
}
_GIT = {
    f"ddflow.infra.git.{n}" for n in ("run", "status_run", "paths", "files", "status", "unmerged")
}
_EXEC = {
    f"os.{n}"
    for n in (
        *("execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe"),
        *("spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp"),
        *("spawnvpe", "posix_spawn", "posix_spawnp"),
    )
}

MCP_METHODS = frozenset(
    {
        "tools/list",
        "tools/call",
        "resources/list",
        "resources/read",
        "prompts/list",
        "prompts/get",
        "notifications/initialized",
        "notifications/cancelled",
        "notifications/tools/list_changed",
        "notifications/resources/list_changed",
        "notifications/resources/updated",
        "notifications/prompts/list_changed",
        "notifications/progress",
        "notifications/message",
        "resources/templates/list",
        "resources/subscribe",
        "logging/setLevel",
        "completion/complete",
    }
)
# The programs ddflow knows by name (the program homes in `archifaces`). The agents are not
# listed here: `agent_clis` reads them from the harness descriptors.
#: An agent program with no descriptor: it is only ever driven over ACP, as a lane.
ACP_ONLY_AGENTS = ("vibe",)
TOOLS = ("roborev", "gitleaks", "pre-commit", "pip-audit", "uv", "uvx", "npm", "npx", "node")
PLATFORM = ("python", "ddflow", "ddflow-mcp", "docker", "systemd-run", "sh", "bash")


def agent_clis() -> tuple[str, ...]:
    """The agent programs: every harness descriptor's id. A function, not a table built on
    import: this module loads at every CLI start and the descriptors parse on first read."""
    return (*HR.ids(), *ACP_ONLY_AGENTS)


ACP_METHODS = frozenset(
    {
        "session/new",
        "session/load",
        "session/prompt",
        "session/update",
        "session/cancel",
        "session/request_permission",
        "fs/read_text_file",
        "fs/write_text_file",
        "authenticate",
    }
)

# A hand-rolled A2A (Agent2Agent) client is detected by its JSON-RPC method names
# (v1.0.0 core methods, `message/send` kept as its deprecated alias).
A2A_METHODS = frozenset(
    {
        "message/send",
        "message/stream",
        "tasks/get",
        "tasks/cancel",
        "tasks/resubscribe",
    }
)

# A hand-rolled client of a protocol that has no home yet names the protocol by its own
# string literal (`"openapi"` is the version key of every OpenAPI document; ``"utcp"`` is the
# Universal Tool Calling Protocol). They have no adapter today, so any use is a new interface
# that must go through one (see ``archifaces.PROTOCOL_HOMES``).
OPENAPI_LITERALS = frozenset({"openapi"})
UTCP_LITERALS = frozenset({"utcp"})

# A set literal that names exactly one protocol family (the `jsonrpc` literal is handled
# apart: it disambiguates to mcp/acp when those methods are present). The family sets are
# required to be pairwise disjoint: a method name in two families is a defect, not a tie
# to resolve silently, so an overlap is asserted against at import.
_LITERAL_FAMILY: dict[str, str] = {}
for _fam, _lits in (
    ("mcp", MCP_METHODS),
    ("acp", ACP_METHODS),
    ("a2a", A2A_METHODS),
    ("openapi", OPENAPI_LITERALS),
    ("utcp", UTCP_LITERALS),
):
    _overlap = set(_lits) & _LITERAL_FAMILY.keys()
    assert not _overlap, (
        f"protocol literals overlap {_fam} with an earlier family: {sorted(_overlap)}"
    )
    for _lit in _lits:
        _LITERAL_FAMILY[_lit] = _fam

# A file another tool owns: the literal's last path component (or the whole literal).
MANAGED_NAMES: dict[str, str] = {
    "settings.json": "settings.json",
    "settings.local.json": "settings.json",
    ".mcp.json": ".mcp.json",
    "opencode.json": "opencode.json",
    "hooks.toml": "hooks.toml",
    "config.toml": "config.toml",
    "AGENTS.md": "AGENTS.md",
    "CLAUDE.md": "CLAUDE.md",
    ".pre-commit-config.yaml": ".pre-commit-config.yaml",
}
GIT_HOOKS = ".git/hooks"

_NET_PREFIX = (
    ("urllib.request.", "http"),
    ("http.client.", "http"),
    ("http.server.", "socket"),
    ("socket.", "socket"),
    ("socketserver.", "socket"),
)
# The host of a URL literal, userinfo dropped (a regex, not urllib: that import would make this
# very module an importer of the http family it measures).
_LOOP_SPAWN = (".subprocess_exec", ".subprocess_shell")  # asyncio event-loop methods
_ENVIRON = ("os.environ", "os.environb")
_PATH_CTORS = {"pathlib.Path", "pathlib.PurePath", "pathlib.PurePosixPath", "os.path.join"}
_URL = re.compile(r"^https?://(?:[^/@?#\s]*@)?(\[[0-9a-f:.]+\]|[^/:?#\s{}\[\]]+)", re.IGNORECASE)
#: URL prefixes that only IDENTIFY a message format's schema namespace (the Adaptive Cards
#: schema the Teams channel embeds as its ``$schema`` field): not an endpoint this project
#: calls, so not a network dependence (bug B489298dd27). The exemption is the SCHEMA namespace
#: path, not the host -- any other path on the same host is still a network site.
_SCHEMA_NS_PREFIXES = ("http://adaptivecards.io/schemas/", "https://adaptivecards.io/schemas/")


@dataclass(frozen=True)
class Site:
    """One call site: where it is and what it names (``detail`` is a name, never a value)."""

    kind: str
    family: str
    path: str
    line: int
    module: str
    detail: str = ""


_ARG_KEYWORDS = ("args", "argv", "cmd", "command", "key", "path", "file", "host")


def _keyword_arg(node: ast.Call) -> ast.AST | None:
    """The first argument given by keyword (``subprocess.run(args=[...])``), when none is
    positional."""
    for k in node.keywords:
        if k.arg in _ARG_KEYWORDS:
            return k.value
    return None


def _base(program: str) -> str:
    return program.rsplit("/", 1)[-1] or program


class _Scan:
    def __init__(
        self, tree: ast.Module, module: str, path: str, is_init: bool, literals: bool = True
    ) -> None:
        self.tree, self.module, self.path = tree, module, path
        self.literals = literals
        # ONE traversal feeds every pass below (the walk dominates the scan's cost).
        self.parent: dict[int, ast.AST] = {}
        self.nodes: list[ast.AST] = []
        todo: list[ast.AST] = [tree]
        while todo:
            node = todo.pop()
            self.nodes.append(node)
            for kid in ast.iter_child_nodes(node):
                self.parent[id(kid)] = node
                todo.append(kid)
        self.alias: dict[str, str] = {}
        self._aliases(module.split("."), is_init)
        # string statements (docstrings, bare f-strings) are prose: they produce no hit
        self.prose: set[int] = set()
        for n in self.nodes:
            if isinstance(n, ast.Expr):
                if isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                    self.prose.add(id(n.value))
                elif isinstance(n.value, ast.JoinedStr):
                    self.prose.update(id(k) for k in ast.walk(n.value))
        self.consts: dict[str, set[str]] = {}
        self.argv0: dict[str, set[str]] = {}
        self._assignments()
        self.out: dict[tuple[str, str, int], Site] = {}

    # -- names ------------------------------------------------------------------------
    def _aliases(self, parts: list[str], is_init: bool) -> None:
        pkg = parts if is_init else parts[:-1]
        for n in self.nodes:
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.asname:
                        self.alias[a.asname] = a.name
                    else:
                        root = a.name.split(".", 1)[0]
                        self.alias[root] = root
            elif isinstance(n, ast.ImportFrom):
                base = n.module or ""
                if n.level:
                    keep = pkg[: max(len(pkg) - (n.level - 1), 0)]
                    base = ".".join([*keep, *([n.module] if n.module else [])])
                for a in n.names:
                    self.alias[a.asname or a.name] = f"{base}.{a.name}" if base else a.name

    def dotted(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.alias.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            head = self.dotted(node.value)
            return f"{head}.{node.attr}" if head else None
        return None

    def _assignments(self) -> None:
        """Names bound to a string (pass one), then to a list led by one (pass two), so the
        result never depends on the order the traversal happens to visit assignments."""
        binds = [
            (n.targets[0].id, n.value)
            for n in self.nodes
            if isinstance(n, ast.Assign)
            and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name)
        ] + [
            (n.target.id, n.value)  # `GIT: str = "git"`
            for n in self.nodes
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value
        ]
        for name, v in binds:
            lit = self._literal(v)
            if lit is not None:
                self.consts.setdefault(name, set()).add(lit)
        for name, v in binds:
            if isinstance(v, (ast.List, ast.Tuple)) and v.elts:
                self.argv0.setdefault(name, set()).add(self._head(v.elts[0]) or DYNAMIC)

    def _literal(self, v: ast.AST) -> str | None:
        """A string a name is bound to: a literal, or ``shutil.which("lit")`` (maybe ``or``)."""
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            return v.value
        if isinstance(v, ast.BoolOp):
            return next((s for s in map(self._literal, v.values) if s), None)
        if isinstance(v, ast.Call) and self.dotted(v.func) == "shutil.which" and v.args:
            a = v.args[0]
            return a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None
        return None

    def _head(self, e: ast.AST) -> str | None:
        """The program a list's first element names, or None when it is not static."""
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            return e.value
        if isinstance(e, ast.Name):
            vals = self.consts.get(e.id, set())
            return next(iter(vals)) if len(vals) == 1 else None
        if isinstance(e, ast.Call):
            return self._literal(e)
        if self.dotted(e) == "sys.executable":
            return "python"
        return None

    def programs_of(self, e: ast.AST, *, line: bool = False) -> list[str]:
        """The program one command argument names: the head of an argv list or of a name bound
        to one; a string is the program path itself (``os.execv``). A call that runs a shell
        (a shell function, ``shell=True``) runs ``sh`` and nothing more is inferred: the census
        does not parse shell grammar (any word-matching over- or under-reports), and the
        guard's job is only that every shell invocation sit behind an adapter. For ``shell=True``
        with an argv list the program named by the list is recorded too."""
        out: list[str] = ["sh"] if line else []
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            return out or ([_base(e.value)] if e.value else [DYNAMIC])
        got: str | None = None
        if isinstance(e, (ast.List, ast.Tuple)):
            got = self._head(e.elts[0]) if e.elts else None
        elif isinstance(e, ast.Name):
            both = e.id in self.argv0 and e.id in self.consts  # bound to a list AND a string
            vals = (
                set()
                if both
                else self.argv0.get(e.id) or (set() if line else self.consts.get(e.id, set()))
            )
            got = next(iter(vals)) if len(vals) == 1 and DYNAMIC not in vals else None
        elif isinstance(e, ast.Attribute):
            got = self._head(e)  # sys.executable
        if got:
            out.append(_base(got))
        elif not out:
            out.append(DYNAMIC)
        return out

    # -- recording --------------------------------------------------------------------
    def add(self, kind: str, family: str, node: ast.AST, detail: str = "") -> None:
        line = getattr(node, "lineno", 0)
        self.out.setdefault(
            (kind, family, line), Site(kind, family, self.path, line, self.module, detail)
        )

    # -- the five kinds ---------------------------------------------------------------
    def run(self) -> list[Site]:
        file_protocols = {
            n.value
            for n in self.nodes
            if isinstance(n, ast.Constant)
            and id(n) not in self.prose
            and n.value in MCP_METHODS | ACP_METHODS
        }
        for node in self.nodes:
            if isinstance(node, ast.Call):
                self._call(node)
                self._git_hooks_join(node)
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                if not self._is_div(self.parent.get(id(node))):
                    self._git_hooks_join(node)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if self.literals and id(node) not in self.prose:
                    self._literal_site(node, file_protocols)
            elif isinstance(node, ast.Attribute) and self.dotted(node) in _ENVIRON:
                self._environ(node)
            elif isinstance(node, ast.Name) and self.alias.get(node.id) in _ENVIRON:
                self._environ(node)
        return sorted(self.out.values(), key=lambda s: (s.path, s.line, s.kind, s.family))

    @staticmethod
    def _is_div(n: ast.AST | None) -> bool:
        return isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)

    def _call(self, node: ast.Call) -> None:
        name = self.dotted(node.func) or ""
        at = 1 if name.startswith("os.spawn") else 0  # os.spawnv(mode, path, args)
        arg = node.args[at] if len(node.args) > at else _keyword_arg(node)
        if name in _GIT:
            self.add("program", "git", node)
        elif name in _SUBPROCESS | _PROC | _EXEC | _SHELL or name.endswith(_LOOP_SPAWN):
            self._process(node, name, arg)
        elif name in ("shutil.which", "ddflow.infra.fsio.which"):
            lit = arg.value if isinstance(arg, ast.Constant) else None
            self.add("program", _base(lit) if isinstance(lit, str) else DYNAMIC, node)
        elif name in ("os.getenv", "os.getenvb"):
            self._env_arg(node, arg)
        self._network(node, name, arg)

    def _process(self, node: ast.Call, name: str, arg: ast.AST | None) -> None:
        shell = (
            name in _SHELL
            or name.endswith(".subprocess_shell")
            or any(
                k.arg == "shell" and not (isinstance(k.value, ast.Constant) and not k.value.value)
                for k in node.keywords  # any `shell=` that is not a literal falsy value
            )
        )
        for program in self.programs_of(arg, line=shell) if arg is not None else [DYNAMIC]:
            self.add("program", program, node)

    def _network(self, node: ast.Call, name: str, arg: ast.AST | None) -> None:
        family = next((f for prefix, f in _NET_PREFIX if name.startswith(prefix)), "")
        if not family:
            return
        self.add("network", family, node, name)
        if name.endswith("Connection") and isinstance(arg, ast.Constant) and arg.value:
            if isinstance(arg.value, str):  # a hard-coded endpoint
                self.add("network", f"host:{arg.value.lower()}", node, arg.value)

    def _env_arg(self, node: ast.AST, arg: ast.AST | None) -> None:
        if isinstance(arg, ast.Name) and len(self.consts.get(arg.id, ())) == 1:
            arg = ast.Constant(value=next(iter(self.consts[arg.id])))  # a name bound to a string
        if isinstance(arg, ast.Constant) and isinstance(arg.value, bytes):  # os.environb
            arg = ast.Constant(value=arg.value.decode("latin-1"))
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            self.add("env", arg.value, node)
        else:
            self.add("env", DYNAMIC, node)

    def _environ(self, node: ast.AST) -> None:
        up = self.parent.get(id(node))
        if isinstance(up, ast.Subscript) and up.value is node:
            aug = isinstance(self.parent.get(id(up)), ast.AugAssign)
            if isinstance(up.ctx, ast.Load) or aug:  # `os.environ[k] += x` reads k first
                self._env_arg(up, up.slice)
        elif isinstance(up, ast.Attribute) and up.value is node:
            call = self.parent.get(id(up))
            if (
                up.attr in ("get", "pop", "setdefault", "__getitem__")
                and isinstance(call, ast.Call)
                and call.func is up
            ):
                self._env_arg(call, call.args[0] if call.args else _keyword_arg(call))
            elif up.attr in ("copy", "items", "keys", "values", "__iter__") and (
                isinstance(call, ast.Call) and call.func is up
            ):
                self.add("env", ALL_ENV, node)
        elif (
            isinstance(up, ast.Compare)
            and node in up.comparators
            and isinstance(up.ops[0], (ast.In, ast.NotIn))
        ):
            self._env_arg(up, up.left)
        elif isinstance(up, ast.Attribute):
            return
        elif not (
            isinstance(up, (ast.Assign, ast.AugAssign)) and node in getattr(up, "targets", [])
        ):
            self.add("env", ALL_ENV, node)

    def _literal_site(self, node: ast.Constant, file_protocols: set[str]) -> None:
        v = node.value
        if v == "jsonrpc":
            fams = [
                f for f, ms in (("mcp", MCP_METHODS), ("acp", ACP_METHODS)) if file_protocols & ms
            ]
            for fam in fams or ["jsonrpc"]:
                self.add("protocol", fam, node, v)
        elif v in _LITERAL_FAMILY:
            self.add("protocol", _LITERAL_FAMILY[v], node, v)
        m = _URL.match(v)
        name = v.rsplit("/", 1)[-1]
        if not m and name in MANAGED_NAMES and "\n" not in v and " " not in v:
            self.add("managed-file", MANAGED_NAMES[name], node, name)
        hooks = v == GIT_HOOKS or v.startswith(GIT_HOOKS + "/") or "/" + GIT_HOOKS in v
        if hooks and not m:
            self.add("managed-file", GIT_HOOKS, node, GIT_HOOKS)
        if m and not v.lower().startswith(_SCHEMA_NS_PREFIXES):
            host = m.group(1).lower()
            self.add("network", f"host:{host}", node, host)

    def _path_call(self, e: ast.AST) -> bool:
        """A call that builds a path: ``Path(...)``, ``os.path.join(...)``, ``x.joinpath(...)``."""
        if not isinstance(e, ast.Call):
            return False
        name = self.dotted(e.func) or ""
        return name in _PATH_CTORS or (
            isinstance(e.func, ast.Attribute) and e.func.attr == "joinpath"
        )

    def _git_hooks_join(self, node: ast.AST) -> None:
        """``Path(".git") / "hooks"``, ``os.path.join(root, ".git", "hooks")`` and
        ``Path(".git").joinpath("hooks")``: the literal parts, in order."""
        leaves: list[str | None] = []

        def flat(e: ast.AST) -> None:
            if self._is_div(e):
                flat(e.left)  # type: ignore[attr-defined]
                flat(e.right)  # type: ignore[attr-defined]
            elif self._path_call(e):
                if isinstance(e.func, ast.Attribute) and e.func.attr == "joinpath":  # type: ignore[attr-defined]
                    if not isinstance(e.func.value, (ast.Name, ast.Attribute)):  # type: ignore[attr-defined]
                        flat(e.func.value)  # type: ignore[attr-defined]
                for a in e.args:  # type: ignore[attr-defined]
                    flat(a)
            else:
                leaves.append(e.value if isinstance(e, ast.Constant) else None)  # type: ignore[attr-defined]

        if isinstance(node, ast.Call) and not self._path_call(node):
            return
        flat(node)
        if any(a == ".git" and b == "hooks" for a, b in pairwise(leaves)):
            self.add("managed-file", GIT_HOOKS, node, GIT_HOOKS)


def sites_in(
    tree: ast.Module, module: str, path: str, *, is_init: bool = False, literals: bool = True
) -> list[Site]:
    """Every interface call site in the parsed module, sorted by path then line.
    ``literals=False`` leaves out the string-literal kinds (protocol methods, file names,
    URL hosts) for the census's own modules, whose pattern tables are data, not use."""
    return _Scan(tree, module, path, is_init, literals).run()
