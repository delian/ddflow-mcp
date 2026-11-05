"""Interface-census tests and ratchet (ADP-census-v2).

`ddflow/services/archifaces.py` extends the dependency census (`archdeps`) to the interfaces
that are not Python imports: external programs run, wire protocols spoken by hand, files owned
by other tools, environment variables read and network endpoints. Fixture trees below hold
one example of each kind; the ratchet must fail a NEW call site and a baselined one that grew.
"""

from __future__ import annotations

import time
from pathlib import Path

from ddflow.services import archdeps as A
from ddflow.services import archifaces as I
from ddflow.services import harnessreg as HR

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "arch_baselines" / "arch_interfaces.txt"


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    pkg = tmp_path / "ddflow"
    pkg.mkdir(exist_ok=True)
    for name, src in files.items():
        target = pkg / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src)
    return tmp_path


def _line(src: str, needle: str) -> int:
    for n, text in enumerate(src.splitlines(), 1):
        if needle in text:
            return n
    raise AssertionError(needle)


def _sites(census: I.InterfaceCensus, kind: str) -> set[tuple[str, str, int]]:
    return {(f.family, s.path, s.line) for f in census.families if f.kind == kind for s in f.sites}


PROGS = '''\
"""Docstring: subprocess.run(["docker"]) and shutil.which("npm") are prose, not calls."""
import shutil
import subprocess
from .infra import git as G
from .infra import proc as P


def f(repo, cmd_from_caller):
    subprocess.run(["gh", "pr", "list"])
    P.capture(["roborev", "wait"])
    exe = shutil.which("gitleaks")
    P.capture([exe, "detect"])
    G.run(repo, "status")
    argv = ["claude", "-p"]
    P.spawn_stdio(argv)
    P.capture(cmd_from_caller)
    # subprocess.run(["codex"]) in a comment is not a call
    return "uv"
'''

MCP = """\
def reply(i):
    return {"jsonrpc": "2.0", "id": i, "result": {}}


def dispatch(method):
    if method == "tools/call":
        return 1
"""
ACP = """\
def dispatch(method):
    if method == "session/new":
        return 1
"""
RAW = """\
import json


def send(out):
    out.write(json.dumps({"jsonrpc": "2.0", "method": "ping"}))
"""

FILES = '''\
"""Docstring naming .mcp.json and AGENTS.md is prose."""
from pathlib import Path


def f(root):
    a = Path(".mcp.json")
    b = root / "opencode.json"
    c = root / ".claude/settings.json"
    d = root / ".git/hooks/pre-commit"
    e = Path(".git") / "hooks"
    g = "AGENTS.md"
    h = ".pre-commit-config.yaml"
    i = "hooks.toml"
    j = "config.toml"
    k = "see settings.json for details"  # prose: not a file path
    return a, b, c, d, e, g, h, i, j, k  # AGENTS.md in a comment
'''

ENV = """\
import os
from os import environ as E


def f(name):
    a = os.environ.get("HOME_X")
    b = os.environ["A_B"]
    c = os.getenv("C_VAR")
    d = E.get("D_VAR")
    e = os.environ.get(name)
    f_ = "IN_VAR" in os.environ
    g = dict(os.environ)
    os.environ["WRITTEN"] = "1"
    return a, b, c, d, e, f_, g
"""

NET = '''\
"""Docstring with https://docs.example.org/ in it is prose."""
import http.client
import socket
import urllib.request


def f(u):
    urllib.request.urlopen(u)
    http.client.HTTPSConnection("api.example.com")
    s = socket.socket()
    return s, "https://hooks.example.net/v1/x", f"https://user:pw@feed.example.io/{u}"
'''

ALL = {
    "progs.py": PROGS,
    "proto_mcp.py": MCP,
    "proto_acp.py": ACP,
    "proto_raw.py": RAW,
    "files.py": FILES,
    "env.py": ENV,
    "net.py": NET,
}


