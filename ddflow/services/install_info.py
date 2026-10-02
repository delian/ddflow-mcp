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
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from ..infra import paths as _paths
from ..infra import proc as P

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
    here = Path(root).resolve() if root is not None else Path(__file__).resolve()
    return not any(part in ("site-packages", "dist-packages") for part in here.parts)


def own_distribution(root: Path | None = None):
    """The installed distribution that THIS `ddflow` package came from, or None.

    Looked up in the directory holding the package rather than by name across
    `sys.path`: a second copy installed elsewhere says nothing about this one."""
    from importlib import metadata

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
    text = dist.read_text("direct_url.json")
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


def _editable_record(root: Path) -> dict | None:
    """The PEP 610 record of an editable install of the tree at `root`, or None.

    An editable install leaves its dist-info in site-packages, not beside the package,
    so `own_distribution()` (which looks beside the package) cannot see it."""
    from importlib import metadata

    for dist in metadata.distributions(name=DIST_NAME):
        record = _direct_url(dist)
        if not record or not (record.get("dir_info") or {}).get("editable"):
            continue
        url = str(record.get("url", ""))
        if (
            url.startswith("file://")
            and Path(url2pathname(urlparse(url).path)).resolve() == root.resolve()
        ):
            return record
    return None


def installed_from_index() -> bool:
    """True when this installation came from a package index, so `uvx ddflow-mcp`
    reaches the same project. No distribution metadata is not evidence of an index."""
    dist = own_distribution()
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
    try:
        r = P.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)
    except (OSError, ValueError):
        return None
    out = (r.stdout or "").strip()
    return out if r.returncode == 0 and out else None


def _kind(dist, record: dict | None, from_source: bool) -> str:
    if record is not None:
        if "vcs_info" in record:
            return "vcs"
        if "dir_info" in record:
            return "editable" if (record["dir_info"] or {}).get("editable") else "local-dir"
        if "archive_info" in record:
            return "archive"
        return "unknown"
    if from_source:
        return "source-tree"
    return "index" if dist is not None else "unknown"


def _version(dist) -> str:
    if dist is not None:
        try:
            return dist.version
        except Exception:
            pass
    import ddflow

    return str(getattr(ddflow, "__version__", "unknown"))


def install_info(root: Path | None = None) -> InstallInfo:
    """Describe the running ddflow. `root` is the directory holding the `ddflow`
    package (default: where it was imported from)."""
    root = Path(root) if root is not None else _paths.package_parent()
    from_source = running_from_source(root)
    dist = own_distribution(root)
    version = _version(dist)
    record = _direct_url(dist)
    if from_source:
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


__all__ = [
    "DIST_NAME",
    "KINDS",
    "InstallInfo",
    "install_info",
    "installed_from_index",
    "is_own_dev_tree",
    "normalise_path",
    "own_distribution",
    "running_from_source",
]
