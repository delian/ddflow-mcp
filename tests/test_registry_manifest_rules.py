"""server.json obeys the rules the MCP registry enforces on publish -- checked offline.

publish #40 failed at the LAST job, `mcp-publisher publish`, with 400 "registry validation
failed for package 1 (docker.io/delian/ddflow-mcp:0.1.8): OCI packages must not have
'version' field - include version in 'identifier' instead". PyPI and both image registries
had already shipped 0.1.8. The published JSON schema does not express that rule (its
Package.version is a plain optional string); it lives in the registry's Go code
(internal/validators/registries/{oci,pypi,nuget,mcpb}.go and validators.go), so a schema
check would have passed. The rules are encoded here, per registryType, and the publish
workflow's `verify` job runs this file before anything is uploaded.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = json.loads((ROOT / "server.json").read_text("utf-8"))
WORKFLOW = (ROOT / ".github" / "workflows" / "publish.yml").read_text("utf-8")

# oci.go allowedOCIRegistries (plus *.pkg.dev and *.azurecr.io).
OCI_REGISTRIES = {
    "docker.io",
    "registry-1.docker.io",
    "index.docker.io",
    "ghcr.io",
    "quay.io",
    "mcr.microsoft.com",
}
PYPI_URL = "https://pypi.org"
NUGET_URL = "https://api.nuget.org/v3/index.json"
# validators.go namespacePattern/namePartPattern: alphanumeric at both ends.
NAME_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?/[a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?$"
)
RANGE_RE = re.compile(r"^\s*(?:\^|~|>=|<=|>|<|=)|\s-\s|\|\||[xX*]")


def _oci(pkg: dict, at: str, ver: str) -> list[str]:
    """oci.go ValidateOCI: the canonical reference carries everything."""
    out = [
        f"{at}: OCI packages must not have '{banned}' (put the tag in identifier)"
        for banned in ("version", "registryBaseUrl", "fileSha256")
        if banned in pkg
    ]
    ident = pkg.get("identifier", "")
    host = ident.split("/")[0]
    if host not in OCI_REGISTRIES and not host.endswith((".pkg.dev", ".azurecr.io")):
        out.append(f"{at}: OCI registry {host!r} is not on the registry's allowlist")
    if ":" not in ident.rsplit("/", 1)[-1] or ident.endswith(":latest"):
        out.append(f"{at}: OCI identifier needs a specific tag")
    elif ident.rsplit(":", 1)[1] != ver:
        out.append(f"{at}: tag is not the declared version {ver}")
    return out


def _pypi(pkg: dict, at: str, ver: str) -> list[str]:
    out = []
    if not pkg.get("version"):
        out.append(f"{at}: PyPI packages need 'version'")
    elif pkg["version"] != ver:
        out.append(f"{at}: version {pkg['version']} is not the declared {ver}")
    if "fileSha256" in pkg:
        out.append(f"{at}: PyPI packages must not have 'fileSha256'")
    if pkg.get("registryBaseUrl", PYPI_URL) != PYPI_URL:
        out.append(f"{at}: registryBaseUrl must be {PYPI_URL}")
    return out


def _nuget(pkg: dict, at: str, ver: str) -> list[str]:
    out = []
    if not pkg.get("version"):
        out.append(f"{at}: NuGet packages need 'version'")
    if "fileSha256" in pkg:
        out.append(f"{at}: NuGet packages must not have 'fileSha256'")
    if pkg.get("registryBaseUrl", NUGET_URL) != NUGET_URL:
        out.append(f"{at}: registryBaseUrl must be {NUGET_URL}")
    return out


def _mcpb(pkg: dict, at: str, ver: str) -> list[str]:
    out = []
    if not pkg.get("fileSha256"):
        out.append(f"{at}: MCPB packages need 'fileSha256'")
    if "registryBaseUrl" in pkg:
        out.append(f"{at}: MCPB packages must not have 'registryBaseUrl'")
    return out


PACKAGE_RULES = {"oci": _oci, "pypi": _pypi, "nuget": _nuget, "mcpb": _mcpb}


def problems(srv: dict) -> list[str]:
    """Every rule the registry would reject `srv` for, as readable strings."""
    out: list[str] = []
    if not NAME_RE.fullmatch(srv.get("name", "")):
        out.append(f"name {srv.get('name')!r} is not 'namespace/name'")
    ver = srv.get("version", "")
    if not ver or ver == "latest" or RANGE_RE.search(ver):
        out.append(f"version {ver!r} must be one specific version")
    repo = srv.get("repository") or {}
    if repo.get("source") == "github" and not re.fullmatch(
        r"https://github\.com/[^/]+/[^/]+", repo.get("url", "")
    ):
        out.append(f"repository url {repo.get('url')!r} is not a github repo URL")
    for i, pkg in enumerate(srv.get("packages", [])):
        at = f"package {i} ({pkg.get('identifier')})"
        kind = pkg.get("registryType")
        if not pkg.get("identifier"):
            out.append(f"{at}: identifier is required")
        transport = pkg.get("transport") or {}
        if transport.get("type") == "stdio" and transport.get("url"):
            out.append(f"{at}: url must be empty for stdio")
        if kind in PACKAGE_RULES:
            out += PACKAGE_RULES[kind](pkg, at, ver)
        else:
            out.append(
                f"{at}: registryType {kind!r} is not one this check knows; read the registry's rules first"
            )
    return out


def test_server_json_passes_the_registrys_package_rules():
    assert problems(SERVER) == []


def test_the_oci_packages_carry_no_version_but_the_pypi_one_does():
    by_type: dict[str, list[dict]] = {}
    for pkg in SERVER["packages"]:
        by_type.setdefault(pkg["registryType"], []).append(pkg)
    assert len(by_type.get("oci", [])) >= 2
    for pkg in by_type["oci"]:
        assert "version" not in pkg, (
            f"{pkg['identifier']}: the registry rejects an OCI 'version' (publish #40)"
        )
        assert pkg["identifier"].endswith(f":{SERVER['version']}"), pkg["identifier"]
    (pypi,) = by_type["pypi"]
    assert pypi["version"] == SERVER["version"]


def test_the_server_version_is_the_one_declared_in_init_py():
    """pyproject.toml has no version (hatch reads ddflow/__init__.py); server.json's is
    rendered from it, so the two cannot be different numbers in a committed tree."""
    m = re.search(
        r'^__version__ = "([^"]+)"$', (ROOT / "ddflow" / "__init__.py").read_text("utf-8"), re.M
    )
    assert m and SERVER["version"] == m.group(1)


def test_the_rules_catch_what_publish_40_hit():
    bad = copy.deepcopy(SERVER)
    for pkg in bad["packages"]:
        if pkg["registryType"] == "oci":
            pkg["version"] = bad["version"]
    found = problems(bad)
    assert found and all("OCI packages must not have 'version'" in p for p in found), found


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda s: s["packages"][0].pop("version"), "PyPI packages need 'version'"),
        (lambda s: s["packages"][1].update(registryBaseUrl="https://docker.io"), "registryBaseUrl"),
        (
            lambda s: s["packages"][1].update(identifier="docker.io/delian/ddflow-mcp:latest"),
            "specific tag",
        ),
        (
            lambda s: s["packages"][1].update(identifier="docker.io/delian/ddflow-mcp"),
            "specific tag",
        ),
        (
            lambda s: s["packages"][1].update(
                identifier="evil.example/delian/ddflow-mcp:" + s["version"]
            ),
            "allowlist",
        ),
        (lambda s: s.update(version="^0.1.0"), "specific version"),
        (lambda s: s.update(name="ddflow-mcp"), "namespace/name"),
        (lambda s: s["packages"][0]["transport"].update(url="https://x"), "stdio"),
        (
            lambda s: s["packages"].append(
                {"registryType": "cargo", "identifier": "x", "transport": {"type": "stdio"}}
            ),
            "not one this check knows",
        ),
    ],
)
def test_each_rule_fires(mutate, needle):
    bad = copy.deepcopy(SERVER)
    mutate(bad)
    assert any(needle in p for p in problems(bad)), problems(bad)


def test_the_verify_job_runs_these_rules_before_anything_is_uploaded():
    """The lesson of publish #40: the registry's rules must fail `verify`, not the last job."""
    verify = WORKFLOW.split("\n  verify:", 1)[1].split("\n  pypi:", 1)[0]
    assert "tests/test_registry_manifest_rules.py" in verify