def test_programs_resolved_from_argv_and_which(tmp_path: Path):
    c = I.scan(_tree(tmp_path, ALL))
    got = _sites(c, "program")
    p = "ddflow/progs.py"
    assert ("gh", p, _line(PROGS, 'subprocess.run(["gh"')) in got
    assert ("roborev", p, _line(PROGS, '["roborev"')) in got
    assert ("gitleaks", p, _line(PROGS, 'which("gitleaks")')) in got
    assert ("gitleaks", p, _line(PROGS, "[exe,")) in got  # exe resolved through its which()
    assert ("git", p, _line(PROGS, "G.run(")) in got
    assert ("claude", p, _line(PROGS, "P.spawn_stdio(argv)")) in got
    assert ("<dynamic>", p, _line(PROGS, "P.capture(cmd_from_caller)")) in got
    fams = {f for f, _p, _l in got}
    assert fams.isdisjoint({"docker", "npm", "codex", "uv"})  # prose, comment, return value


def test_program_homes_name_the_planned_task(tmp_path: Path):
    c = I.scan(_tree(tmp_path, ALL))
    home = {f.family: f.home for f in c.families if f.kind == "program"}
    assert home["git"] == "ADP-git"
    assert home["gh"] == "ADP-forge"
    assert home["roborev"] == "ADP-tools" and home["gitleaks"] == "ADP-tools"
    assert home["claude"] == "ADP-agent-lanes"


def test_every_descriptor_is_an_agent_program():
    """The agent programs are the descriptors, not a table beside them: one added there is
    homed with the others without touching the census."""
    for agent in (*HR.ids(), *I.ACP_ONLY_AGENTS):
        assert I.home_of("program", agent) == ("ADP-agent-lanes", ())
    assert I.home_of("program", "not-an-agent")[0] == "ADP-tools"


def test_protocols_by_method_and_jsonrpc_literal(tmp_path: Path):
    c = I.scan(_tree(tmp_path, ALL))
    got = _sites(c, "protocol")
    assert ("mcp", "ddflow/proto_mcp.py", _line(MCP, '"jsonrpc"')) in got
    assert ("mcp", "ddflow/proto_mcp.py", _line(MCP, '"tools/call"')) in got
    assert ("acp", "ddflow/proto_acp.py", _line(ACP, '"session/new"')) in got
    assert ("jsonrpc", "ddflow/proto_raw.py", _line(RAW, '"jsonrpc"')) in got
    home = {f.family: f.home for f in c.families if f.kind == "protocol"}
    assert home == {"mcp": "ADP-mcp", "acp": "ADP-acp", "jsonrpc": "ADP-lane-transport"}


def test_managed_files_exclude_prose_and_comments(tmp_path: Path):
    c = I.scan(_tree(tmp_path, ALL))
    got = {(f, line) for f, _p, line in _sites(c, "managed-file")}
    for fam, needle in (
        (".mcp.json", 'Path(".mcp.json")'),
        ("opencode.json", '"opencode.json"'),
        ("settings.json", ".claude/settings.json"),
        (".git/hooks", ".git/hooks/pre-commit"),
        (".git/hooks", 'Path(".git") / "hooks"'),
        ("AGENTS.md", 'g = "AGENTS.md"'),
        (".pre-commit-config.yaml", 'h = ".pre'),
        ("hooks.toml", 'i = "hooks.toml"'),
        ("config.toml", 'j = "config.toml"'),
    ):
        assert (fam, _line(FILES, needle)) in got, fam
    assert len(got) == 9  # the docstring, comment and prose lines add nothing
    assert {f.home for f in c.families if f.kind == "managed-file"} == {"ADP-managed-files"}


def test_env_reads_report_names_never_values(tmp_path: Path):
    c = I.scan(_tree(tmp_path, ALL))
    got = {(f, line) for f, _p, line in _sites(c, "env")}
    for fam, needle in (
        ("HOME_X", "HOME_X"),
        ("A_B", "A_B"),
        ("C_VAR", "C_VAR"),
        ("D_VAR", "D_VAR"),
        ("IN_VAR", "IN_VAR"),
        ("<dynamic>", "get(name)"),
        ("<all>", "dict(os.environ)"),
    ):
        assert (fam, _line(ENV, needle)) in got, fam
    assert "WRITTEN" not in {f for f, _l in got}  # a write is not a read
    assert {f.home for f in c.families if f.kind == "env"} == {"ADP-clock-env"}


