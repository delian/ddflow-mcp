"""The MCP registry can verify that this server owns every package it lists.

publish #36 failed at `mcp-publisher publish` with: "PyPI package 'ddflow-mcp' ownership
validation failed. The server name 'io.github.delian/ddflow-mcp' must appear as
'mcp-name: io.github.delian/ddflow-mcp' in the package README". The registry reads that
line from the PyPI long description -- README.md -- and, for an OCI package, the image
label `io.modelcontextprotocol.server.name`. Neither was there, and the registry checks
the exact PyPI version server.json names, so the failure surfaced only at the last job,
after PyPI and both image registries had already published the version.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = json.loads((ROOT / "server.json").read_text("utf-8"))["name"]


def test_the_pypi_readme_names_the_server():
    assert f"mcp-name: {NAME}" in (ROOT / "README.md").read_text("utf-8")


def test_the_image_is_labelled_with_the_server_name():
    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    assert re.search(rf'io\.modelcontextprotocol\.server\.name="{re.escape(NAME)}"', dockerfile)


def test_the_readme_is_the_long_description_that_reaches_pypi():
    """The line counts only in the file PyPI shows."""
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    assert project["readme"] == "README.md"


def test_the_publish_workflow_checks_ownership_before_uploading_anything():
    """A release the registry will refuse must fail BEFORE PyPI holds the version: a
    PyPI version cannot be replaced, so the next attempt needs a new number."""
    wf = (ROOT / ".github" / "workflows" / "publish.yml").read_text("utf-8")
    pypi_job = wf[wf.index("\n  pypi:") : wf.index("\n  docker:")]
    assert "mcp-name:" in pypi_job and "io.modelcontextprotocol.server.name" in pypi_job


def test_a_readme_change_releases():
    """README.md is the PyPI page: a push that changes only it must publish, or the
    ownership line never reaches the registry."""
    wf = (ROOT / ".github" / "workflows" / "publish.yml").read_text("utf-8")
    paths = wf[wf.index("paths:") : wf.index("tags:")]
    assert '"README.md"' in paths and '"server.json"' in paths
