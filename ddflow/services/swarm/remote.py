"""Remote lane transport (SW-remote-bundle), v1: the host is the one writer.

Out: ``build_outbound`` turns the base commit into ONE parentless commit whose tree leaves
out ``Knobs.exclude``, so no history, event log or once-committed secret travels; it refuses a
tree with a file matching a secret pattern, bundles the commit and writes the redacted seed
pack and a manifest. ``ship`` sends that over a `LaneTransport`, only under a `Consent`.

In: ``import_return`` reads a pushed branch or a return bundle from a LOCAL path (``collect``
fetches the bundle and proposals over the transport). It checks the work descends from the
seed commit, refuses an excluded path, a link or a secret, reports what lies outside the
item's globs, and lands one commit on a host branch whose parent is the original base. It
never checks out, merges, approves, skips a gate or writes an event: the caller records, the
host runs the gates, and a remote claim that tests passed is data (D-swarm-answers Q9).

`LaneTransport` is the verbs ADP-lane-transport will move to a port. `SshTransport` is the only
implementation; ``stdio_argv`` carries ACP over the same ssh. No relay exists (Q8). ``ddflow
serve`` and its scoped token are not here: the server does not exist yet (MP-transport) and
the token is SW-remote-trust's.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re
import shlex
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from ...config import Config, SessionConfig
from ...core.digest import content_digest
from ...core.globs import inside
from ...core.outcome import FAIL
from ...core.redact import mask_secrets
from ...core.secret_shapes import session_secret_patterns, shape_hit
from ...infra import fsio
from ...infra import git as G
from ...infra import proc as P
from ..lane_driver import LaneStatus, Result, failed, ok, unavailable
from ..redact_report import redactor
from . import seedpack as S

_DATE = "1970-01-01T00:00:00Z"
_BLOB = ("100644", "100755", "120000")
_LINKS = ("120000", "160000")  # a returned symlink or gitlink reaches outside the tree
_REF = re.compile(r"refs/[A-Za-z0-9._/-]+")
_HOST = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@%:\[\]-]*")
_FILE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
_ROOT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]*")
_SIGNALS = ("TERM", "INT", "HUP", "KILL")
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----\s+[A-Za-z0-9+/]")
_LOOSE = frozenset(session_secret_patterns(SessionConfig(), builtin=False))
_SSH_LOST = 255  # ssh's own failure, as against the remote command's exit code
_CHUNK = 65536
BUNDLE, MANIFEST, RETURN, PROPOSALS = (
    "base.bundle",
    "manifest.json",
    "return.bundle",
    "proposals.json",
)


class RemoteError(RuntimeError):
    exit_code = FAIL


@dataclass(frozen=True)
class Knobs:
    """SW-remote-config feeds these from ``[swarm]``."""

    exclude: tuple[str, ...] = (".ddflow", ".env", "**/.env")
    bundle_max_bytes: int = 104857600
    return_max_bytes: int = 52428800
    return_max_commits: int = 200
    git_timeout_s: int = 120
    scan_max_blob_bytes: int = 1048576
    scan_batch_files: int = 64  # at most this many blobs of scan_max_blob_bytes in memory
    branch_prefix: str = "swarm/remote/"
    seed_name: str = "SEED.md"
    proposals_max: int = 5
    proposal_chars: int = 400
    proposals_max_bytes: int = 65536
    ssh_argv: tuple[str, ...] = ("ssh",)
    ssh_timeout_s: int = 60
    ssh_connect_timeout_s: int = 15
    copy_timeout_s: int = 600
    lanes_root: str = "ddflow-lanes"  # under the remote home
    detail_chars: int = 200


DEFAULT = Knobs()


@dataclass(frozen=True)
class Outbound:
    dir: Path
    bundle: Path
    seed: Path
    manifest: Path
    base: str  # the host commit the lane was seeded from
    root: str  # the parentless commit it receives
    ref: str  # the ref the bundle carries
    sha256: str
    size: int
    unscanned: tuple[str, ...]
    item: str
    lane: str


@dataclass(frozen=True)
class Returned:
    ok: bool
    branch: str  # the host branch holding the work; "" when refused
    sha: str
    tip: str  # the lane's own tip, for the record: no ref keeps it
    changed: tuple[str, ...]
    outside_globs: tuple[str, ...]
    violations: tuple[str, ...]  # excluded paths and links the lane touched
    secret_paths: tuple[str, ...]
    commits: int
    unscanned: tuple[str, ...] = ()  # changed files too big to scan: refused too


def _git(repo: Path, k: Knobs, *args: str, **kw) -> G.GitResult:
    r = G.run(repo, *args, timeout=k.git_timeout_s, **kw)
    if not r.ok:
        raise RemoteError(f"git {args[0]} failed: {r.err or r.out}")
    return r


def _safe(s: str) -> str:
    """``s``, if one harmless path and ref component. Never rewritten: that could give two
    lanes one ref."""
    if not _FILE.fullmatch(s) or ".." in s or s.endswith((".lock", ".")):
        raise RemoteError(f"{s!r} is not one plain name")
    return s


def _excluded(path: str, k: Knobs) -> bool:
    return any(inside(path, e) for e in k.exclude)


def _tree_entries(repo: Path, k: Knobs, rev: str) -> list[tuple[str, str, str, int]]:
    """(mode, sha, path, size) of every file of ``rev``."""
    raw = _git(repo, k, "ls-tree", "-r", "-l", "-z", rev, z=True).out_bytes or b""
    out = []
    for rec in raw.split(b"\0"):
        if not rec:
            continue
        meta, _, path = rec.partition(b"\t")
        mode, kind, sha, size = meta.split()
        count = int(size) if kind == b"blob" else 0
        out.append((mode.decode(), sha.decode(), os.fsdecode(path), count))
    return out


def _blobs(repo: Path, k: Knobs, shas: list[str]) -> dict[str, bytes]:
    """Content of each blob by one ``cat-file --batch`` call."""
    if not shas:
        return {}
    raw = _git(repo, k, "cat-file", "--batch", input="\n".join(shas) + "\n", binary=True)
    data, got, i = raw.out_bytes or b"", {}, 0
    while i < len(data):
        nl = data.index(b"\n", i)
        sha, _kind, size = data[i:nl].decode().split()
        got[sha] = data[nl + 1 : nl + 1 + int(size)]
        i = nl + 1 + int(size) + 1
    return got


def _secret(text: str, cfg: Config | None) -> bool:
    """A provider shape, a private key or an operator's own pattern; not the default ``key =
    value`` ones, which refused 129 files here."""
    if shape_hit(text) or _PEM.search(text):
        return True
    mine = session_secret_patterns(cfg.session if cfg else SessionConfig(), builtin=False)
    return bool(mask_secrets(text, [p for p in mine if p not in _LOOSE])[1])


def _redact(cfg: Config | None) -> Callable[[str], str]:
    r = redactor("upstream", cfg)
    return lambda s: r.text(s).text


def _scan(
    repo: Path, k: Knobs, rev: str, cfg: Config | None, only: set[str] | None = None
) -> tuple[list[str], list[str]]:
    """(paths with a secret, paths too big to read) of ``rev``; binary files are read too."""
    by_sha: dict[str, list[str]] = {}
    unscanned = []
    for mode, sha, path, size in _tree_entries(repo, k, rev):
        if mode not in _BLOB or (only is not None and path not in only):
            continue
        if size > k.scan_max_blob_bytes:
            unscanned.append(path)
            continue
        by_sha.setdefault(sha, []).append(path)
    hits, shas, step = [], sorted(by_sha), max(1, k.scan_batch_files)
    for i in range(0, len(shas), step):
        for sha, blob in _blobs(repo, k, shas[i : i + step]).items():
            if _secret(blob.decode("utf-8", "replace"), cfg):
                hits += by_sha[sha]
    return sorted(hits), sorted(unscanned)


def _commit_tree(repo: Path, k: Knobs, tree: str, msg: str, parent: str, env: dict[str, str]):
    args = ["commit-tree", tree, "-m", msg]
    if parent:
        args[2:2] = ["-p", parent]
    return _git(repo, k, *args, env=env).out


def _who(name: str) -> dict[str, str]:
    return {
        f"GIT_{role}_{what}": value
        for role in ("AUTHOR", "COMMITTER")
        for what, value in (("NAME", name), ("EMAIL", "remote-lane@ddflow.invalid"))
    }


def _with_index(repo: Path, k: Knobs, fn: Callable[[dict[str, str]], str]) -> str:
    with fsio.scratch_dir("ddflow-idx-") as d:
        return fn({"GIT_INDEX_FILE": str(d / "index")})


def _seed_text(seed: object, redact: Callable[[str], str]) -> str:
    return redact(S.render(seed) if isinstance(seed, S.Seed) else str(seed))  # type: ignore[arg-type]


def build_outbound(
    repo: Path,
    base: str,
    item_id: str,
    lane: str,
    seed: S.Seed | str,
    dest: Path,
    *,
    knobs: Knobs = DEFAULT,
    cfg: Config | None = None,
) -> Outbound:
    """The directory a remote lane is sent: bundle, seed pack, manifest."""
    k = knobs
    repo = Path(repo)
    base_sha = G.run(repo, "rev-parse", "--verify", f"{base}^{{commit}}", timeout=k.git_timeout_s)
    if not base_sha.ok:
        raise RemoteError(f"base {base!r} is not a commit of {repo}")
    base_sha_s = base_sha.out

    def tree(env: dict[str, str]) -> str:
        _git(repo, k, "read-tree", base_sha_s, env=env)
        drop = [p for _, _, p, _ in _tree_entries(repo, k, base_sha_s) if _excluded(p, k)]
        if drop:
            raw = b"".join(os.fsencode(p) + b"\0" for p in drop)
            _git(repo, k, "update-index", "--force-remove", "-z", "--stdin", env=env, input=raw)
        return _git(repo, k, "write-tree", env=env).out

    tree_sha = _with_index(repo, k, tree)
    hits, unscanned = _scan(repo, k, tree_sha, cfg)
    if hits:
        raise RemoteError(
            "files match a secret pattern and would leave the host: "
            + ", ".join(hits)
            + " (fix them, or add the path to [swarm] remote_exclude)"
        )
    env = {**_who("ddflow-host"), "GIT_AUTHOR_DATE": _DATE, "GIT_COMMITTER_DATE": _DATE}
    root = _commit_tree(repo, k, tree_sha, f"ddflow remote seed for {item_id}", "", env)
    ref = _ref("remote-base", item_id, lane)
    # the ref stays until `forget`: a return bundle needs the root, and gc would prune it
    _git(repo, k, "update-ref", ref, root)
    dest = Path(dest).resolve()  # git runs in the repository, this code in the caller's cwd
    bundle = dest / BUNDLE
    try:
        dest.mkdir(parents=True, exist_ok=True)
        _git(repo, k, "bundle", "create", "-q", str(bundle), ref)
        size = bundle.stat().st_size
        if size > k.bundle_max_bytes:
            bundle.unlink()
            raise RemoteError(f"bundle is {size} bytes, over [swarm] remote_bundle_max_bytes")
    except (RemoteError, OSError):
        G.run(repo, "update-ref", "-d", ref, timeout=k.git_timeout_s)
        raise
    digest = content_digest(bundle.read_bytes())
    seed_path = dest / k.seed_name
    fsio.atomic_write(seed_path, _seed_text(seed, _redact(cfg)))
    manifest = dest / MANIFEST
    fsio.atomic_write(
        manifest,
        json.dumps(
            {
                "item": item_id,
                "lane": lane,
                "root": root,
                "ref": ref,
                "sha256": digest,
                "bytes": size,
                "trust": "untrusted",
                "seed": k.seed_name,
                "exclude": list(k.exclude),
                "unscanned": unscanned,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    return Outbound(
        dest, bundle, seed_path, manifest, base_sha_s, root, ref, digest, size,
        tuple(unscanned), item_id, lane,
    )  # fmt: skip


def _ref(family: str, item: str, lane: str) -> str:
    return f"refs/ddflow/{family}/{_safe(item)}/{_safe(lane)}"


def forget(repo: Path, out: Outbound, knobs: Knobs = DEFAULT) -> None:
    """Drop the seed ref of one lane, once its branch is settled."""
    G.run(Path(repo), "update-ref", "-d", out.ref, timeout=knobs.git_timeout_s)


def _source(source: object, k: Knobs, branch: str | None) -> tuple[Path, str]:
    text = str(source)
    p = Path(text)
    if "://" in text or not p.exists():
        raise RemoteError(f"source {text!r} must be a local path (a bundle file or a repository)")
    p = p.resolve()  # absolute: a path never reads as a git option
    if p.is_file():
        size = p.stat().st_size
        if size > k.return_max_bytes:
            raise RemoteError(
                f"returned bundle is {size} bytes, over [swarm] remote_return_max_bytes"
            )
        if branch is None:
            heads = _git(p.parent, k, "bundle", "list-heads", str(p)).out.splitlines()
            refs = [h.split()[1] for h in heads if len(h.split()) > 1 and h.split()[1] != "HEAD"]
            if len(refs) != 1:
                raise RemoteError(f"bundle carries {len(refs)} branches; name one")
            if not _REF.fullmatch(refs[0]):
                raise RemoteError("the bundle names a ref git would not accept")
            return p, refs[0]
    if branch is None:
        raise RemoteError("a pushed repository needs the branch name")
    return p, branch if branch.startswith("refs/") else f"refs/heads/{branch}"


def _land(repo: Path, k: Knobs, out: Outbound, tip: str, lane: str) -> tuple[str, str]:
    branch = f"{k.branch_prefix}{_safe(out.item)}/{_safe(lane)}"

    def tree(env: dict[str, str]) -> str:
        _git(repo, k, "read-tree", tip, env=env)
        back = b"".join(
            f"{m} {sha}\t".encode() + os.fsencode(path) + b"\0"
            for m, sha, path, _ in _tree_entries(repo, k, out.base)
            if _excluded(path, k)
        )
        if back:
            _git(repo, k, "update-index", "--add", "-z", "--index-info", env=env, input=back)
        return _git(repo, k, "write-tree", env=env).out

    t = _with_index(repo, k, tree)
    name = _safe(lane)
    msg = f"remote lane {name} for {_safe(out.item)}"
    sha = _commit_tree(repo, k, t, msg, out.base, _who(name))
    _git(repo, k, "update-ref", f"refs/heads/{branch}", sha)
    return branch, sha


def import_return(
    repo: Path,
    source: object,
    out: Outbound,
    *,
    globs: Iterable[str] = (),
    branch: str | None = None,
    knobs: Knobs = DEFAULT,
    cfg: Config | None = None,
) -> Returned:
    """Take a lane's pushed branch or bundle from a local path; land it on a host branch.

    Only the landed commit stays: the lane's own history, which nothing scanned, keeps no
    ref. A pushed branch can only be measured once fetched; a bundle file is measured first,
    so prefer ``collect`` for an untrusted lane.
    """
    k, repo = knobs, Path(repo)
    src, srcref = _source(source, k, branch)
    dest = _ref("remote", out.item, out.lane)
    f = G.run(
        repo,
        "-c",
        "transfer.fsckObjects=true",
        "fetch",
        "--no-tags",
        "--no-write-fetch-head",
        "--no-recurse-submodules",
        "-q",
        str(src),
        f"+{srcref}:{dest}",
        timeout=k.git_timeout_s,
        env={"GIT_ALLOW_PROTOCOL": "file"},
    )
    if not f.ok:
        raise RemoteError(f"could not read {srcref} from {src}: {f.err or f.out}")
    tip = _git(repo, k, "rev-parse", "--verify", dest).out
    try:
        got = _inspect(repo, k, out, tip, tuple(globs), cfg)
        if not got.ok:
            return got
        name, sha = _land(repo, k, out, tip, out.lane)
        return dataclasses.replace(got, branch=name, sha=sha)
    finally:
        G.run(repo, "update-ref", "-d", dest, timeout=k.git_timeout_s)


def _inspect(
    repo: Path, k: Knobs, out: Outbound, tip: str, globs: tuple[str, ...], cfg: Config | None
) -> Returned:
    """What the host checks of a fetched tip."""
    if not G.run(repo, "merge-base", "--is-ancestor", out.root, tip, timeout=k.git_timeout_s).ok:
        raise RemoteError("the returned branch does not descend from the seed commit")
    span = f"{out.root}..{tip}"
    commits = int(_git(repo, k, "rev-list", "--count", span).out)
    if commits > k.return_max_commits:
        raise RemoteError(f"{commits} commits is over [swarm] remote_return_max_commits")
    size = int(_git(repo, k, "rev-list", "--objects", "--disk-usage", span).out)
    if size > k.return_max_bytes:
        raise RemoteError(f"{size} bytes is over [swarm] remote_return_max_bytes")
    names = _git(repo, k, "diff", "--name-only", "--no-renames", out.root, tip, z=True)
    changed = tuple(sorted(names.paths() or []))
    if not changed:
        raise RemoteError("the returned branch makes no change to the seed commit")
    touched = set(changed)
    links = {p for m, _, p, _ in _tree_entries(repo, k, tip) if p in touched and m in _LINKS}
    bad = tuple(sorted(links | {p for p in changed if _excluded(p, k)}))
    outside = tuple(p for p in changed if not any(inside(p, g) for g in globs))
    hits, unscanned = _scan(repo, k, tip, cfg, touched)
    return Returned(
        not (bad or hits or unscanned), "", "", tip, changed, outside, bad, tuple(hits), commits,
        tuple(unscanned),
    )  # fmt: skip


@dataclass(frozen=True)
class Proposal:
    """An approval a remote lane ASKS for: untrusted data, never an approval."""

    kind: str
    subject: str = ""
    reason: str = ""
    trust: str = "unknown"

    def fenced(self) -> str:
        rec = S.Rec(self.subject or "-", self.kind, self.reason, "unknown")
        return S.fence_line("proposal", rec, S.default_redact(), 10_000, 100_000) or ""


@dataclass(frozen=True)
class Proposals:
    proposals: tuple[Proposal, ...] = ()
    dropped: int = 0


def parse_proposals(
    text: str, knobs: Knobs = DEFAULT, redact: Callable[[str], str] | None = None
) -> Proposals:
    """The proposals in a lane's JSON list: redacted, capped, malformed entries dropped."""
    red = redact or S.default_redact()
    try:
        rows = json.loads(text)
    except ValueError:
        return Proposals((), 1)
    if not isinstance(rows, list):
        return Proposals((), 1)
    n, got, dropped = knobs.proposal_chars, [], 0
    for row in rows:
        kind = row.get("kind") if isinstance(row, dict) else None
        if not isinstance(kind, str) or not kind.strip() or len(got) >= knobs.proposals_max:
            dropped += 1
            continue
        sub, why = (str(row.get(x) or "") for x in ("subject", "reason"))
        got.append(Proposal(*(red(v).strip()[:n] for v in (kind, sub, why))))
    return Proposals(tuple(got), dropped)


