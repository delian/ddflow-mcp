"""What is running: ddflow's version, how it was installed and from which commit.

One place answers this for everything that needs it -- `adopt` deciding how to launch
the server, `enforce` deciding how a hook reaches ddflow, and a bug report saying which
ddflow it was found in. PEP 610: an installer writes `direct_url.json` into the
dist-info of every install that did NOT come from an index (a VCS URL, a local directory,
an archive URL, an editable install) and never for one that did.

Nothing returned names a home directory, and a URL is never returned at all: it can
carry credentials and says nothing a kind plus a commit does not.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from ..config_sections import _compat as CV
from ..core import version as V
from ..infra import git as G
from ..infra import paths as _paths
from ..infra import proc as P
from ..infra.container import in_container

#: The distribution name on the index, and what `uvx` resolves.
DIST_NAME = "ddflow-mcp"

#: Install kinds. `unknown` is installed code with no distribution metadata at all.
KINDS = ("index", "vcs", "local-dir", "editable", "archive", "source-tree", "unknown")

_OWN_REMOTE = re.compile(r"[/:]delian/ddflow-mcp(?:\.git)?/?$")


@dataclass(frozen=True)
class InstallInfo:
    version: str
    kind: str
    commit: str | None
    is_own_dev_tree: bool
    location: str | None = None  # home-normalised; only for a source tree or editable install

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def running_from_source(root: Path | None = None) -> bool:
    """True when this package -- or the tree at `root`, when given -- lives in a
    checkout rather than in site-packages."""
    return CV.is_source_tree(root)


def own_distribution(root: Path | None = None):
    """The installed distribution that THIS `ddflow` package came from, or None.

    Looked up in the directory holding the package rather than by name across
    `sys.path`: a second copy installed elsewhere says nothing about this one."""

    for dist in metadata.distributions(
        name=DIST_NAME, path=[str(root if root is not None else _paths.package_parent())]
    ):
        return dist
    return None


def _direct_url(dist=None) -> dict | None:
    """The PEP 610 record of the install (private: it carries the URL), or None when there is none (an index install,
    no distribution, or an unreadable file -- the last is also 'not evidence of an
    index', and `kind` says so)."""
    if dist is None:
        return None
    try:
        text = dist.read_text("direct_url.json")
    except (ValueError, OSError):  # not UTF-8, or unreadable in a way read_text lets through
        return {}
    if text is None:
        # `read_text` also answers None for a file that exists but cannot be read.
        try:
            present = (Path(str(dist._path)) / "direct_url.json").exists()  # type: ignore[attr-defined]
        except Exception:
            present = False
        return {} if present else None
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _editable(record: dict) -> bool:
    info = record.get("dir_info")
    return bool(info.get("editable")) if isinstance(info, dict) else False


def _editable_record(root: Path) -> dict | None:
    """The PEP 610 record of an editable install of the tree at `root`, or None.

    An editable install leaves its dist-info in site-packages, not beside the package,
    so `own_distribution()` (which looks beside the package) cannot see it."""

    for dist in metadata.distributions(name=DIST_NAME):
        record = _direct_url(dist)
        if not record or not _editable(record):
            continue
        url = str(record.get("url", ""))
        if (
            url.startswith("file://")
            and Path(url2pathname(urlparse(url).path)).resolve() == root.resolve()
        ):
            return record
    return None


def _is_checkout(root: Path, dist) -> bool:
    """A checkout, not an install: outside site-packages AND no real dist-info beside
    the package. A build's `*.egg-info` in a checkout does not make it an install; a
    `*.dist-info` (an installer wrote it, e.g. `pip install --target`) does."""
    if not running_from_source(root):
        return False
    path = getattr(dist, "_path", None)
    return not (path is not None and str(path).endswith(".dist-info"))


def installed_from_index() -> bool:
    """True when this installation came from a package index, so `uvx ddflow-mcp`
    reaches the same project. No distribution metadata is not evidence of an index."""
    dist = own_distribution()
    if _is_checkout(_paths.package_parent(), dist):
        return False  # a checkout is never an index install, whatever egg-info it carries
    return dist is not None and _direct_url(dist) is None


def normalise_path(value: str | Path) -> str:
    """`value` with the home directory replaced by `~` wherever it occurs."""
    home = str(Path.home())
    if not home or home == "/":
        return str(value)
    # Only at a path-component boundary: `/home/ann` must not eat `/home/anna`.
    return re.sub(r"(?<![\w.-])" + re.escape(home) + r"(?=/|$|[^\w.-])", "~", str(value))


def is_own_dev_tree(root: Path | None = None) -> bool:
    """True when `root` (default: the tree this package runs from) is ddflow's own
    source: a pyproject named `ddflow-mcp` with `ddflow/` at the root, or an `origin`
    remote that is the upstream project."""
    root = Path(root) if root is not None else _paths.package_parent()
    pyproject = root / "pyproject.toml"
    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project")
    except (OSError, ValueError):
        project = None
    name = project.get("name") if isinstance(project, dict) else None
    if name == DIST_NAME and (root / "ddflow").is_dir():
        return True
    return bool(_OWN_REMOTE.search(_git(root, "remote", "get-url", "origin") or ""))


def _git(root: Path, *args: str) -> str | None:
    r = G.run(root, *args, timeout=P.TIMEOUTS["probe"])
    return r.out if r.ok and r.out else None


def _kind(dist, record: dict | None, from_source: bool) -> str:
    if record is not None:
        if "vcs_info" in record:
            return "vcs"
        if "dir_info" in record:
            return "editable" if _editable(record) else "local-dir"
        if "archive_info" in record:
            return "archive"
        return "unknown"
    if from_source:
        return "source-tree"
    return "index" if dist is not None else "unknown"


def _version(dist) -> str:
    if dist is not None:
        try:
            if dist.version:
                return str(dist.version)
        except Exception:
            pass
    return V.running() or "unknown"


def install_info(root: Path | None = None) -> InstallInfo:
    """Describe the running ddflow. `root` is the directory holding the `ddflow`
    package (default: where it was imported from)."""
    root = Path(root) if root is not None else _paths.package_parent()
    dist = own_distribution(root)
    from_source = _is_checkout(root, dist)
    version = _version(dist)
    record = _direct_url(dist)
    if from_source and record is None:
        # A checkout is never an index install, even when a build leaves an egg-info
        # (no direct_url.json) beside it; it is an editable install when one says so.
        record = _editable_record(root)
    kind = _kind(dist, record, from_source)
    commit = None
    if record and isinstance(record.get("vcs_info"), dict):
        commit = record["vcs_info"].get("commit_id")
    if commit is None and kind in ("source-tree", "editable"):
        commit = _git(root, "rev-parse", "HEAD")
    location = normalise_path(root) if kind in ("source-tree", "editable") else None
    return InstallInfo(
        version=version,
        kind=kind,
        commit=commit,
        is_own_dev_tree=is_own_dev_tree(root),
        location=location,
    )


def upgrade_advice(highest: str = "", install: InstallInfo | None = None) -> str:
    """What to do about a ddflow older than the data it met, fitted to how THIS one is
    installed (`core.version.upgrade_advice`): a source tree merges main, an index install
    upgrades its package. ``highest`` is the version needed, "" when unknown."""
    return CV.upgrade_advice(highest, (install or install_info()).kind)


__all__ = [
    "DIST_NAME",
    "KINDS",
    "METHODS",
    "ForeignLaunch",
    "InstallInfo",
    "foreign_launch_root",
    "install_info",
    "install_method",
    "installed_from_index",
    "is_own_dev_tree",
    "normalise_path",
    "own_distribution",
    "repoint_shared_editable",
    "running_from_source",
    "upgrade_advice",
]


#: How an installed ddflow can be brought up to date (`install_method`). `unknown` and the
#: kinds that are not an index install are never upgraded by ddflow itself.
METHODS = ("uv-tool", "pipx", "pip-venv", "uvx", "container", "source", "readonly", "other")


def _under(prefix: Path, *parts: str) -> bool:
    """Is ``prefix`` inside a directory chain ``.../p0/p1/...``? (path components, in order)"""
    names = prefix.parts
    for i in range(len(names) - len(parts) + 1):
        if names[i : i + len(parts)] == parts:
            return True
    return False


def _managed_by(pre: Path, tool_dir: str | None) -> str:
    """``pipx`` or ``uv-tool`` when ``pre`` is one of their environments (the default
    locations, or where ``PIPX_HOME``/``PIPX_LOCAL_VENVS``/``UV_TOOL_DIR`` moved them), else ""."""
    uv_dir = os.environ.get("UV_TOOL_DIR", "") if tool_dir is None else tool_dir
    pipx_venvs = os.environ.get("PIPX_LOCAL_VENVS") or (
        str(Path(os.environ["PIPX_HOME"]) / "venvs") if os.environ.get("PIPX_HOME") else ""
    )
    if _under(pre, "pipx", "venvs") or (pipx_venvs and pre.is_relative_to(pipx_venvs)):
        return "pipx"
    if _under(pre, "uv", "tools") or (uv_dir and pre.is_relative_to(uv_dir)):
        return "uv-tool"
    return ""


def install_method(
    info: InstallInfo | None = None,
    *,
    prefix: str | Path | None = None,
    base_prefix: str | Path | None = None,
    container: bool | None = None,
    writable: bool | None = None,
    tool_dir: str | None = None,
) -> str:
    """Which tool installed THIS ddflow, so the right command can upgrade it (D-self-upgrade 3).

    One of `METHODS`: ``container`` (an image: pull it and restart), ``source`` (a checkout or
    an editable install: git pull), ``uvx`` (an ephemeral environment: re-run it), ``uv-tool``,
    ``pipx``, ``pip-venv`` (a virtual environment's own pip), ``readonly`` (an install this
    user cannot write) and ``other`` (a system or user-site pip, a VCS or archive install:
    ddflow does not guess, the operator reinstalls). The arguments are the facts, injectable
    for tests; by default they are read from the running interpreter (and ``UV_TOOL_DIR``)."""
    info = info or install_info()
    pre = Path(prefix if prefix is not None else sys.prefix)
    base = Path(base_prefix if base_prefix is not None else sys.base_prefix)
    if container if container is not None else in_container():
        return "container"
    if info.kind in ("source-tree", "editable"):
        return "source"
    if any(p.startswith(("archive-v", "environments-v")) for p in pre.parts):
        return "uvx"  # uv's ephemeral environments, wherever UV_CACHE_DIR put them
    if not (writable if writable is not None else os.access(pre, os.W_OK)):
        return "readonly"  # a managed install this user cannot write is not upgraded from here
    if managed := _managed_by(pre, tool_dir):
        return managed
    if info.kind != "index":
        return "other"
    return "pip-venv" if pre != base else "other"


#: The editable-install ``.pth`` an installer writes next to ``ddflow_mcp``'s dist-info.
_EDITABLE_PTH = "_editable_impl_ddflow_mcp.pth"


@dataclass(frozen=True)
class ForeignLaunch:
    """A linked worktree the running package was imported FROM, and its primary checkout.

    Importing from a linked worktree is only legitimate when that worktree is the
    CALLER's own tree; imported from any OTHER lane's tree it is foreign — the shared
    editable install was repointed at a tree that is removed when its branch merges, and
    code from it must not run (bug B-cli-ran-code-from-another-lanes-worktree). Paths are
    resolved real paths; `normalise_path` renders them for a message.
    """

    worktree: Path
    primary: Path


def foreign_launch_root(root: Path | None = None) -> ForeignLaunch | None:
    """The linked worktree the running `ddflow` package was imported from, or None.

    A linked worktree whose PRIMARY holds a ddflow source tree too (`primary_checkout`) is
    a place the shared editable install must never end up, EXCEPT in the caller's own
    tree — the caller's own tree is legitimate, only another lane's tree is not, and the
    caller decides which this is (it is not this function's call). A checkout that is not
    a linked worktree, or a worktree of a repository that is not ddflow, is not a redirect
    away from anything and answers None. Pure: reads the git files `primary_checkout`
    already reads.
    """
    root = Path(root) if root is not None else _paths.package_parent()
    # ddflow's own throwaway scratch worktrees (scratch_tree: `ddflow-*` in the system temp
    # dir, and the `.merge-*`/`.version-*` trees under the worktree root) hold ddflow's own
    # code under test, not another lane's unmerged branch — a ci/pre-push run lives in one, so
    # importing from it is legitimate and must not read as foreign (B267ff88849).
    if root.name.startswith(("ddflow-", ".merge-", ".version-")):
        return None
    primary = _paths.primary_checkout(root)
    if primary is None:
        return None
    return ForeignLaunch(root, primary)


def _direct_url_path(dist) -> Path | None:
    """The `direct_url.json` filename for ``dist``'s dist-info, or None when it has none."""
    raw = getattr(dist, "_path", None)
    if raw is None:
        return None
    try:
        return Path(raw) / "direct_url.json"
    except (TypeError, ValueError):
        return None


def repoint_shared_editable(foreign: Path, primary: Path) -> bool:
    """Repoint the shared editable install of the tree at `foreign` back at `primary`.

    A shared editable install imported from a foreign lane's worktree has its
    `direct_url.json` URL and `_editable_impl_*.pth` pointing at that doomed tree; the
    next console-script call then crashes once the tree is removed. Rewrite both to the
    primary checkout so the next call imports code that outlives the tree. Returns True
    when any file changed; a file that cannot be read or written is left alone, never
    raised — this is a self-heal ahead of a refusal, and a failure to heal must not be a
    second crash.
    """
    changed = False
    primary_text = str(primary)
    primary_url = primary.as_uri()
    foreign_resolved = foreign.resolve()
    for dist in metadata.distributions(name=DIST_NAME):
        # `direct_url.json`: only an editable URL that names `foreign` is repointed, and
        # `dir_info.editable` (and any other key) is kept. The URL may name `foreign`
        # through an alias, so it is matched RESOLVED (as `_editable_record` does).
        record = _direct_url(dist)
        if record and _editable(record):
            url = record.get("url", "")
            try:
                target = Path(url2pathname(urlparse(url).path)).resolve()
            except (TypeError, ValueError, OSError):
                target = None
            if target is not None and target == foreign_resolved:
                record = dict(record)
                record["url"] = primary_url
                wrote = _direct_url_path(dist)
                try:
                    if wrote is not None:
                        wrote.write_text(json.dumps(record))
                        changed = True
                except OSError:
                    pass
        # The `.pth` itself: a single line naming the import root. `_direct_url_path` is
        # `<site-packages>/<dist>.dist-info/direct_url.json`, so the `.pth` sits two levels
        # up, beside the dist-info rather than inside it.
        path = _direct_url_path(dist)
        if path is None:
            continue
        pth = path.parent.parent / _EDITABLE_PTH
        try:
            line = pth.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        try:
            # The `.pth` line names the import root as the installer wrote it, possibly
            # through a symlink (`/tmp` -> `/private/tmp`), so it is matched RESOLVED,
            # exactly like the URL above: a repoint that skips this file strands the
            # foreign tree on sys.path and turns the refusal into a lockout.
            same = pth.is_file() and Path(line).resolve() == foreign_resolved
        except (OSError, ValueError):
            same = False
        if same:
            try:
                pth.write_text(primary_text + "\n")
                changed = True
            except OSError:
                pass
    return changed
