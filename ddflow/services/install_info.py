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
    location: str | None = None  # home-normalised; only for a source tree or local install

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def running_from_source() -> bool:
    """True when this package lives in a checkout rather than in site-packages."""
    here = Path(__file__).resolve()
    return not any(part in ("site-packages", "dist-packages") for part in here.parts)


def own_distribution():
    """The installed distribution that THIS `ddflow` package came from, or None.

    Looked up in the directory holding the package rather than by name across
    `sys.path`: a second copy installed elsewhere says nothing about this one."""
    from importlib import metadata

    for dist in metadata.distributions(name=DIST_NAME, path=[str(_paths.package_parent())]):
        return dist
    return None


def direct_url(dist=None) -> dict | None:
    """The PEP 610 record of the install, or None when there is none (an index install,
    no distribution, or an unreadable file -- the last is also 'not evidence of an
    index', and `kind` says so)."""
    dist = dist if dist is not None else own_distribution()
    if dist is None:
        return None
    text = dist.read_text("direct_url.json")
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def installed_from_index() -> bool:
    """True when this installation came from a package index, so `uvx ddflow-mcp`
    reaches the same project. No distribution metadata is not evidence of an index."""
    dist = own_distribution()
    return dist is not None and dist.read_text("direct_url.json") is None


def normalise_path(value: str | Path) -> str:
    """`value` with the home directory replaced by `~` wherever it occurs."""
    text = str(value)
    home = str(Path.home())
    if home and home != "/":
        text = text.replace(home, "~")
    return text


def is_own_dev_tree(root: Path | None = None) -> bool:
    """True when `root` (default: the tree this package runs from) is ddflow's own
    source: a pyproject named `ddflow-mcp` with `ddflow/` at the root, or an `origin`
    remote that is the upstream project."""
    root = Path(root) if root is not None else _paths.package_parent()
    pyproject = root / "pyproject.toml"
    try:
        name = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}).get("name")
    except (OSError, ValueError):
        name = None
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
    if dist is None:
        return "source-tree" if from_source else "unknown"
    if record is None:
        return "index"
    if "vcs_info" in record:
        return "vcs"
    if "dir_info" in record:
        return "editable" if (record["dir_info"] or {}).get("editable") else "local-dir"
    if "archive_info" in record:
        return "archive"
    return "unknown"


def _version(dist) -> str:
    if dist is not None:
        try:
            return dist.version
        except Exception:
            pass
    try:
        from .. import __version__

        return str(__version__)
    except ImportError:
        return "unknown"


def install_info(root: Path | None = None) -> InstallInfo:
    """Describe the running ddflow. `root` is the directory holding the `ddflow`
    package (default: where it was imported from)."""
    root = Path(root) if root is not None else _paths.package_parent()
    from_source = running_from_source()
    dist = own_distribution()
    record = direct_url(dist)
    kind = _kind(dist, record, from_source)
    commit = None
    if record and isinstance(record.get("vcs_info"), dict):
        commit = record["vcs_info"].get("commit_id")
    if commit is None and kind in ("source-tree", "editable"):
        commit = _git(root, "rev-parse", "HEAD")
    location = normalise_path(root) if kind in ("source-tree", "editable", "local-dir") else None
    return InstallInfo(
        version=_version(dist),
        kind=kind,
        commit=commit,
        is_own_dev_tree=is_own_dev_tree(root),
        location=location,
    )


__all__ = [
    "DIST_NAME",
    "KINDS",
    "InstallInfo",
    "direct_url",
    "install_info",
    "installed_from_index",
    "is_own_dev_tree",
    "normalise_path",
    "own_distribution",
    "running_from_source",
]
