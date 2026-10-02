"""The public install-info helper: version, install kind and commit from PEP 610.

Each kind is built as a real dist-info with a fixture `direct_url.json` and read back by
`importlib.metadata`; no private path, home directory or URL credential may appear in
what the helper returns.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.infra import paths as PATHS
from ddflow.services import adopt as A
from ddflow.services import enforce as E
from ddflow.services import install_info as I

COMMIT = "a1b2c3d4" * 5


def _site(tmp_path: Path, direct_url: dict | None, version: str = "9.9.9") -> Path:
    site = tmp_path / "env" / "lib" / "site-packages"
    (site / "ddflow").mkdir(parents=True)
    info = site / f"ddflow_mcp-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: ddflow-mcp\nVersion: {version}\n")
    if direct_url is not None:
        (info / "direct_url.json").write_text(json.dumps(direct_url))
    return site


@pytest.fixture
def installed(tmp_path, monkeypatch):
    def make(direct_url, **kw):
        site = _site(tmp_path, direct_url, **kw)
        monkeypatch.setattr(PATHS, "package_parent", lambda: site)
        monkeypatch.setattr(I, "running_from_source", lambda *a: False)
        return I.install_info(root=site)

    return make


def test_helper_is_public():
    assert callable(I.install_info) and callable(I.running_from_source)


def test_index_install(installed):
    info = installed(None)
    assert (info.version, info.kind, info.commit) == ("9.9.9", "index", None)
    assert info.is_own_dev_tree is False


def test_vcs_install_carries_commit(installed):
    info = installed(
        {
            "url": "https://example.invalid/x/ddflow-mcp",
            "vcs_info": {"vcs": "git", "commit_id": COMMIT},
        }
    )
    assert (info.kind, info.commit) == ("vcs", COMMIT)


def test_local_dir_install(installed):
    info = installed({"url": "file:///home/someone/src/ddflow", "dir_info": {}})
    assert info.kind == "local-dir"
    assert "someone" not in json.dumps(info.as_dict())


def test_editable_install(installed):
    info = installed({"url": "file:///home/someone/src/ddflow", "dir_info": {"editable": True}})
    assert info.kind == "editable"


def test_archive_install(installed):
    assert (
        installed({"url": "https://example.invalid/a.tar.gz", "archive_info": {}}).kind == "archive"
    )


def test_credentials_in_url_are_never_returned(installed):
    info = installed(
        {
            "url": "https://tok:secret@example.invalid/x",
            "vcs_info": {"vcs": "git", "commit_id": COMMIT},
        }
    )
    assert "secret" not in json.dumps(info.as_dict())


def test_no_distribution_outside_a_checkout_is_unknown(tmp_path, monkeypatch):
    site = tmp_path / "site-packages"
    (site / "ddflow").mkdir(parents=True)
    monkeypatch.setattr(PATHS, "package_parent", lambda: site)
    monkeypatch.setattr(I, "running_from_source", lambda *a: False)
    info = I.install_info(root=site)
    assert info.kind == "unknown" and info.is_own_dev_tree is False


def test_source_tree_reads_the_commit_from_git(tmp_path, monkeypatch):
    tree = tmp_path / "checkout"
    (tree / "ddflow").mkdir(parents=True)

    def git(*a):
        return subprocess.run(
            ["git", "-C", str(tree), *a], check=True, capture_output=True, text=True
        )

    git("init", "-q")
    git(
        "-c",
        "commit.gpgsign=false",
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    head = git("rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)
    monkeypatch.setattr(I, "running_from_source", lambda *a: True)
    info = I.install_info(root=tree)
    assert (info.kind, info.commit) == ("source-tree", head)


def test_home_directory_is_normalised(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert I.normalise_path(tmp_path / "src" / "x") == "~/src/x"
    assert str(tmp_path) not in I.normalise_path(f"see {tmp_path}/a/b")


def test_own_dev_tree_true_for_this_repo():
    repo = Path(__file__).resolve().parents[1]
    assert I.is_own_dev_tree(repo) is True


def test_own_dev_tree_false_for_a_fixture_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "other-project"\n')
    (tmp_path / "ddflow").mkdir()
    assert I.is_own_dev_tree(tmp_path) is False


def test_own_dev_tree_by_origin_remote(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "remote",
            "add",
            "origin",
            "git@github.com:delian/ddflow-mcp.git",
        ],
        check=True,
    )
    assert I.is_own_dev_tree(tmp_path) is True


def test_adopt_and_enforce_share_the_one_implementation():
    assert E.running_from_source is I.running_from_source
    assert E._running_from_source() == I.running_from_source() == A._running_from_source()
    assert A.DIST_NAME == I.DIST_NAME


def _checkout(tmp_path: Path) -> Path:
    tree = tmp_path / "checkout"
    (tree / "ddflow").mkdir(parents=True)
    return tree


def test_editable_install_is_found_in_site_packages(tmp_path, monkeypatch):
    """PEP 660: the dist-info (and its direct_url.json) lives in site-packages, not
    beside the package, so it is looked up by name and matched on the tree."""
    tree = _checkout(tmp_path)
    site = tmp_path / "site"
    info = site / "ddflow_mcp-1.2.3.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: ddflow-mcp\nVersion: 1.2.3\n")
    (info / "direct_url.json").write_text(
        json.dumps({"url": tree.as_uri(), "dir_info": {"editable": True}})
    )
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)
    monkeypatch.setattr(I, "running_from_source", lambda *a: True)
    assert I.install_info(root=tree).kind == "editable"


def test_editable_url_is_percent_decoded(tmp_path, monkeypatch):
    tree = tmp_path / "my project"
    (tree / "ddflow").mkdir(parents=True)
    site = tmp_path / "site"
    info = site / "ddflow_mcp-1.2.3.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: ddflow-mcp\nVersion: 1.2.3\n")
    (info / "direct_url.json").write_text(
        json.dumps({"url": tree.as_uri(), "dir_info": {"editable": True}})
    )
    assert "%20" in tree.as_uri()
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setattr(I, "running_from_source", lambda *a: True)
    assert I.install_info(root=tree).kind == "editable"


def test_egg_info_in_a_checkout_is_not_an_index_install(tmp_path, monkeypatch):
    tree = _checkout(tmp_path)
    egg = tree / "ddflow_mcp.egg-info"
    egg.mkdir()
    (egg / "PKG-INFO").write_text("Metadata-Version: 2.1\nName: ddflow-mcp\nVersion: 1.2.3\n")
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)
    monkeypatch.setattr(I, "running_from_source", lambda *a: True)
    info = I.install_info(root=tree)
    assert info.kind == "source-tree" and info.version == "1.2.3"


def test_home_replacement_respects_path_boundaries(monkeypatch):
    monkeypatch.setenv("HOME", "/home/ann")
    assert I.normalise_path("/home/anna/checkout") == "/home/anna/checkout"
    assert I.normalise_path("/home/ann/checkout") == "~/checkout"
    assert I.normalise_path("/home/ann") == "~"


def test_root_selects_the_distribution(tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    site = _site(
        tmp_path / "a",
        {"url": "https://example.invalid/x", "vcs_info": {"vcs": "git", "commit_id": COMMIT}},
        version="4.5.6",
    )
    monkeypatch.setattr(PATHS, "package_parent", lambda: other)
    monkeypatch.setattr(I, "running_from_source", lambda *a: False)
    assert I.install_info(root=site).version == "4.5.6"
    assert I.install_info(root=site).kind == "vcs"
    assert I.install_info().kind == "unknown"


def test_from_source_follows_root_not_the_calling_interpreter(tmp_path):
    """No monkeypatching: an installed fixture under site-packages is judged by ITS
    path, whatever tree the test itself runs from."""
    site = _site(
        tmp_path,
        {"url": "https://example.invalid/x", "vcs_info": {"vcs": "git", "commit_id": COMMIT}},
    )
    info = I.install_info(root=site)
    assert (info.kind, info.commit) == ("vcs", COMMIT)


def test_missing_distribution_never_borrows_the_running_installs_record(tmp_path):
    site = tmp_path / "x" / "site-packages"
    (site / "ddflow").mkdir(parents=True)
    assert I._direct_url(None) is None
    assert I.install_info(root=site).kind == "unknown"


def test_home_replacement_needs_a_leading_boundary(monkeypatch):
    monkeypatch.setenv("HOME", "/home/ann")
    assert I.normalise_path("/srv/backup/home/ann/ddflow") == "/srv/backup/home/ann/ddflow"


def test_non_table_project_in_pyproject_is_tolerated(tmp_path):
    (tmp_path / "pyproject.toml").write_text('project = "x"\n')
    assert I.is_own_dev_tree(tmp_path) is False


def test_unreadable_direct_url_is_not_an_index_install(installed):
    import os

    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    info = installed(
        {"url": "https://example.invalid/x", "vcs_info": {"vcs": "git", "commit_id": COMMIT}}
    )
    assert info.kind == "vcs"
    site = Path(I._paths.package_parent())
    record = next(site.glob("*.dist-info")) / "direct_url.json"
    record.chmod(0)
    try:
        assert I.install_info(root=site).kind == "unknown"
        assert I.installed_from_index() is False
    finally:
        record.chmod(0o644)


def test_a_real_record_beside_the_package_survives_a_source_run(tmp_path, monkeypatch):
    tree = tmp_path / "checkout"
    (tree / "ddflow").mkdir(parents=True)
    info = tree / "ddflow_mcp-1.2.3.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: ddflow-mcp\nVersion: 1.2.3\n")
    (info / "direct_url.json").write_text(
        json.dumps(
            {"url": "https://example.invalid/x", "vcs_info": {"vcs": "git", "commit_id": COMMIT}}
        )
    )
    monkeypatch.setattr(I, "running_from_source", lambda *a: True)
    got = I.install_info(root=tree)
    assert (got.kind, got.commit) == ("vcs", COMMIT)


def test_a_checkout_with_an_egg_info_is_not_from_the_index(tmp_path, monkeypatch):
    tree = _checkout(tmp_path)
    egg = tree / "ddflow_mcp.egg-info"
    egg.mkdir()
    (egg / "PKG-INFO").write_text("Metadata-Version: 2.1\nName: ddflow-mcp\nVersion: 1.2.3\n")
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)
    assert I.installed_from_index() is False