def test_network_calls_and_hardcoded_hosts(tmp_path: Path):
    c = I.scan(_tree(tmp_path, ALL))
    got = {(f, line) for f, _p, line in _sites(c, "network")}
    assert ("http", _line(NET, "urlopen")) in got
    assert ("http", _line(NET, "HTTPSConnection")) in got
    assert ("socket", _line(NET, "socket.socket()")) in got
    hosts = {f for f, _l in got if f.startswith("host:")}
    assert hosts == {
        "host:api.example.com",
        "host:hooks.example.net",
        "host:feed.example.io",
    }
    assert all("pw" not in s.detail for f in c.families for s in f.sites)  # userinfo dropped


def test_unparsable_file_is_reported(tmp_path: Path):
    c = I.scan(
        _tree(tmp_path, {"ok.py": 'import os\nos.getenv("OK_VAR")\n', "bad.py": "def f(:\n"})
    )
    assert c.unparsed == ("ddflow.bad",)
    assert {f for f, _p, _l in _sites(c, "env")} == {"OK_VAR"}


def test_output_is_deterministic_and_sorted(tmp_path: Path):
    root = _tree(tmp_path, ALL)
    a, b = I.scan(root), I.scan(root)
    assert a == b
    for f in a.families:
        keys = [(s.path, s.line) for s in f.sites]
        assert keys == sorted(keys)
    order = [(I.KINDS.index(f.kind), f.family) for f in a.families]
    assert order == sorted(order)


def test_violations_exclude_home_modules(tmp_path: Path):
    c = I.scan(
        _tree(tmp_path, {"infra/git.py": 'import subprocess\nsubprocess.run(["git", "x"])\n'})
    )
    git = next(f for f in c.families if f.kind == "program" and f.family == "git")
    assert len(git.sites) == 1 and git.violations == ()


def test_ratchet_fails_a_new_call_site_and_a_grown_one(tmp_path: Path):
    root = _tree(tmp_path, ALL)
    base = I.parse_baseline(I.format_baseline(I.scan(root)))
    assert I.ratchet(I.scan(root), base) == ((), ())
    # a NEW call site in a NEW file
    (root / "ddflow" / "fresh.py").write_text('import os\nos.getenv("BRAND_NEW")\n')
    grew, shrank = I.ratchet(I.scan(root), base)
    assert any("BRAND_NEW" in g and "fresh.py" in g for g in grew) and shrank == ()
    (root / "ddflow" / "fresh.py").unlink()
    # a baselined site that GREW: a second read of the same variable in the same file
    (root / "ddflow" / "env.py").write_text(ENV + '\n\ndef g():\n    return os.getenv("C_VAR")\n')
    grew, _ = I.ratchet(I.scan(root), base)
    assert any("C_VAR" in g and "env.py" in g for g in grew)
    # a site removed: the baseline must be lowered
    (root / "ddflow" / "env.py").unlink()
    grew, shrank = I.ratchet(I.scan(root), base)
    assert grew == () and any("HOME_X" in s for s in shrank)


def test_baseline_format_round_trips_and_names_no_values(tmp_path: Path):
    c = I.scan(_tree(tmp_path, ALL))
    text = I.format_baseline(c)
    assert I.parse_baseline(text) == I.counts(c) and I.counts(c)
    odd = I.InterfaceCensus(
        (
            I.InterfaceFamily(
                "env", "X", "ADP-clock-env", (), (I.Site("env", "X", "ddflow/a\tb\nc.py", 3, "m"),)
            ),
        )
    )
    assert I.parse_baseline(I.format_baseline(odd)) == {("env", "X", "ddflow/a\tb\nc.py"): 1}
    assert "pw" not in text and "\n\n" not in text


def test_real_tree_ratchet_against_committed_baseline():
    census = I.scan(ROOT)
    assert census.unparsed == ()
    base = I.parse_baseline(BASELINE.read_text())
    grew, shrank = I.ratchet(census, base)
    assert not grew, f"NEW or grown interface call site(s): {grew}"
    assert not shrank, f"lower the baseline (ddflow archdeps --interfaces all --baseline): {shrank}"
    assert {f.kind for f in census.families} == set(I.KINDS)


def test_scan_is_cheap_enough_for_the_suite():
    started = time.process_time()  # CPU time: immune to a preempted runner
    I.scan(ROOT)
    assert time.process_time() - started < 15


