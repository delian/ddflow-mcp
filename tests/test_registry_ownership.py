"""The MCP registry can verify that this server owns every package it lists.

publish #36 failed at `mcp-publisher publish` with: "PyPI package 'ddflow-mcp' ownership
validation failed. The server name 'io.github.delian/ddflow-mcp' must appear as
'mcp-name: io.github.delian/ddflow-mcp' in the package README". The registry reads that
line from the PyPI long description -- README.md -- and, for an OCI package, the image
label `io.modelcontextprotocol.server.name`. Neither was there, and the registry checks
the exact PyPI version server.json names, so the failure surfaced only at the last job,
after PyPI and both image registries had already published the version.

This file is the ONE definition of the check: the publish workflow's `verify` job runs it
before anything is uploaded, so the two cannot drift apart.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = json.loads((ROOT / "server.json").read_text("utf-8"))["name"]
WORKFLOW = (ROOT / ".github" / "workflows" / "publish.yml").read_text("utf-8")


def _mcp_name_present(readme: str, name: str = NAME) -> bool:
    """The registry's rule: the token, then a boundary -- `ddflow-mcp-x` is another name."""
    return bool(re.search(rf"mcp-name:\s*{re.escape(name)}(?=\s|-->|<|$)", readme, re.M))


def _labelled(dockerfile: str, name: str = NAME) -> bool:
    """A LABEL instruction that is not commented out carries the server name."""
    live = "\n".join(ln for ln in dockerfile.splitlines() if not ln.lstrip().startswith("#"))
    return bool(re.search(rf'io\.modelcontextprotocol\.server\.name="{re.escape(name)}"', live))


def _job(name: str) -> str:
    """One job's text, from its key to the next top-level job key, in any order."""
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z][\w-]*:\n|\Z)", WORKFLOW, re.M | re.S)
    assert m, f"no job {name!r} in publish.yml"
    return m.group(1)


def test_the_pypi_readme_names_the_server():
    assert _mcp_name_present((ROOT / "README.md").read_text("utf-8"))


def test_the_image_is_labelled_with_the_server_name():
    assert _labelled((ROOT / "Dockerfile").read_text("utf-8"))


def test_the_checks_are_not_fooled_by_near_misses():
    assert not _mcp_name_present(f"mcp-name: {NAME}-x\n")
    assert _mcp_name_present(f"<!-- mcp-name: {NAME} -->")
    assert not _labelled(f'# LABEL io.modelcontextprotocol.server.name="{NAME}"\n')


def test_the_readme_is_the_long_description_that_reaches_pypi():
    """The line counts only in the file PyPI shows."""
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    assert project["readme"] == "README.md"


def test_verify_runs_this_check_and_every_publishing_job_waits_for_it():
    """Before PyPI or an image registry holds the version: a PyPI version cannot be
    replaced, and `:latest` cannot be taken back."""
    assert re.search(r"run:.*pytest tests/test_registry_ownership\.py", _job("verify"))
    for job in ("pypi", "docker"):
        needs = re.search(r"^    needs:\s*(.+)$", _job(job), re.M)
        assert needs and "verify" in needs.group(1), f"{job} does not need verify"


def test_a_readme_change_releases():
    """README.md is the PyPI page: a push that changes only it must publish, or the
    ownership line never reaches the registry."""
    on = WORKFLOW[WORKFLOW.index("\non:") : WORKFLOW.index("\njobs:")]
    for path in ("README.md", "server.json"):
        assert re.search(rf'^\s*-\s*"?{re.escape(path)}"?\s*(#.*)?$', on, re.M), path
