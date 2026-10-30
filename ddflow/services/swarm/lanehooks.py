"""Lane-private hook configuration: written INSIDE the lane's home, never the project (SW-lane-hooks).

Decision ATF-Q2 / D-swarm-answers Q6 and Q7. A lane worker must not read the project's tracked
hook files (every lane would share them and the identity would be wrong), so its pre-tool hook is
written under the lane's private home and carries the LANE's identity (``DDFLOW_AGENT`` for the
launch, ``--agent`` in the command), which attributes events and leases to the lane. What the
lane gets is read from its harness descriptor, never guessed:

  prevent  a blocking pre-tool hook the harness reads from the lane home: Claude Code
           (``--settings <file>``) and the opencode family (a plugin in the lane's config dir).
           A write outside the item's lease globs, or any write once the lease is gone, is refused.
  detect   everything else (no descriptor, no hook door, a door whose location is unverified):
           the lane worktree's lease-checking git pre-commit hook (``githook``): the file is
           already written when it is caught, and `git commit --no-verify` skips it.

A plugin is executable code: the FIRST use per template needs the operator's approval of its
digest, shown as a diff (``approval.check``: a person's ``approval.granted`` for subject
``lane-plugin:<harness>``); ``prepare`` returns ``Consent`` until it exists and writes nothing.
Every file is listed in a manifest in the lane home and ``remove`` deletes exactly those.
``hook_probe`` is what ``conformance.run(hook_probe=...)`` calls: it fires the written hook the
way the harness would and is None (unavailable, never passed) when it cannot. No model, no key,
no endpoint is used here.
"""

from __future__ import annotations

import difflib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...infra import git as G
from ...infra import proc as P
from .. import approval, harnessreg
from ..lane_driver import Result, unavailable
from . import githook
from . import lanehooks_plugin as PL
from .lanehooks_check import PROBE_DIR
from .lanehooks_check import REFUSE as CK_REFUSE

#: Subdirectory of the lane home that holds the manifest and any non-plugin hook file.
DIR_NAME = "ddflow-lane-hooks"
MANIFEST = "manifest.json"
PREVENT, DETECT = "prevent", "detect"
#: Where each harness reads a hook from the LANE home: (door, how well that is known).
#: claude: `claude --settings <file>` (documented in `claude --help`); kilo: its global config dir
#: ``<home>/.config/kilo/plugin`` was read with `kilo debug config` on a scratch HOME
#: (kilo 7.8.18); opencode: not installed here, so a fact nobody has read.
DOORS: dict[str, tuple[str, str]] = {
    "claude": ("settings-arg", "documented"),
    "kilo": ("plugin-home", "verified"),
    "opencode": ("plugin-home", "unverified"),
}
#: Claude tools that write files (the matcher of the settings hook).
CLAUDE_WRITE_MATCHER = "Edit|Write|MultiEdit|NotebookEdit"
_READABLE = ("verified", "documented")


@dataclass(frozen=True)
class Strength:
    """What a harness can do for a lane's hook, from its descriptor."""

    harness: str
    level: str  #: prevent | detect
    door: str  #: settings-arg | plugin-home | git-pre-commit
    known: str  #: verified | documented | unverified | "" (for git)
    reason: str  #: why not prevent, "" when it is
    consent: bool = False  #: executable code: the operator approves the template first


def strength(harness: str, *, allow_unverified: bool = False) -> Strength:
    """The strength a lane of ``harness`` gets. A harness whose door is unverified is
    ``detect`` unless ``allow_unverified`` (the caller has read the door on the real tool)."""
    git = lambda why: Strength(harness, DETECT, "git-pre-commit", "", why)  # noqa: E731
    h = harnessreg.get(harness)
    if h is None:
        return git(f"no harness descriptor for {harness!r}")
    hk = h.hooks
    if hk is None:
        return git("its hooks are NOT VERIFIED in the descriptor")
    if "pre_tool" not in hk.events or hk.block == "none":
        return git("its descriptor has no blocking pre-tool hook")
    door = DOORS.get(harness)
    if door is None:
        return git("no lane-private location for its hook config is verified")
    kind, known = door
    if (kind == "plugin-home") != (hk.style == "plugin"):
        return git(f"its descriptor style {hk.style!r} does not match the lane door {kind!r}")
    if known not in _READABLE and not allow_unverified:
        return git(f"its lane hook location is {known}: read it with the installed tool first")
    return Strength(harness, PREVENT, kind, known, "", consent=hk.style == "plugin")


@dataclass(frozen=True)
class Consent:
    """The operator's approval is missing: nothing was written."""

    subject: str
    digest: str
    diff: str  #: unified diff of the plugin template against nothing
    how: str


