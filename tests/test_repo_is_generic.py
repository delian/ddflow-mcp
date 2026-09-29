"""The repository ships generic: nobody's own services are committed.

Operator, 2026-09-29: the critic LLM, roborev and companion configurations stay local to
whoever runs the project, and are never pushed -- ddflow recommends services, it does
not hand a new user someone else's. They live in git-ignored files that ddflow reads
beside the committed config (`.ddflow/reviewers.toml`, `.ddflow/gates.toml`).

This is the check that holds every agent to it, because every agent's unit_tests gate
runs it: a private-network address in a tracked file, or a reviewer in the committed
config, fails the suite.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Not scanned, each for its reason:
#: - the event log is history, and verbatim operator words; session-prompt redaction
#:   covers it going forward (B-local-config), and history is not rewritten here;
#: - .roborev.toml leaves the index in B-local-roborev -- remove this entry there.
EXEMPT = (".ddflow/events/", ".roborev.toml")

_IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")

#: Addresses that are made up, not anybody's host -- each with where and why. An entry
#: here is a claim someone can check, which an exempted directory is not.
EXAMPLES = {
    "10.0.0.5": "tests/test_container.py: a fabricated container-host address",
}


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def _private_addresses(text: str) -> set[str]:
    found = set()
    for m in _IPV4.finditer(text):
        try:
            ip = ipaddress.IPv4Address(m.group(1))
        except ValueError:
            continue
        # Loopback is the documented default for a local endpoint; 0.0.0.0 is "any
        # interface", not a host.
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or str(ip) in EXAMPLES:
            continue
        if ip.is_private:
            found.add(str(ip))
    return found


def test_no_tracked_file_names_a_private_network_host():
    hits = {}
    for path in _tracked():
        if path.startswith(EXEMPT):
            continue
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found = _private_addresses(text)
        if found:
            hits[path] = sorted(found)
    assert not hits, (
        f"private-network hosts in committed files: {hits}. A host is somebody's own "
        "service: put it in a git-ignored local file (.ddflow/reviewers.toml, "
        ".ddflow/gates.toml, an untracked .roborev.toml)."
    )


def test_the_committed_config_names_no_reviewer():
    data = tomllib.loads((ROOT / ".ddflow" / "config.toml").read_text(encoding="utf-8"))
    assert not data.get("reviewer"), (
        "a [[reviewer]] in the committed .ddflow/config.toml ships one person's endpoint "
        "to every clone; it belongs in the git-ignored .ddflow/reviewers.toml"
    )


def test_the_local_files_are_ignored():
    for path in (".ddflow/reviewers.toml", ".ddflow/gates.toml"):
        r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", path])
        assert r.returncode == 0, f"{path} is not git-ignored, so it would be committed"


def _ip(*parts: int) -> str:
    """An address built at run time, so this file -- which the check also scans -- holds
    no dotted literal of its own and needs no exemption."""
    return ".".join(map(str, parts))


def test_the_address_check_can_see_one():
    """The check above passing means nothing unless it can fail."""
    lan, home, corp = _ip(10, 220, 1, 8), _ip(192, 168, 0, 4), _ip(172, 16, 9, 9)
    assert _private_addresses(f'base_url = "http://{lan}:8000/v1"') == {lan}
    assert _private_addresses(f"{home} and {corp}") == {home, corp}
    benign = (
        f"{_ip(127, 0, 0, 1)}:8000, {_ip(0, 0, 0, 0)}, version {_ip(1, 2, 3, 4)}, {_ip(8, 8, 8, 8)}"
    )
    assert _private_addresses(benign) == set()
