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

#: Not scanned, each for its reason. EXACT paths and directory prefixes are kept apart,
#: so an exemption for one file never covers its neighbours (`.roborev.toml.example`).
#: - the event log is history, and verbatim operator words; session-prompt redaction
#:   covers it going forward (B-local-config), and history is not rewritten here;
#: - .roborev.toml leaves the index in B-local-roborev -- remove this entry there.
EXEMPT_DIRS = (".ddflow/events/",)
EXEMPT_FILES = frozenset({".roborev.toml"})

#: Made-up addresses, each allowed ONLY in the file that uses it as a fixture: the same
#: address anywhere else is a host like any other.
EXAMPLES = {
    # a fabricated container-host address; spelled in pieces, as every address in this
    # file is, because the check scans this file too
    "tests/test_container.py": {"10.0.0" + ".5"},
}

#: A dotted quad not glued to more digits or octets. A full stop that ENDS a sentence
#: ("the host is at 10.x.y.z.") is punctuation; only a dot followed by a digit
#: disqualifies.
_IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?!\d|\.\d)")
#: Candidate IPv6 text: hex groups with at least two colons. Parsing decides.
_IPV6 = re.compile(r"(?<![\w:])([0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7})(?![\w:])")


def _scanned(path: str) -> bool:
    return path not in EXEMPT_FILES and not path.startswith(EXEMPT_DIRS)


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def _private_addresses(text: str, *, path: str = "") -> set[str]:
    allowed = EXAMPLES.get(path, set())
    found = set()
    candidates = [(m.group(1), ipaddress.IPv4Address) for m in _IPV4.finditer(text)]
    candidates += [(m.group(1), ipaddress.IPv6Address) for m in _IPV6.finditer(text)]
    for raw, kind in candidates:
        try:
            ip = kind(raw)
        except ValueError:
            continue
        # Loopback is the documented default for a local endpoint; the unspecified
        # address ("any interface") is not a host.
        if ip.is_loopback or ip.is_unspecified or raw in allowed:
            continue
        if ip.is_private or ip.is_link_local:
            found.add(raw)
    return found


def test_no_tracked_file_names_a_private_network_host():
    hits = {}
    for path in _tracked():
        if not _scanned(path):
            continue
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found = _private_addresses(text, path=path)
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
    # Regressions (LAN critic on B-local-config-repo): each was a way past the check.
    assert _private_addresses(f"The critic is at {lan}.") == {lan}, "a sentence's full stop"
    ula, link = "fd" + "00::1", "fe" + "80::2"
    assert _private_addresses(f"base_url = http://[{ula}]:8000/v1 or {link}") == {ula, link}
    assert _private_addresses(":" + ":1 is loopback, 12:30:45 is a time") == set()


def test_an_exemption_is_exactly_the_path_it_names():
    fixture = _ip(10, 0, 0, 5)
    assert _private_addresses(fixture, path="tests/test_container.py") == set()
    assert _private_addresses(fixture, path=".ddflow/config.toml") == {fixture}
    assert _scanned(".roborev.toml") is False
    assert _scanned(".roborev.toml.example") is True, "an exemption is a path, not a prefix"
    assert _scanned(".ddflow/events/x.jsonl") is False