def test_api_view_carries_interfaces_and_baseline_text():
    from ddflow.api import arch_deps as API

    out = API.archdeps(ROOT, interfaces="all")
    assert out.exit == 0
    rows = out.data["interfaces"]
    assert {"kind", "family", "home", "sites", "violations"} <= set(rows[0])
    assert {"path", "line"} <= set(rows[0]["sites"][0])
    assert "program" in out.data["text"]
    only = API.archdeps(ROOT, interfaces="env")
    assert {r["kind"] for r in only.data["interfaces"]} == {"env"}
    bad = API.archdeps(ROOT, interfaces="nope")
    assert bad.exit != 0
    text = API.archdeps(ROOT, interfaces="all", baseline=True).data["text"]
    assert text == I.format_baseline(I.scan(ROOT))
    assert "interfaces" not in API.archdeps(ROOT).data
    # the committed baseline is applied by the view: the real tree is inside it
    ratchet = out.data["ratchet"]
    assert ratchet == {"grew": [], "shrank": [], "baseline": I.BASELINE_PATH}


def test_the_census_does_not_count_its_own_pattern_tables(tmp_path: Path):
    # the scanner's tables name "AGENTS.md", "tools/call", ...: data, not call sites; but a
    # real call inside a census module (an env read here) is still counted
    src = 'import os\nX = "AGENTS.md"\nY = "tools/call"\nZ = os.getenv("REAL")\n'
    root = _tree(tmp_path, {"services/archifaces_scan.py": src, "services/real.py": src})
    c = I.scan(root)
    mine = {(f.kind, f.family) for f in c.families for s in f.sites if "scan" in s.path}
    assert mine == {("env", "REAL")}
    assert {f.kind for f in c.families for s in f.sites if "real" in s.path} == {
        "env",
        "managed-file",
        "protocol",
    }
    live = I.scan(ROOT)
    assert not [s for f in live.families for s in f.sites if s.module in I.SELF_MODULES]


REVIEW = """\
import os
import subprocess
from pathlib import Path


def f(logger, u):
    logger.debug(".git", "hooks")
    h = Path(".git", "hooks")
    url = "https://example.com/settings.json"
    os.system("git status && pre-commit run")
    os.environ["PATH"] += ":x"
    return h, url
"""
DOC_ONLY = """\
"tools/list"


def f():
    return {"jsonrpc": "2.0"}
"""


def test_review_findings_stay_fixed(tmp_path: Path):
    root = _tree(tmp_path, {"rev.py": REVIEW, "doc.py": DOC_ONLY})
    c = I.scan(root)
    # only a path constructor makes `.git`, `hooks` a managed file; a logger call does not
    hooks = {s.line for f in c.families if f.family == ".git/hooks" for s in f.sites}
    assert hooks == {_line(REVIEW, 'Path(".git", "hooks")')}
    # a URL is a host, never a managed file
    assert {f.family for f in c.families if f.kind == "managed-file"} == {".git/hooks"}
    assert "host:example.com" in {f.family for f in c.families}
    # a shell LINE runs the shell: one `sh` site, nothing is inferred from the line
    progs = {f.family for f in c.families if f.kind == "program"}
    assert progs == {"sh"}
    # `os.environ[k] += x` reads k
    assert ("PATH", "ddflow/rev.py", _line(REVIEW, "+=")) in _sites(c, "env")
    # a bare string statement does not decide which protocol a jsonrpc literal belongs to
    assert {f for f, p, _l in _sites(c, "protocol") if p.endswith("doc.py")} == {"jsonrpc"}


MORE = """\
import os
from pathlib import Path


def f(p):
    a = "https://example.com/.git/hooks/pre-commit"
    b = Path(".git").joinpath("hooks")
    c = p.joinpath(".git", p.joinpath("hooks"))
    os.system("git commit -m 'fix && bug' && grep -E 'a|b' x")
    return a, b, c
"""


def test_second_round_review_findings_stay_fixed(tmp_path: Path):
    c = I.scan(_tree(tmp_path, {"more.py": MORE}))
    hooks = {s.line for f in c.families if f.family == ".git/hooks" for s in f.sites}
    assert hooks == {_line(MORE, 'Path(".git").joinpath'), _line(MORE, "c = p.joinpath")}
    assert {f.family for f in c.families if f.kind == "program"} == {"sh"}


def test_the_census_modules_import_no_os_family():
    # whole-module self exclusion is safe only while the census modules touch nothing external
    fams = {f.family: set(f.importers) for f in A.scan(ROOT).families}
    for family in ("subprocess", "http", "shutil", "clock-env", "sqlite3", "fsio"):
        assert not fams[family] & set(I.SELF_MODULES), family


