"""Remote lane transport: bundle out, branch or bundle back, the host the only writer.

The ssh transport runs against a stub `ssh`: no network, but every shell line is executed.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import select
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ddflow.adapters.acp import OK, AcpStdioClient
from ddflow.config import Config
from ddflow.services.lane_driver import LaneStatus, Result, ok
from ddflow.services.swarm import remote as R
from ddflow.services.swarm import seedpack as S

FAKE_KEY = "-".join(["sk", "ant", "api03", "FAKEFAKEFAKE0123456789abcdefABCDEF0123456789zz"])
ENV = {f"GIT_{r}_{w}": "t" for r in ("AUTHOR", "COMMITTER") for w in ("NAME", "EMAIL")}


def git(cwd: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **ENV, "GIT_CONFIG_GLOBAL": "/dev/null"},
        check=True,
    )
    return p.stdout.strip()


def _commit(cwd: Path) -> None:
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", "c")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "host"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "src").mkdir()
    (r / "src" / "a.py").write_text("print('a')\n")
    (r / "README.md").write_text("hi\n")
    (r / ".ddflow" / "events").mkdir(parents=True)
    (r / ".ddflow" / "events" / "x.jsonl").write_text('{"event":"private"}\n')
    _commit(r)
    return r


def _seed() -> str:
    return S.render(S.build(S.SeedInput(item_id="X-1", item_title="t", item_body="b")))


def _out(repo, tmp_path, **kw):
    return R.build_outbound(repo, "main", "X-1", "l", _seed(), tmp_path / "o", **kw)


def test_outbound_has_bundle_seed_manifest(repo, tmp_path):
    out = R.build_outbound(repo, "main", "X-1", "lane-r", _seed(), tmp_path / "out")
    assert out.bundle.is_file() and out.seed.read_text() == _seed()
    m = json.loads(out.manifest.read_text())
    assert m["item"] == "X-1" and m["lane"] == "lane-r" and m["root"] == out.root
    assert m["sha256"] == out.sha256 and m["trust"] == "untrusted"
    assert git(repo, "rev-parse", "main") == out.base


def test_bundle_has_no_event_log_nor_history(repo, tmp_path):
    out = R.build_outbound(repo, "main", "X-1", "l", _seed(), tmp_path / "out")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--no-checkout", str(out.bundle), str(clone))
    git(clone, "fetch", "-q", str(out.bundle), f"{out.ref}:refs/heads/w")
    files = git(clone, "ls-tree", "-r", "--name-only", "w").split("\n")
    assert "src/a.py" in files
    assert not any(f.startswith(".ddflow/events") for f in files)
    assert git(clone, "rev-list", "--count", "w") == "1"
    assert out.base not in git(clone, "rev-list", "--all")
    assert ".ddflow" not in git(clone, "ls-tree", "--name-only", "w")


def test_secret_in_tracked_file_refuses_and_names_path(repo, tmp_path):
    (repo / "src" / "cfg.py").write_text(f'KEY = "{FAKE_KEY}"\n')
    _commit(repo)
    with pytest.raises(R.RemoteError) as e:
        _out(repo, tmp_path)
    assert "src/cfg.py" in str(e.value) and FAKE_KEY not in str(e.value)
    assert not (tmp_path / "o" / "base.bundle").exists()


def test_private_key_block_refuses(repo, tmp_path):
    (repo / "src" / "id").write_text("-----BEGIN OPENSSH " + "PRIVATE KEY-----\nabc\n")
    _commit(repo)
    for cfg in (None, Config()):
        with pytest.raises(R.RemoteError, match="src/id"):
            _out(repo, tmp_path, cfg=cfg)


def test_assignment_is_not_a_provider_key(repo, tmp_path):
    (repo / "src" / "d.py").write_text("tok" + 'en = "abcdefgh12345678abcdefgh"\n')
    _commit(repo)
    assert _out(repo, tmp_path, cfg=Config()).unscanned == ()


def test_operator_pattern_refuses_and_redacts(repo, tmp_path):
    cfg = Config()
    cfg.session.redact_extra = ["Project-Zebra"]
    out = R.build_outbound(repo, "main", "X-1", "l", "Project-Zebra", tmp_path / "o", cfg=cfg)
    assert "Zebra" not in out.seed.read_text()
    (repo / "src" / "d.py").write_text("Project-Zebra\n")
    _commit(repo)
    with pytest.raises(R.RemoteError, match=r"src/d\.py"):
        _out(repo, tmp_path, cfg=cfg)


def test_secret_path_can_be_excluded_by_knob(repo, tmp_path):
    (repo / "src" / "cfg.py").write_text(f'KEY = "{FAKE_KEY}"\n')
    _commit(repo)
    k = dataclasses.replace(R.Knobs(), exclude=(*R.Knobs().exclude, "src/cfg.py"))
    out = _out(repo, tmp_path, knobs=k)
    assert "cfg.py" not in git(repo, "ls-tree", "-r", "--name-only", out.root)


def test_seed_text_is_redacted_again(repo, tmp_path):
    out = R.build_outbound(
        repo, "main", "X-1", "l", f"note {FAKE_KEY} at 192.168.4.5", tmp_path / "o"
    )
    text = out.seed.read_text()
    assert FAKE_KEY not in text and "192.168.4.5" not in text


def test_relative_dest_is_the_callers(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = R.build_outbound(repo, "main", "X-1", "l", _seed(), Path("rel"))
    assert out.bundle == tmp_path / "rel" / R.BUNDLE and out.bundle.is_file()


def test_bundle_size_cap(repo, tmp_path):
    k = dataclasses.replace(R.Knobs(), bundle_max_bytes=10)
    with pytest.raises(R.RemoteError, match="bundle_max_bytes"):
        _out(repo, tmp_path, knobs=k)
    assert "refs/ddflow" not in git(repo, "for-each-ref")


def _worker(tmp_path, out, edits: dict[str, str]):
    branch = "work"
    w = tmp_path / "worker"
    git(tmp_path, "init", "-q", "-b", "x", str(w))
    git(w, "fetch", "-q", str(out.bundle), f"{out.ref}:refs/heads/{branch}")
    git(w, "checkout", "-q", branch)
    for p, t in edits.items():
        (w / p).parent.mkdir(parents=True, exist_ok=True)
        (w / p).write_text(t)
    _commit(w)
    return w


def test_return_from_pushed_branch_lands_on_host_branch(repo, tmp_path):
    out = _out(repo, tmp_path)
    w = _worker(tmp_path, out, {"src/a.py": "print('b')\n", "src/new.py": "x=1\n"})
    got = R.import_return(repo, w, out, globs=("src/*.py",), branch="work")
    assert got.ok and got.branch == "swarm/remote/X-1/l"
    assert sorted(got.changed) == ["src/a.py", "src/new.py"] and got.outside_globs == ()
    files = git(repo, "ls-tree", "-r", "--name-only", got.branch).split("\n")
    assert ".ddflow/events/x.jsonl" in files  # the host's own tree is restored
    assert git(repo, "rev-parse", f"{got.branch}^") == out.base
    assert git(repo, "show", f"{got.branch}:src/a.py") == "print('b')"
    assert git(repo, "rev-parse", "main") == out.base  # never merges
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert git(repo, "status", "--porcelain") == ""


def test_outside_globs_reported_not_hidden(repo, tmp_path):
    out = _out(repo, tmp_path)
    w = _worker(tmp_path, out, {"README.md": "changed\n"})
    got = R.import_return(repo, w, out, globs=("src/**",), branch="work")
    assert got.outside_globs == ("README.md",)


def test_touching_event_log_is_refused(repo, tmp_path):
    out = _out(repo, tmp_path)
    w = _worker(tmp_path, out, {".ddflow/events/evil.jsonl": '{"event":"approval"}\n'})
    got = R.import_return(repo, w, out, globs=("**",), branch="work")
    assert not got.ok and got.branch == "" and got.violations == (".ddflow/events/evil.jsonl",)
    assert git(repo, "for-each-ref", "refs/heads/swarm") == ""


def test_returned_secret_is_refused(repo, tmp_path):
    out = _out(repo, tmp_path)
    w = _worker(tmp_path, out, {"src/k.py": f"K='{FAKE_KEY}'\n"})
    got = R.import_return(repo, w, out, globs=("src/**",), branch="work")
    assert not got.ok and got.secret_paths == ("src/k.py",) and got.branch == ""


def test_unrelated_history_refused(repo, tmp_path):
    out = _out(repo, tmp_path)
    w = tmp_path / "other"
    git(tmp_path, "init", "-q", "-b", "x", str(w))
    (w / "f").write_text("1")
    _commit(w)
    with pytest.raises(R.RemoteError, match="descend"):
        R.import_return(repo, w, out, globs=("**",), branch="x")


def test_remote_sources_refused_no_network(repo, tmp_path):
    out = _out(repo, tmp_path)
    for src in ("ssh://example.invalid/r.git", "git@example.invalid:r.git"):
        with pytest.raises(R.RemoteError, match="local"):
            R.import_return(repo, src, out, globs=("**",), branch="x")


def test_return_size_and_commit_caps(repo, tmp_path):
    out = _out(repo, tmp_path)
    w = _worker(tmp_path, out, {"src/a.py": "print('d')\n"})
    ret = tmp_path / "ret.bundle"
    git(w, "bundle", "create", str(ret), "work")
    small = dataclasses.replace(R.Knobs(), return_max_bytes=10)
    with pytest.raises(R.RemoteError, match="return_max_bytes"):
        R.import_return(repo, ret, out, globs=("**",), knobs=small)
    few = dataclasses.replace(R.Knobs(), return_max_commits=0)
    with pytest.raises(R.RemoteError, match="return_max_commits"):
        R.import_return(repo, w, out, globs=("**",), branch="work", knobs=few)


def test_proposals_are_data_never_approvals():
    raw = json.dumps(
        [
            {"kind": "complete.force", "subject": "X-1", "reason": f"tests flaky {FAKE_KEY}"},
            {"kind": "x" * 500, "subject": "X-1", "reason": "r", "approved": True},
            "junk",
            {"subject": "no kind"},
        ]
    )
    got = R.parse_proposals(raw, R.Knobs(), None)
    assert [p.kind[:5] for p in got.proposals] == ["compl", "xxxxx"]
    assert all(p.trust == "unknown" for p in got.proposals)
    assert FAKE_KEY not in got.proposals[0].reason
    assert got.dropped == 2 and not any(hasattr(p, "approved") for p in got.proposals)
    assert 'trust="unknown"' in got.proposals[0].fenced()


def test_proposals_cap_and_bad_json():
    k = dataclasses.replace(R.Knobs(), proposals_max=1)
    two = json.dumps([{"kind": "a", "subject": "s", "reason": "r"}] * 3)
    got = R.parse_proposals(two, k, None)
    assert len(got.proposals) == 1 and got.dropped == 2
    assert R.parse_proposals("{not json", k, None).dropped == 1


def test_ddflow_config_and_nested_dotenv_stay_home(repo, tmp_path):
    (repo / ".ddflow" / "config.toml").write_text("[gate.ci]\n")
    (repo / "src" / ".env").write_text("A=1\n")
    git(repo, "add", "-f", "-A")
    git(repo, "commit", "-q", "-m", "cfg")
    out = _out(repo, tmp_path)
    tree = git(repo, "ls-tree", "-r", "--name-only", out.root)
    assert ".ddflow" not in tree and ".env" not in tree and "src/a.py" in tree


def test_key_inside_a_binary_file_is_found(repo, tmp_path):
    (repo / "src" / "blob.bin").write_bytes(b"\0\xff " + FAKE_KEY.encode() + b" \0")
    _commit(repo)
    with pytest.raises(R.RemoteError, match=r"src/blob\.bin"):
        _out(repo, tmp_path)


def test_file_too_big_to_scan_needs_consent_that_saw_it(repo, tmp_path):
    (repo / "src" / "big.txt").write_text("x" * 200)
    _commit(repo)
    k = dataclasses.replace(R.Knobs(), scan_max_blob_bytes=100)
    out = _out(repo, tmp_path, knobs=k)
    assert out.unscanned == ("src/big.txt",)
    assert json.loads(out.manifest.read_text())["unscanned"] == list(out.unscanned)
    t = Recording()
    with pytest.raises(R.RemoteError, match=r"src/big\.txt"):
        R.ship(t, out, _consent(out), now=60.0)
    assert t.sent == []
    R.ship(t, out, _consent(out, unscanned_seen=True), now=60.0)
    assert t.sent[0] == "base.bundle"


def test_scan_reads_a_tree_in_batches(repo, tmp_path):
    (repo / "src" / "b.py").write_text(f'K = "{FAKE_KEY}"\n')
    (repo / "src" / "c.py").write_text("y = 2\n" * 40)
    _commit(repo)
    k = dataclasses.replace(R.Knobs(), scan_batch_files=1)
    with pytest.raises(R.RemoteError, match=r"src/b\.py"):
        _out(repo, tmp_path, knobs=k)


def test_seed_commit_survives_gc_until_forget(repo, tmp_path):
    out = _out(repo, tmp_path)
    git(repo, "gc", "-q", "--prune=now")
    w = _worker(tmp_path, out, {"src/a.py": "print('g')\n"})
    ret = tmp_path / "ret.bundle"
    git(w, "bundle", "create", "-q", str(ret), f"{out.root}..work")  # needs the root on the host
    assert R.import_return(repo, ret, out, globs=("src/**",)).ok
    R.forget(repo, out)
    assert "refs/ddflow" not in git(repo, "for-each-ref")


@pytest.mark.parametrize("bad", ["..", "../..", ".", "a/../b", "x.lock", "-rf", "", "a b", "a."])
def test_unsafe_name_is_refused(bad):
    with pytest.raises(R.RemoteError, match="plain name"):
        R._safe(bad)


def _link_worker(tmp_path, out, mode: str):
    w = _worker(tmp_path, out, {"src/z.py": "z=1\n"})
    if mode == "120000":
        (w / "src" / "key").symlink_to("/home/someone/.ssh/id_ed25519")
        git(w, "add", "-A")
    else:
        git(w, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},vendor/sub")
    git(w, "commit", "-q", "-m", "link")
    return w


@pytest.mark.parametrize(("mode", "path"), [("120000", "src/key"), ("160000", "vendor/sub")])
def test_returned_link_is_refused(repo, tmp_path, mode, path):
    out = _out(repo, tmp_path)
    w = _link_worker(tmp_path, out, mode)
    got = R.import_return(repo, w, out, globs=("**",), branch="work")
    assert not got.ok and got.violations == (path,) and got.branch == ""


def test_returned_change_to_ddflow_config_is_refused(repo, tmp_path):
    out = _out(repo, tmp_path)
    w = _worker(tmp_path, out, {".ddflow/config.toml": "[gates]\nrequired=[]\n"})
    got = R.import_return(repo, w, out, globs=("**",), branch="work")
    assert not got.ok and got.violations == (".ddflow/config.toml",)


def test_lane_history_keeps_no_ref(repo, tmp_path):
    out = _out(repo, tmp_path)
    w = _worker(tmp_path, out, {"src/a.py": "print('h')\n", "src/k.py": f"K='{FAKE_KEY}'\n"})
    git(w, "rm", "-q", "src/k.py")
    git(w, "commit", "-q", "-m", "hide it")
    got = R.import_return(repo, w, out, globs=("**",), branch="work")
    assert got.ok and "refs/ddflow/remote/" not in git(repo, "for-each-ref")
    assert git(repo, "rev-list", "--count", f"{out.base}..{got.branch}") == "1"
    git(repo, "gc", "-q", "--prune=now")
    with pytest.raises(subprocess.CalledProcessError):
        git(repo, "cat-file", "-e", git(w, "rev-parse", "work~:src/k.py"))


def test_refused_return_keeps_no_ref(repo, tmp_path):
    out = _out(repo, tmp_path)
    w = _worker(tmp_path, out, {"src/k.py": f"K='{FAKE_KEY}'\n"})
    got = R.import_return(repo, w, out, globs=("**",), branch="work")
    assert not got.ok and got.tip
    assert "refs/ddflow/remote/" not in git(repo, "for-each-ref")
    assert git(repo, "for-each-ref", "refs/heads/swarm") == ""


def test_return_with_no_change_is_refused(repo, tmp_path):
    out = _out(repo, tmp_path)
    w = tmp_path / "idle"
    git(tmp_path, "init", "-q", "-b", "x", str(w))
    git(w, "fetch", "-q", str(out.bundle), f"{out.ref}:refs/heads/work")
    with pytest.raises(R.RemoteError, match="no change"):
        R.import_return(repo, w, out, globs=("**",), branch="work")
    assert "refs/ddflow/remote/" not in git(repo, "for-each-ref")


def test_returned_file_too_big_to_scan_is_refused(repo, tmp_path):
    out = _out(repo, tmp_path)
    w = _worker(tmp_path, out, {"src/big.txt": "x" * 200})
    k = dataclasses.replace(R.Knobs(), scan_max_blob_bytes=100)
    got = R.import_return(repo, w, out, globs=("**",), branch="work", knobs=k)
    assert not got.ok and got.unscanned == ("src/big.txt",) and got.branch == ""
    assert git(repo, "for-each-ref", "refs/heads/swarm") == ""


def test_pushed_branch_over_the_size_cap_is_refused(repo, tmp_path):
    out = _out(repo, tmp_path)
    w = _worker(tmp_path, out, {"src/a.py": os.urandom(999).hex()})
    k = dataclasses.replace(R.Knobs(), return_max_bytes=100)
    with pytest.raises(R.RemoteError, match="return_max_bytes"):
        R.import_return(repo, w, out, globs=("**",), branch="work", knobs=k)
    assert "refs/ddflow/remote/" not in git(repo, "for-each-ref")


@pytest.mark.parametrize("via", ["branch", "bundle"])
def test_malformed_tree_never_lands(repo, tmp_path, via):
    out = _out(repo, tmp_path)
    w = _worker(tmp_path, out, {"src/a.py": "print('m')\n"})
    blob = git(w, "rev-parse", "HEAD:src/a.py")
    p = subprocess.run(
        ["git", "-C", str(w), "hash-object", "-t", "tree", "-w", "--stdin", "--literally"],
        input=b"100644 .git\0" + bytes.fromhex(blob),
        capture_output=True,
        check=True,
    )
    bad = git(w, "commit-tree", p.stdout.decode().strip(), "-p", "work", "-m", "evil")
    git(w, "update-ref", "refs/heads/work", bad)
    src, kw = w, {"branch": "work"}
    if via == "bundle":
        src, kw = tmp_path / "ret.bundle", {}
        git(w, "bundle", "create", "-q", str(src), "work")
    with pytest.raises(R.RemoteError):
        R.import_return(repo, src, out, globs=("**",), **kw)
    assert git(repo, "for-each-ref", "refs/heads/swarm") == ""


def test_excluded_file_with_a_name_that_is_not_text_stays_home(repo, tmp_path):
    with open(os.fsencode(repo) + b"/.ddflow/\xff", "w") as fh:
        fh.write("private\n")
    _commit(repo)
    out = _out(repo, tmp_path)
    assert ".ddflow" not in git(repo, "ls-tree", "-r", "--name-only", out.root)
    w = _worker(tmp_path, out, {"src/a.py": "print('o')\n"})
    got = R.import_return(repo, w, out, globs=("**",), branch="work")
    assert got.ok and "377" in git(repo, "ls-tree", "--name-only", f"{got.branch}:.ddflow")


def test_forged_ref_name_in_a_bundle_is_refused(repo, tmp_path):
    out = _out(repo, tmp_path)
    w = _worker(tmp_path, out, {"src/a.py": "print('f')\n"})
    ret = tmp_path / "ret.bundle"
    git(w, "bundle", "create", "-q", str(ret), "work")
    ret.write_bytes(ret.read_bytes().replace(b"refs/heads/work", b"refs/heads/w:rk", 1))
    with pytest.raises(R.RemoteError, match="ref"):
        R.import_return(repo, ret, out, globs=("**",))


def test_module_cannot_write_the_log_or_answer_a_gate():
    """D-swarm-answers Q9: nothing here reaches the log, gates or approvals."""
    tree = ast.parse(Path(R.__file__).read_text())
    mods = [
        f"{getattr(n, 'module', '') or ''}.{a.name}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Import | ast.ImportFrom)
        for a in n.names
    ]
    for word in ("event", "gate", "approval", "lifecycle", "ddflow.api"):
        assert not [m for m in mods if word in m]


# -- the transport -----------------------------------------------------------------------

STUB = """#!/bin/sh
while [ "$1" != "--" ]; do shift; done
shift
host=$1
shift
[ "$host" = unreachable ] && { echo "ssh: connect failed" >&2; exit 255; }
[ "$host" = slow ] && exec sleep 30
[ "$host" = garbage ] && { printf 'running \\377\\n'; exit 0; }
cd "$STUB_HOME" || exit 254
exec sh -c "$*"
"""


@pytest.fixture
def ssh(tmp_path, monkeypatch):
    """A `Knobs` whose ssh is the stub, and the directory standing for the remote home."""
    home = tmp_path / "remote-home"
    home.mkdir()
    stub = tmp_path / "ssh-stub"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("STUB_HOME", str(home))
    return dataclasses.replace(R.Knobs(), ssh_argv=(str(stub),), ssh_timeout_s=20), home


def _until(fn, s: float = 15.0):
    """Poll ``fn`` until it is truthy, at most ``s`` seconds."""
    end = time.monotonic() + s
    while time.monotonic() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError("condition not reached in time")


def _alive(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def _ended(t, lane):
    st = t.observe(lane)
    return st if isinstance(st, LaneStatus) and st.state != "running" else None


def test_ssh_command_forwards_nothing_and_never_prompts(ssh):
    k, _ = ssh
    argv = R.SshTransport("user@host.example", k).command("true")
    head = argv[: argv.index("--")]
    assert "BatchMode=yes" in head and "StrictHostKeyChecking=yes" in head
    assert "ClearAllForwardings=yes" in head
    assert "PermitLocalCommand=no" in head and "-a" in head and "-x" in head
    assert argv[argv.index("--") + 1] == "user@host.example"


@pytest.mark.parametrize("host", ["-oProxyCommand=x", "a b", "", "$(x)"])
def test_host_that_is_no_plain_name_is_refused(host):
    with pytest.raises(R.RemoteError, match="host"):
        R.SshTransport(host)


@pytest.mark.parametrize("root", ["/abs", "a/../b", "", "a b;"])
def test_lanes_root_must_stay_under_the_remote_home(root):
    with pytest.raises(R.RemoteError, match="lanes_root"):
        R.SshTransport("h", dataclasses.replace(R.Knobs(), lanes_root=root))


def test_copy_in_and_out_round_trip(ssh, tmp_path):
    k, home = ssh
    t = R.SshTransport("h", k)
    src = tmp_path / "f.bin"
    src.write_bytes(bytes(range(256)) * 2000)
    assert t.copy_in("l", src, "base.bundle").ok
    assert (home / k.lanes_root / "l" / "base.bundle").read_bytes() == src.read_bytes()
    back = tmp_path / "back.bin"
    assert t.copy_out("l", "base.bundle", back, 10_000_000).ok
    assert back.read_bytes() == src.read_bytes()


def test_copy_out_over_the_cap_leaves_nothing(ssh, tmp_path):
    k, _ = ssh
    t = R.SshTransport("h", k)
    src = tmp_path / "f"
    src.write_bytes(b"x" * 500_000)
    assert t.copy_in("l", src, "r").ok
    back = tmp_path / "back"
    r = t.copy_out("l", "r", back, 1000)
    assert r.status == "failed" and "1000" in r.detail
    assert not back.exists() and not list(tmp_path.glob("back*"))
    assert t.copy_out("l", "nothing", back, 1000).status == "failed" and not back.exists()


@pytest.mark.parametrize("name", ["../x", "a/b", ".hidden", "-f", ""])
def test_file_names_are_plain(ssh, tmp_path, name):
    k, _ = ssh
    t = R.SshTransport("h", k)
    with pytest.raises(R.RemoteError, match="plain name"):
        t.copy_out("l", name, tmp_path / "x", 10)
    with pytest.raises(R.RemoteError, match="plain name"):
        t.copy_in("l", tmp_path, name)


def test_start_observe_exit_codes(ssh):
    k, _ = ssh
    t = R.SshTransport("h", k)
    assert t.start("good", ("sh", "-c", "exit 0")).ok
    assert t.start("bad", ("sh", "-c", "exit 3")).ok
    good, bad = _until(lambda: _ended(t, "good")), _until(lambda: _ended(t, "bad"))
    assert good.state == "exited" and good.exit_code == 0
    assert bad.state == "failed" and bad.exit_code == 3


def test_argv_reaches_the_lane_as_words_not_shell(ssh):
    k, home = ssh
    t = R.SshTransport("h", k)
    assert t.start("l", ("sh", "-c", 'printf %s "$1" > out', "x", "a b; touch pwned")).ok
    _until(lambda: _ended(t, "l"))
    d = home / k.lanes_root / "l"
    assert (d / "out").read_text() == "a b; touch pwned" and not (d / "pwned").exists()


def test_second_start_fails_and_signal_stops_the_group(ssh):
    k, home = ssh
    t = R.SshTransport("h", k)
    assert t.start("l", ("sh", "-c", "sleep 30 & echo $! > child; wait")).ok
    st = _until(lambda: (s := t.observe("l")).state == "running" and s)
    assert st.pid and t.start("l", ("true",)).status == "failed"
    child = home / k.lanes_root / "l" / "child"
    pid = int(_until(lambda: child.exists() and child.read_text()))
    assert t.signal("l").ok
    assert _until(lambda: _ended(t, "l")).state == "stopped"
    _until(lambda: not _alive(pid))


def test_recycled_pid_is_not_the_lane(ssh):
    k, home = ssh
    t = R.SshTransport("h", k)
    assert t.start("l", ("sleep", "30")).ok
    _until(lambda: t.observe("l").state == "running")
    d = home / k.lanes_root / "l"
    pid = int((d / "lane.pid").read_text())
    (d / "lane.stamp").write_text("x")
    assert t.observe("l").state == "failed" and t.signal("l").status == "failed"
    assert _alive(pid)
    os.killpg(pid, 9)


def test_ps_without_lstart_refuses_start(ssh, tmp_path, monkeypatch):
    k, home = ssh
    ps = tmp_path / "ps"
    ps.write_text("#!/bin/sh\nexit 0\n")
    ps.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    r = R.SshTransport("h", k).start("l", ("touch", "ran"))
    assert r.status == "failed" and "lstart" in r.detail
    assert not (home / k.lanes_root / "l").exists()


def test_only_named_signals_are_sent():
    with pytest.raises(R.RemoteError, match="signal"):
        R.SshTransport("h").signal("l", "TERM; rm -rf ~")


def test_cleanup_removes_only_that_lane_and_stops_it(ssh, tmp_path):
    k, home = ssh
    t = R.SshTransport("h", k)
    (tmp_path / "f").write_text("x")
    assert t.copy_in("keep", tmp_path / "f", "f").ok
    assert t.start("l", ("sh", "-c", "sleep 30 & echo $! > child; wait")).ok
    child = home / k.lanes_root / "l" / "child"
    pid = int(_until(lambda: child.exists() and child.read_text()))
    assert t.cleanup("l").ok
    _until(lambda: not _alive(pid))
    assert not (home / k.lanes_root / "l").exists()
    assert (home / k.lanes_root / "keep" / "f").exists()
    assert t.observe("l").status == "failed"
    with pytest.raises(R.RemoteError):
        t.cleanup("..")
    assert (home / k.lanes_root / "keep" / "f").exists()


def test_unreachable_host_is_unavailable(ssh, tmp_path):
    k, _ = ssh
    t = R.SshTransport("unreachable", k)
    (tmp_path / "f").write_text("x")
    for r in (
        t.start("l", ("true",)),
        t.observe("l"),
        t.signal("l"),
        t.copy_in("l", tmp_path / "f", "f"),
        t.copy_out("l", "f", tmp_path / "g", 10),
        t.cleanup("l"),
    ):
        assert isinstance(r, Result) and r.status == "unavailable"


def test_hanging_host_is_cut_by_the_timeouts(ssh, tmp_path):
    k, _ = ssh
    t = R.SshTransport("slow", dataclasses.replace(k, ssh_timeout_s=1, copy_timeout_s=1))
    begun = time.monotonic()
    assert t.observe("l").status == "unavailable"
    assert t.copy_out("l", "f", tmp_path / "g", 10).status == "unavailable"
    (tmp_path / "f").write_text("x")
    assert t.copy_in("l", tmp_path / "f", "f").status == "unavailable"
    assert time.monotonic() - begun < 20 and not (tmp_path / "g").exists()


def test_unreadable_answer_is_failed_and_missing_ssh_unavailable(ssh, tmp_path):
    k, _ = ssh
    assert R.SshTransport("garbage", k).observe("l").status == "failed"
    t = R.SshTransport("h", dataclasses.replace(k, ssh_argv=(str(tmp_path / "no-ssh"),)))
    assert t.observe("l").status == "unavailable"
    assert t.copy_out("l", "f", tmp_path / "g", 10).status == "unavailable"


# -- the whole trip ----------------------------------------------------------------------

AGENT = (
    "sh",
    "-c",
    "git config user.email l@x && git config user.name l && "
    "echo 'print(2)' > src/a.py && git add -A && git commit -q -m work && "
    """printf '[{"kind":"claim.force","subject":"X-1","reason":"please"}]' > ../proposals.json""",
)


def _consent(out, host="h", **kw):
    return R.Consent(
        **{"host": host, "item": out.item, "lane": out.lane, "expires_at": 100.0, **kw}
    )


class Recording:
    target = "h"

    def __init__(self):
        self.sent = []

    def copy_in(self, lane, local, name):
        self.sent.append(name)
        return ok()


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {"host": "other"},
        {"item": "X-2"},
        {"lane": "other"},
        {"expires_at": 50.0},
    ],
)
def test_nothing_leaves_without_consent(repo, tmp_path, bad):
    out = _out(repo, tmp_path)
    t = Recording()
    with pytest.raises(R.RemoteError, match="consent"):
        R.ship(t, out, None if bad is None else _consent(out, **bad), now=60.0)
    assert t.sent == []


def test_trip_out_and_back_over_ssh(repo, tmp_path, ssh):
    k, home = ssh
    out = _out(repo, tmp_path, knobs=k)
    t = R.SshTransport("h", k)
    assert R.ship(t, out, _consent(out), now=60.0).ok
    assert R.launch(t, out, AGENT).ok
    st = _until(lambda: _ended(t, "l"))
    assert st.state == "exited", (st, (home / k.lanes_root / "l" / "lane.log").read_text())
    got = R.collect(t, out, tmp_path / "in", knobs=k)
    assert got.result.ok and [p.kind for p in got.proposals.proposals] == ["claim.force"]
    back = R.import_return(repo, got.bundle, out, globs=("src/**",), knobs=k)
    assert back.ok and back.changed == ("src/a.py",) and back.commits == 1
    assert git(repo, "show", f"{back.branch}:src/a.py") == "print(2)"
    assert t.cleanup("l").ok


def test_lane_that_did_no_work_returns_nothing(repo, tmp_path, ssh):
    k, _ = ssh
    out = _out(repo, tmp_path, knobs=k)
    t = R.SshTransport("h", k)
    assert R.ship(t, out, _consent(out), now=60.0).ok
    assert R.launch(t, out, ("true",)).ok
    _until(lambda: _ended(t, "l"))
    got = R.collect(t, out, tmp_path / "in", knobs=k)
    assert got.bundle is None and got.result.status == "failed"
    assert got.asked.ok and got.proposals.proposals == ()


ACP_AGENT = """
import json, sys
req = json.loads(sys.stdin.readline())
print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {"protocolVersion": 1}}), flush=True)
sys.stdin.read()
"""


class _Pipe:
    """A `Channel` over a started process."""

    def __init__(self, argv, env):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def send(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()

    def receive(self, timeout_s):
        ready = select.select([self.p.stdout], [], [], timeout_s)[0]
        return (self.p.stdout.readline() or None) if ready else None

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)


def test_acp_speaks_through_the_ssh_stdio(repo, tmp_path, ssh):
    k, _ = ssh
    out = _out(repo, tmp_path, knobs=k)
    t = R.SshTransport("h", k)
    assert R.ship(t, out, _consent(out), now=60.0).ok
    argv = t.stdio_argv("l", R.lane_argv(out, (sys.executable, "-c", ACP_AGENT)))
    c = AcpStdioClient(_Pipe, list(argv))
    hello = c.connect(20)
    c.close()
    assert hello.status == OK