@dataclass(frozen=True)
class Consent:
    """A person's go-ahead for one lane on one host (r-consent-outward), read by the caller
    from its record; this module only checks its scope and expiry."""

    host: str
    item: str
    lane: str
    expires_at: float
    by: str = ""
    unscanned_seen: bool = False  # the person saw the list of files too big to scan


class LaneTransport(Protocol):
    """Where a remote lane runs. ``name`` is a plain file in the lane's own directory; a host
    that cannot be reached is ``unavailable``, never ``failed`` or ok."""

    target: str

    def start(self, lane: str, argv: Sequence[str]) -> Result: ...

    def signal(self, lane: str, sig: str = "TERM") -> Result: ...

    def observe(self, lane: str) -> LaneStatus | Result: ...

    def copy_in(self, lane: str, local: Path, name: str) -> Result: ...

    def copy_out(self, lane: str, name: str, local: Path, max_bytes: int) -> Result: ...

    def cleanup(self, lane: str) -> Result: ...


#: The start time `start` wrote proves the pid is the lane's, so a recycled pid is never
#: signalled. Without ``lstart`` no lane could be stopped: `start` refuses.
_STAMP = 'ps -o lstart= -p "$(cat lane.pid)"'
_ALIVE = f'[ ! -s lane.rc ] && [ -s lane.stamp ] && [ "$({_STAMP})" = "$(cat lane.stamp)" ]'
_OBSERVE = f"""if {_ALIVE}; then echo "running $(cat lane.pid)"
elif [ -s lane.rc ]; then echo "ended $(cat lane.rc)"
elif [ -f lane.stopped ]; then echo stopped
elif [ -f lane.pid ]; then echo gone
else echo absent; fi"""