def test_a_string_is_a_shell_line_only_for_shell_calls(tmp_path: Path):
    src = (
        "import os\nimport subprocess\n"
        'os.execv("/tmp/foo|bar", [])\n'
        'subprocess.run("git status & uv sync", shell=True)\n'
    )
    c = I.scan(_tree(tmp_path, {"sh.py": src}))
    assert {f.family for f in c.families if f.kind == "program"} == {"foo|bar", "sh"}


THIRD = """\
import os
import shutil
import subprocess


def f():
    f"opencode.json"
    os.system("git status\\npre-commit run")
    subprocess.run(args=["gh", "pr"])
    return os.getenv(key="KW_VAR"), shutil.which(cmd="uv")
"""


def test_third_round_review_findings_stay_fixed(tmp_path: Path):
    c = I.scan(_tree(tmp_path, {"third.py": THIRD}))
    assert {f.family for f in c.families if f.kind == "program"} == {
        "sh",
        "gh",
        "uv",
    }
    assert {f.family for f in c.families if f.kind == "env"} == {"KW_VAR"}
    assert not [f for f in c.families if f.kind == "managed-file"]  # a bare f-string is prose
    assert I.parse_baseline(" # indented comment\n\n") == {}


def test_network_home_is_the_import_census_http_home(tmp_path: Path):
    src = 'import urllib.request\nurllib.request.urlopen("x")\n'
    c = I.scan(_tree(tmp_path, {"adapters/http.py": src, "services/other.py": src}))
    http = next(f for f in c.families if f.kind == "network" and f.family == "http")
    assert {s.module for s in http.violations} == {"ddflow.services.other"}
    assert http.home_modules == A.STDLIB_HOMES["http"]


def test_mcp_adapter_and_its_fake_are_the_protocol_home(tmp_path: Path):
    src = 'def f():\n    return "tools/call"\n'
    files = {"adapters/mcp.py": src, "adapters/fakes/mcp.py": src, "surfaces/x.py": src}
    c = I.scan(_tree(tmp_path, files))
    mcp = next(f for f in c.families if f.kind == "protocol" and f.family == "mcp")
    assert {s.module for s in mcp.violations} == {"ddflow.surfaces.x"}


def test_fourth_round_findings_stay_fixed(tmp_path: Path):
    src = (
        "import os\nimport subprocess\n"
        'PROG = "roborev"\n'
        "subprocess.run(PROG)\n"
        'os.environ.get(key="KW_ENV")\n'
        'subprocess.run(["echo", "hi"], shell=True)\n'
    )
    c = I.scan(_tree(tmp_path, {"f.py": src}))
    assert {f.family for f in c.families if f.kind == "program"} == {"roborev", "sh", "echo"}
    assert {f.family for f in c.families if f.kind == "env"} == {"KW_ENV"}


def test_fifth_round_findings_stay_fixed(tmp_path: Path):
    src = (
        "import os\nimport subprocess\nimport sys\n"
        'GIT: str = "git"\n'
        'KEY = "KEYED"\n'
        "ARGV = ['uv', 'sync']\n"
        "subprocess.run(GIT)\n"
        "os.execv(sys.executable, [])\n"
        'subprocess.getoutput("x")\n'
        "subprocess.run(ARGV, shell=True)\n"
        "os.getenv(KEY)\n"
    )
    c = I.scan(_tree(tmp_path, {"g.py": src}))
    assert {f.family for f in c.families if f.kind == "program"} == {"git", "python", "sh", "uv"}
    assert {f.family for f in c.families if f.kind == "env"} == {"KEYED"}


def test_sixth_round_findings_stay_fixed(tmp_path: Path):
    src = (
        "import http.client\nimport os\nimport subprocess\n"
        'subprocess.getstatusoutput("x")\n'
        'os.execl("/usr/bin/git", "git")\n'
        'http.client.HTTPConnection(host="kw.example.com")\n'
        "f = os.environ.items\n"
        "ARGV = ['uv']\nARGV = 'tool'\nsubprocess.run(ARGV)\n"
    )
    c = I.scan(_tree(tmp_path, {"s.py": src}))
    assert {f.family for f in c.families if f.kind == "program"} == {"sh", "git", "<dynamic>"}
    assert "host:kw.example.com" in {f.family for f in c.families if f.kind == "network"}
    assert not [f for f in c.families if f.kind == "env"]  # a bound method reads nothing