@dataclass(frozen=True)
class Prepared:
    """What the launcher needs: ``env`` into ``LaunchSpec.fixed_env`` (identity), ``args`` appended
    to the harness argv, and the level honestly reported."""

    strength: Strength
    agent: str
    home: Path
    files: tuple[Path, ...]
    env: dict[str, str]
    args: tuple[str, ...] = ()
    note: str = ""

    @property
    def level(self) -> str:
        return self.strength.level


@dataclass
class _Plan:
    files: dict[Path, str] = field(default_factory=dict)
    args: tuple[str, ...] = ()
    argv: list[str] = field(default_factory=list)


def consent_subject(harness: str) -> str:
    return f"lane-plugin:{harness}"


def consent_diff() -> str:
    """The plugin template as an added file: what the operator reads before approving."""
    lines = PL.TEMPLATE.splitlines(keepends=True)
    return "".join(difflib.unified_diff([], lines, "/dev/null", f"lane-home/{PL.FILE_NAME}"))


def _need_consent(harness: str, state: Any) -> Consent | None:
    subject, dig = consent_subject(harness), PL.digest()
    if state is not None and approval.check(state, subject, dig).ok:
        return None
    return Consent(
        subject,
        dig,
        consent_diff(),
        f"{harness} runs this plugin code in the lane. A person approves it once per template: "
        f"`ddflow approvals request` for {subject} at digest {dig}, then re-run; an agent never "
        "approves it.",
    )


def check_argv(repo: Path, agent: str) -> list[str]:
    """The command every lane hook runs: the lane's own identity and the project root."""
    return [sys.executable, "-m", "ddflow.services.swarm.lanehooks_check", "--repo", str(repo),
            "--agent", agent]  # fmt: skip


def _plan(s: Strength, home: Path, repo: Path, agent: str) -> _Plan:
    argv = check_argv(repo, agent)
    if s.door == "plugin-home":
        hk = harnessreg.get(s.harness).hooks  # type: ignore[union-attr]
        sub = Path(hk.file).name or "plugin"
        target = home / ".config" / s.harness / sub / PL.FILE_NAME
        return _Plan({target: PL.render(argv, agent)}, (), argv)
    settings = home / DIR_NAME / "claude-settings.json"
    hook = {"type": "command", "command": shlex.join(argv)}
    doc = {"hooks": {"PreToolUse": [{"matcher": CLAUDE_WRITE_MATCHER, "hooks": [hook]}]}}
    return _Plan({settings: json.dumps(doc, indent=2) + "\n"}, ("--settings", str(settings)), argv)


def _refuse_location(home: Path, repo: Path, worktree: Path) -> str:
    """Why ``home`` is not a private place, or "": never in the lane checkout, nor the project
    (except under its git-ignored ``.ddflow/``)."""
    h = home.resolve()
    for root, allow in ((worktree, ()), (repo, (".ddflow",))):
        r = root.resolve()
        if h == r or r in h.parents:
            if not any(h == r / a or (r / a) in h.parents for a in allow):
                return f"the lane home {h} is inside {r}: hook files would reach the project"
    return ""


def _tracked(worktree: Path, files: list[Path]) -> bool:
    return any(G.run(worktree, "ls-files", "--error-unmatch", str(f)).ok for f in files)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write(text)