class SshTransport:
    """`LaneTransport` over the operator's own ssh login: nothing is forwarded, and an unknown
    host key or a password prompt fails, whatever the ssh config says. The remote needs
    ``sh``, ``setsid`` and git. What it answers is data."""

    def __init__(self, host: str, knobs: Knobs = DEFAULT) -> None:
        if not _HOST.fullmatch(host):
            raise RemoteError(f"ssh host {host!r} is not a plain host name")
        root = PurePosixPath(knobs.lanes_root)
        if root.is_absolute() or ".." in root.parts or not _ROOT.fullmatch(knobs.lanes_root):
            raise RemoteError(f"lanes_root {knobs.lanes_root!r} must stay under the remote home")
        self.target, self._k = host, knobs

    def command(self, shell_line: str) -> tuple[str, ...]:
        """The ssh argv that runs ``shell_line`` under ``sh``, whatever the login shell is."""
        k = self._k
        opts = ("BatchMode=yes", "StrictHostKeyChecking=yes")
        opts += (f"ConnectTimeout={k.ssh_connect_timeout_s}", "ClearAllForwardings=yes")
        opts += ("PermitLocalCommand=no",)
        flags = [x for o in opts for x in ("-o", o)]
        line = f"sh -c {shlex.quote(shell_line)}"
        return (*k.ssh_argv, "-T", "-a", "-x", *flags, "--", self.target, line)

    def _dir(self, lane: str) -> str:
        return shlex.quote(f"{self._k.lanes_root}/{_safe(lane)}")

    def _file(self, lane: str, name: str) -> str:
        return f"{self._dir(lane)}/{shlex.quote(_safe(name))}"

    def _judge(self, code: int, err: str) -> Result:
        clip = err.strip()[: self._k.detail_chars]
        if code == 0:
            return ok()
        return (unavailable if code == _SSH_LOST else failed)(clip or f"exit code {code}")

    def _call(self, line: str) -> tuple[Result, str]:
        t = self._k.ssh_timeout_s
        c = P.capture(list(self.command(line)), timeout=t, encoding="utf-8", errors="replace")
        if c.timed_out:
            return unavailable(f"{self.target} did not answer in {t}s"), ""
        if not c.ran:
            return unavailable(f"ssh could not run: {c.error}"), ""
        return self._judge(c.returncode or 0, c.stderr), c.stdout

    def start(self, lane: str, argv: Sequence[str]) -> Result:
        d = self._dir(lane)
        body = f"{shlex.join(argv)}; echo $? > lane.rc"
        line = (
            '[ -n "$(ps -o lstart= -p $$ 2>/dev/null)" ] || '
            "{ echo 'the remote ps has no lstart' >&2; exit 3; }\n"
            f"mkdir -p {d} && cd {d} || exit 1\n"
            f"if {_ALIVE}; then echo 'the lane is already running' >&2; exit 3; fi\n"
            "rm -f lane.rc lane.stopped lane.stamp\n"
            f"nohup setsid sh -c {shlex.quote(body)} >lane.log 2>&1 </dev/null &\n"
            f"echo $! > lane.pid\n{_STAMP} > lane.stamp || :"
        )
        return self._call(line)[0]

    def signal(self, lane: str, sig: str = "TERM") -> Result:
        if sig not in _SIGNALS:
            raise RemoteError(f"signal {sig!r} is not one of {', '.join(_SIGNALS)}")
        d = self._dir(lane)
        line = f'cd {d} && {_ALIVE} && : > lane.stopped && kill -s {sig} -- "-$(cat lane.pid)"'
        return self._call(line)[0]

    def observe(self, lane: str) -> LaneStatus | Result:
        r, out = self._call(
            f"cd {self._dir(lane)} 2>/dev/null || {{ echo absent; exit 0; }}\n{_OBSERVE}"
        )
        if not r.ok:
            return r
        word, _, rest = out.strip().partition(" ")
        if word == "running" and rest.isdigit():
            return LaneStatus("running", pid=int(rest))
        if word == "ended" and rest.isdigit():
            return LaneStatus("exited" if rest == "0" else "failed", exit_code=int(rest))
        if word == "stopped":
            return LaneStatus("stopped")
        if word == "gone":
            return LaneStatus("failed", detail="the process is gone and left no exit code")
        return failed("no such lane" if word == "absent" else "an unreadable answer")

    def _piped(self, line: str, work: Callable[[P.Popen], Result | None]) -> Result:
        """``line`` with its pipes given to ``work``, cut at ``copy_timeout_s``; a ``work``
        that answers ends it early."""
        try:
            p = P.spawn_stdio(list(self.command(line)))
        except OSError as exc:
            return unavailable(f"ssh could not run: {exc}")
        clock = threading.Timer(self._k.copy_timeout_s, P.kill_group, (p,))
        clock.start()
        try:
            early = work(p)
            if early is not None:
                return early
            code = p.wait()
            if code < 0:
                return unavailable(f"{self.target} did not finish in {self._k.copy_timeout_s}s")
            err = p.stderr.read(self._k.detail_chars)  # type: ignore[union-attr]
            return self._judge(code, err.decode("utf-8", "replace"))
        finally:
            clock.cancel()
            if p.poll() is None:  # ended early: never signal a reaped pid
                P.stop_group(p)

    def copy_in(self, lane: str, local: Path, name: str) -> Result:
        line = f"mkdir -p {self._dir(lane)} && cat > {self._file(lane, name)}"

        def feed(p: P.Popen) -> None:
            with contextlib.suppress(BrokenPipeError), open(local, "rb") as fh:
                while chunk := fh.read(_CHUNK):
                    p.stdin.write(chunk)  # type: ignore[union-attr]
                p.stdin.close()  # type: ignore[union-attr]

        return self._piped(line, feed)

    def copy_out(self, lane: str, name: str, local: Path, max_bytes: int) -> Result:
        """Stream a file back, cut at ``max_bytes``: the remote's word on size is not believed.
        Nothing is left at ``local`` unless the whole file came."""
        local = Path(local)
        part = local.with_name(f"{local.name}.part")

        def drain(p: P.Popen) -> Result | None:
            p.stdin.close()  # type: ignore[union-attr]
            total = 0
            with open(part, "wb") as fh:
                while chunk := p.stdout.read(_CHUNK):  # type: ignore[union-attr]
                    total += len(chunk)
                    if total > max_bytes:
                        return failed(f"the file is over the {max_bytes} byte cap")
                    fh.write(chunk)
            return None

        r = self._piped(f"cat {self._file(lane, name)}", drain)
        if r.ok:
            part.replace(local)
        else:
            part.unlink(missing_ok=True)
        return r

    def stdio_argv(self, lane: str, argv: Sequence[str]) -> tuple[str, ...]:
        """The ssh argv whose stdin and stdout ARE the lane's: ACP over SSH stdio (Q8)."""
        d = self._dir(lane)
        return self.command(f"mkdir -p {d} && cd {d} && exec {shlex.join(argv)}")

    def cleanup(self, lane: str) -> Result:
        d = self._dir(lane)
        line = (
            f"( cd {d} 2>/dev/null || exit 0\n"
            f"if {_ALIVE}; then\n"
            'kill -s KILL -- "-$(cat lane.pid)"; fi )\n'
            f"rm -rf -- {d}"
        )
        return self._call(line)[0]


