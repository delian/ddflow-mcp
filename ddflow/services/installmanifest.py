"""The install manifest: what an agent's onboarding put on disk, recorded through ONE call.

A writer (a marked-block writer, an MCP or settings writer, a hook or git-hook installer, a
plugin, command, skill or subagent writer) calls `record` or opens a `recording` run and names
the artifact; this module owns where the record goes, so a writer in any other module needs
no change here:

* a project path reaches the log as one `agent.onboarded {agent, artifacts[]}` event per run,
  not one per file (`core.handlers.installmanifest` folds it);
* a user-scope path (absolute, outside the repository) goes to a machine-local file,
  ``.ddflow/local/install-manifest.json``, never to the committed log;
* re-recording a path and key replaces its digest (and version), so a rewrite through upgrade
  stays true; a run that changes nothing appends nothing;
* `read` returns an agent's artifacts and which of them no longer match their digest.

Digests are the writer's: of a block's text, of a key's value, of a file's bytes (`file_digest`).
No writer is instrumented here.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..adapters.digest import hasher
from ..core.handlers.installmanifest import ARTIFACT_FIELDS, identity, onboarded_of
from ..core.model import fold
from ..infra.fsio import atomic_write, file_lock

LOCAL_FILE = Path(".ddflow") / "local" / "install-manifest.json"
KIND = "agent.onboarded"

#: What an artifact can be: a marked block in a file, a JSON or TOML key in an MCP or settings
#: file, a hook entry, a plugin, command, skill or subagent file, a git hook, an env line, a
#: worktree directory.
ARTIFACT_KINDS = (
    "block",
    "json_key",
    "toml_key",
    "hook",
    "plugin",
    "command",
    "skill",
    "subagent",
    "git_hook",
    "env_line",
    "worktree_dir",
)
#: Kinds whose digest is that of the whole file, so `read` can recompute it unaided.
FILE_KINDS = frozenset({"plugin", "command", "skill", "subagent", "git_hook"})


#: What a resolver, or `_current`, returns for "I cannot say": the artifact is neither changed nor gone.
UNKNOWN: Any = object()


def file_digest(path: Path) -> str | None:
    """SHA-256 of a file's bytes; None when it is absent. An unreadable file (or one behind an
    unreadable directory) raises OSError: `Path.is_file` would call it absent."""
    try:
        return hasher(Path(path).read_bytes()).hexdigest()
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None


def _scope(repo: Path, path: str) -> tuple[str, bool]:
    """``(path as stored, is_project)``: a project path is stored repo-relative (posix)."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        return Path(path).as_posix(), True
    try:
        return p.resolve().relative_to(Path(repo).resolve()).as_posix(), True
    except (OSError, ValueError):
        return str(p), False


def _artifact(repo: Path, agent: str, path: str, kind: str, key: str, digest: str,
              descriptor: str, version: str) -> tuple[dict[str, str], bool]:  # fmt: skip
    if kind not in ARTIFACT_KINDS:
        raise ValueError(f"unknown artifact kind {kind!r}; one of {', '.join(ARTIFACT_KINDS)}")
    if not agent or not path:
        raise ValueError("an artifact needs an agent and a path")
    stored, project = _scope(repo, path)
    art = dict(zip(ARTIFACT_FIELDS, (stored, kind, key, digest, descriptor, version), strict=True))
    return art, project