def _manifest(home: Path) -> dict[str, Any] | None:
    try:
        d = json.loads((home / DIR_NAME / MANIFEST).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def prepare(
    repo: Path,
    worktree: Path,
    home: Path,
    harness: str,
    agent: str,
    *,
    state: Any = None,
    allow_unverified: bool = False,
    git_fallback: bool = True,
) -> Prepared | Consent | Result:
    """Write the lane's hook config under ``home`` and report what it gives.

    ``state`` is the folded event log (for the plugin approval check). A ``Consent`` or an
    ``unavailable`` Result means nothing was written. ``git_fallback`` installs the worktree's
    lease-checking pre-commit hook when the harness has no lane hook door (``detect``)."""
    repo, worktree, home = Path(repo), Path(worktree), Path(home)
    if not agent or agent != agent.strip() or any(c.isspace() or c == "/" for c in agent):
        return unavailable("the lane needs its own agent identity (no spaces or slashes)")
    if bad := _refuse_location(home, repo, worktree):
        return unavailable(bad)
    s = strength(harness, allow_unverified=allow_unverified)
    env = {"DDFLOW_AGENT": agent}
    files: list[Path] = []
    args: tuple[str, ...] = ()
    argv: list[str] = []
    if s.level == PREVENT:
        if s.consent and (need := _need_consent(harness, state)) is not None:
            return need
        plan = _plan(s, home, repo, agent)
        if _tracked(worktree, list(plan.files)):
            return unavailable("a lane hook path is tracked by the project: refusing to write it")
        for path, text in plan.files.items():
            _write(path, text)
        files, args, argv = list(plan.files), plan.args, plan.argv
        note = f"{harness}: a blocking pre-tool hook in the lane home ({s.known})"
    elif git_fallback:
        githook.install(worktree, agent=agent)
        note = f"{harness}: detection at commit only, not prevention ({s.reason})"
    else:
        note = f"{harness}: no hook installed ({s.reason})"
    manifest = {
        "harness": harness, "agent": agent, "level": s.level, "door": s.door,
        "files": [str(f) for f in files], "argv": argv, "worktree": str(worktree),
        "git": s.level == DETECT and git_fallback, "template": PL.digest() if s.consent else "",
    }  # fmt: skip
    _write(home / DIR_NAME / MANIFEST, json.dumps(manifest, indent=2) + "\n")
    return Prepared(s, agent, home, tuple(files), env, args, note)


def listed(home: Path) -> list[Path]:
    """The files the lane's hook config owns (for the lane record), manifest included."""
    m = _manifest(Path(home))
    if m is None:
        return []
    return [*(Path(f) for f in m.get("files", [])), Path(home) / DIR_NAME / MANIFEST]


def remove(home: Path) -> str:
    """Delete what ``prepare`` wrote (and the git hook it installed); one line saying what."""
    home = Path(home)
    m = _manifest(home)
    if m is None:
        return "no lane hook config here"
    done = []
    if m.get("git"):
        done.append(githook.uninstall(Path(m["worktree"])))
    for f in m.get("files", []):
        Path(f).unlink(missing_ok=True)
        done.append(f"removed {f}")
    shutil.rmtree(home / DIR_NAME, ignore_errors=True)
    return "; ".join(done) or "removed the lane hook manifest"


_NODE = """const mod = await import(process.argv[2]);
const hooks = await Object.values(mod)[0]({ directory: process.argv[3], worktree: process.argv[3] });
try {
  await hooks["tool.execute.before"]({ tool: "write" }, { args: { filePath: process.argv[4] } });
  process.exit(3);
} catch (e) { process.stderr.write(String(e.message)); process.exit(2); }
"""


def _fire(argv: list[str], agent: str, cwd: str, payload: dict[str, Any]) -> bool | None:
    try:
        r = P.run(
            argv, input=json.dumps(payload), capture_output=True, text=True, timeout=60,
            env={**os.environ, "DDFLOW_AGENT": agent}, check=False,
        )  # fmt: skip
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return r.returncode == CK_REFUSE and "hook fired" in r.stderr


def _fire_plugin(m: dict[str, Any]) -> bool | None:
    node = shutil.which("node")
    files = m.get("files") or []
    if not node or not files:
        return None  # JS that was never run proves nothing
    with tempfile.TemporaryDirectory(prefix="ddflow-hookprobe-") as tmp:
        copy = Path(tmp) / "plugin.mjs"  # node reads ESM by extension; kilo (bun) reads .js
        copy.write_text(Path(files[0]).read_text("utf-8"), "utf-8")
        script = Path(tmp) / "run.mjs"
        script.write_text(_NODE, "utf-8")
        argv = [node, str(script), copy.as_uri(), m["worktree"], f"{PROBE_DIR}/x"]
        try:
            r = P.run(argv, capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
    return r.returncode == CK_REFUSE and "hook fired" in r.stderr


def hook_probe(home: Path) -> Callable[[], bool | None]:
    """The ``hook_probe`` for ``conformance.run``: fires the lane's written hook for the probe
    path (always refused) and passes only when it refuses. None (unavailable) for a ``detect``
    lane, a missing manifest, or a plugin that cannot be run (no node)."""
    home = Path(home)

    def probe() -> bool | None:
        m = _manifest(home)
        if m is None or m.get("level") != PREVENT:
            return None  # a git hook is not a pre-tool hook: it never reaches the 'hook' level
        for f in m.get("files", []):
            if not Path(f).is_file():
                return False  # written config gone: the hook cannot fire
        if m.get("door") == "plugin-home":
            return _fire_plugin(m)
        cfg = Path(m["files"][0])
        try:
            cmd = json.loads(cfg.read_text("utf-8"))["hooks"]["PreToolUse"][0]["hooks"][0][
                "command"
            ]
        except (OSError, ValueError, KeyError, IndexError):
            return False
        argv = shlex.split(cmd)  # exactly what the harness would run
        payload = {"tool_name": "Write", "tool_input": {"file_path": f"{PROBE_DIR}/x"},
                   "cwd": m["worktree"]}  # fmt: skip
        return _fire(argv, str(m["agent"]), m["worktree"], payload)

    return probe