def ship(t: LaneTransport, out: Outbound, consent: Consent | None, *, now: float) -> Result:
    """Send the outbound directory, the one point where code leaves the host, under a
    person's unexpired consent for this host, item and lane."""
    want = (t.target, out.item, out.lane)
    if consent is None or (consent.host, consent.item, consent.lane) != want:
        raise RemoteError(f"no consent for lane {out.lane} of {out.item} on {t.target}")
    if consent.expires_at <= now:
        raise RemoteError(f"the consent for {t.target} has expired")
    if out.unscanned and not consent.unscanned_seen:
        raise RemoteError(
            "the consent does not cover files too big to scan: " + ", ".join(out.unscanned)
        )
    for path in (out.bundle, out.seed, out.manifest):
        r = t.copy_in(out.lane, path, path.name)
        if not r.ok:
            return r
    return ok()


def lane_argv(out: Outbound, agent: Sequence[str]) -> tuple[str, ...]:
    """What runs in the lane's directory: unpack the bundle once, run ``agent`` in ``work/``,
    then write the return bundle (none if nothing was committed) and, when the lane asked
    nothing, an empty proposals list. Only the agent writes to stdout, which ACP owns."""
    fetch = shlex.quote(f"{out.ref}:refs/heads/lane")
    script = (
        "if [ ! -d work ]; then\n"
        "git init -q work >&2 &&\n"
        f"git -C work fetch -q ../{BUNDLE} {fetch} >&2 &&\n"
        "git -C work checkout -q lane >&2 || { rm -rf work; exit 125; }\n"
        "fi\n"
        f"cd work || exit 125\nrm -f ../{RETURN}\n"
        f"{shlex.join(agent)}\nrc=$?\n"
        f"git bundle create -q ../{RETURN} {out.root}..lane >&2 || :\n"
        f"[ -e ../{PROPOSALS} ] || echo '[]' > ../{PROPOSALS}\n"
        'exit "$rc"\n'
    )
    return ("sh", "-c", script)


def launch(t: LaneTransport, out: Outbound, agent: Sequence[str]) -> Result:
    return t.start(out.lane, lane_argv(out, agent))


@dataclass(frozen=True)
class Collected:
    result: Result
    bundle: Path | None  # for ``import_return``; None when the lane left no return bundle
    proposals: Proposals
    asked: Result  # the proposals' own copy: not ok is "could not tell", not "asked nothing"


def collect(
    t: LaneTransport,
    out: Outbound,
    dest: Path,
    *,
    knobs: Knobs = DEFAULT,
    cfg: Config | None = None,
) -> Collected:
    """Fetch the return bundle and the proposals, each under its cap, into ``dest``."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    bundle, asked = dest / RETURN, dest / PROPOSALS
    r = t.copy_out(out.lane, RETURN, bundle, knobs.return_max_bytes)
    got = Proposals()
    read = t.copy_out(out.lane, PROPOSALS, asked, knobs.proposals_max_bytes)
    if read.ok:
        text = asked.read_text(encoding="utf-8", errors="replace")
        got = parse_proposals(text, knobs, _redact(cfg))
    return Collected(r, bundle if r.ok else None, got, read)