def test_a_file_that_stops_parsing_is_not_a_shrink(tmp_path: Path):
    root = _tree(tmp_path, {"a.py": 'import os\nos.getenv("A_VAR")\n'})
    base = I.parse_baseline(I.format_baseline(I.scan(root)))
    (root / "ddflow" / "a.py").write_text("def f(:\n")
    census = I.scan(root)
    assert census.unparsed == ("ddflow.a",)
    grew, shrank = I.ratchet(census, base)  # not a shrink; an unknown fails closed
    assert shrank == () and grew == ("unparsed ddflow.a: the census cannot read it",)


def test_seventh_round_findings_stay_fixed(tmp_path: Path):
    src = (
        "import os\nimport subprocess\n"
        'os.spawnv(os.P_WAIT, "/usr/bin/git", ["git"])\n'
        'subprocess.run("git status", shell=1)\n'
        'def register(fn, k): pass\nregister("SECRET", os.environ.get)\n'
    )
    c = I.scan(_tree(tmp_path, {"h.py": src}))
    assert {f.family for f in c.families if f.kind == "program"} == {"git", "sh"}
    assert not [f for f in c.families if f.kind == "env"]  # a passed bound method reads nothing


def test_baseline_refused_while_a_file_does_not_parse(tmp_path: Path):
    from ddflow.api import arch_deps as API

    (tmp_path / "ddflow").mkdir()
    (tmp_path / "ddflow" / "bad.py").write_text("def f(:\n")
    out = API.archdeps(tmp_path, interfaces="all", baseline=True)
    assert out.exit != 0


def test_the_ratchet_shown_matches_the_kind_shown(tmp_path: Path):
    from ddflow.api import arch_deps as API

    root = _tree(tmp_path, {"m.py": 'import os\nos.getenv("E_VAR")\nos.system("x")\n'})
    (root / "tests" / "arch_baselines").mkdir(parents=True)
    (root / I.BASELINE_PATH).write_text("")
    every = API.archdeps(root, interfaces="all").data["ratchet"]["grew"]
    only = API.archdeps(root, interfaces="env").data["ratchet"]["grew"]
    assert len(every) == 2 and len(only) == 1 and only[0].startswith("env ")


def test_eighth_round_findings_stay_fixed(tmp_path: Path):
    import pytest

    from ddflow.api import arch_deps as API

    src = (
        "import asyncio\nimport os\n"
        'asyncio.create_subprocess_exec("git", "status")\n'
        'asyncio.create_subprocess_shell("x")\n'
        'os.environb[b"BYTES_VAR"]\n'
        'M = "notifications/resources/list_changed"\n'
        'U = "http://[::1]:8080/api"\n'
    )
    c = I.scan(_tree(tmp_path, {"e8.py": src}))
    assert {f.family for f in c.families if f.kind == "program"} == {"git", "sh"}
    assert {f.family for f in c.families if f.kind == "env"} == {"BYTES_VAR"}
    assert {f.family for f in c.families if f.kind == "protocol"} == {"mcp"}
    assert "host:[::1]" in {f.family for f in c.families if f.kind == "network"}
    # an unparsed file: no baseline can be written, and a kind-scoped ratchet still shows it
    (tmp_path / "ddflow" / "bad.py").write_text("def f(:\n")
    broken = I.scan(tmp_path)
    with pytest.raises(ValueError):
        I.format_baseline(broken)
    (tmp_path / "tests" / "arch_baselines").mkdir(parents=True)
    (tmp_path / I.BASELINE_PATH).write_text("")
    grew = API.archdeps(tmp_path, interfaces="env").data["ratchet"]["grew"]
    assert any(g.startswith("unparsed ") for g in grew)


def test_ninth_round_findings_stay_fixed(tmp_path: Path):
    home = tmp_path / "__pycache__" / "repo"  # an ancestor named __pycache__ hides nothing
    home.mkdir(parents=True)
    src = "def f(loop):\n    loop.subprocess_exec(object, 'git', 'status')\n    loop.subprocess_shell(object, 'x')\n"
    c = I.scan(_tree(home, {"loop.py": src}))
    assert {f.family for f in c.families if f.kind == "program"} == {"sh", "<dynamic>"}