def _local_read(repo: Path) -> dict[str, list[dict[str, str]]]:
    try:
        raw = json.loads((Path(repo) / LOCAL_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        a: [{f: str(r.get(f, "")) for f in ARTIFACT_FIELDS} for r in rows if isinstance(r, dict)]
        for a, rows in raw.items()
        if isinstance(a, str) and isinstance(rows, list)
    }


def _local_merge(repo: Path, agent: str, arts: list[dict[str, str]]) -> None:
    path = Path(repo) / LOCAL_FILE
    with file_lock(path.with_suffix(".lock"), 30):
        raw = _local_read(repo)
        rows = {identity(r["path"], r["key"]): r for r in raw.get(agent, [])}
        before = dict(rows)
        for a in arts:
            rows[identity(a["path"], a["key"])] = a
        if rows == before:
            return
        raw[agent] = [rows[k] for k in sorted(rows)]
        atomic_write(path, json.dumps(raw, indent=1, sort_keys=True) + "\n")


@dataclass
class Run:
    """One onboarding run's artifacts, written together by `commit` (or `recording`'s exit)."""

    log: Any
    repo: Path
    agent: str
    version: str = ""
    _project: list[dict[str, str]] = field(default_factory=list)
    _user: list[dict[str, str]] = field(default_factory=list)

    def record(self, path: str, kind: str, key: str, digest: str, *, descriptor: str = "",
               version: str | None = None) -> None:  # fmt: skip
        """Note one artifact: ``key`` is the block id, JSON or TOML key, hook name or env name
        ("" for a file that is the artifact whole); ``digest`` is the writer's."""
        art, project = _artifact(self.repo, self.agent, path, kind, key, digest, descriptor,
                                 self.version if version is None else version)  # fmt: skip
        (self._project if project else self._user).append(art)

    def commit(self) -> None:
        if self._user:
            _local_merge(self.repo, self.agent, self._user)
        fresh = _changed(self.log, self.agent, self._project)
        if fresh:
            self.log.append(KIND, self.agent, {"agent": self.agent, "artifacts": fresh})
        self._project, self._user = [], []


def _changed(log: Any, agent: str, arts: list[dict[str, str]]) -> list[dict[str, str]]:
    """``arts`` (the last of a path and key wins) minus those the log already holds as is."""
    have = onboarded_of(fold(log.read_all())).get(agent, {})
    last = {identity(a["path"], a["key"]): a for a in arts}
    return [a for k, a in last.items() if have.get(k) != a]


@contextmanager
def recording(log: Any, repo: Path, agent: str, *, version: str = "") -> Iterator[Run]:
    """A run: everything recorded inside is written once on a clean exit, nothing on a raise."""
    run = Run(log, Path(repo), agent, version)
    yield run
    run.commit()


def record(log: Any, repo: Path, agent: str, path: str, kind: str, key: str, digest: str, *,
           descriptor: str = "", version: str = "") -> None:  # fmt: skip
    """Record one artifact (a run of one)."""
    with recording(log, repo, agent, version=version) as run:
        run.record(path, kind, key, digest, descriptor=descriptor)


def artifacts(log: Any, agent: str, *, repo: Path | None = None) -> list[dict[str, str]]:
    """The agent's recorded artifacts: the project's from the log, plus, with ``repo``, the
    user-scope ones from the machine-local file."""
    rows = dict(onboarded_of(fold(log.read_all())).get(agent, {}))
    if repo is not None:
        rows.update({identity(r["path"], r["key"]): r for r in _local_read(repo).get(agent, [])})
    return [rows[k] for k in sorted(rows)]


@dataclass(frozen=True)
class Report:
    """An agent's artifacts and those whose digest is no longer the recorded one."""

    artifacts: list[dict[str, str]]
    #: the artifact rows that differ, each with ``now``: the digest found (None: gone)
    changed: list[dict[str, Any]]


def _current(repo: Path, art: dict[str, str]) -> str | None:
    """The digest now: None when the file is gone, UNKNOWN when only the writer can say (a
    block, key, hook or env line) or the file cannot be read."""
    if art["kind"] not in FILE_KINDS:
        return UNKNOWN
    p = Path(art["path"]).expanduser()
    try:
        return file_digest(p if p.is_absolute() else Path(repo) / p)
    except OSError:
        return UNKNOWN


def read(log: Any, repo: Path, agent: str,
         current: Callable[[dict[str, str]], str | None] | None = None) -> Report:  # fmt: skip
    """The agent's artifacts and which digests changed. ``current(artifact)`` is a writer's
    resolver for the digest now of a block, key, hook or env line (None: gone; `UNKNOWN`: it
    cannot say); whole-file kinds are always checked here, never by the resolver. Whatever stays unknown is never reported as changed."""
    rows = artifacts(log, agent, repo=repo)
    changed: list[dict[str, Any]] = []
    for a in rows:
        # Whole-file kinds are this module's to check; a resolver speaks for the rest only.
        now = current(a) if current is not None and a["kind"] not in FILE_KINDS else UNKNOWN
        if now is UNKNOWN:
            now = _current(repo, a)
        if now is UNKNOWN:
            continue
        if now != a["digest"]:
            changed.append({**a, "now": now})
    return Report(rows, changed)
