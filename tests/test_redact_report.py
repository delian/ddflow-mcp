"""Report redaction (D-upstream-reporting (3)): nothing private leaves in a report.

The corpus is the project's own event log, read at test time (.ddflow/events is exempt
from the repo-is-generic scan; nothing from it is committed). The committed fixture is
built from string pieces, as tests/test_repo_is_generic.py does.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from pathlib import Path

import pytest

from ddflow.services import redact_report as rr

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / ".ddflow" / "events"


def _ip(*parts: int) -> str:
    return ".".join(map(str, parts))


def _events() -> list[dict]:
    out = []
    for f in sorted(EVENTS.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _bugs() -> list[dict]:
    return [e for e in _events() if e.get("kind") == "bug.found"]


def _corpus_hostname() -> str:
    """The machine name the agent ids embed: the commonest first segment."""
    counts: dict[str, int] = {}
    for e in _events():
        head = str(e.get("agent", "")).partition("-")[0]
        if head:
            counts[head] = counts.get(head, 0) + 1
    return max(counts, key=counts.__getitem__)


def _corpus_projects(events: list[dict]) -> set[str]:
    """Project names as they appear in paths under a home directory in the log."""
    names = set()
    for e in events:
        for m in re.finditer(
            r"/home/[\w.-]+/(?:src|git|code|work|projects)/([\w.-]+)", json.dumps(e)
        ):
            names.add(m.group(1))
    return {n for n in names if len(n) >= 4 and n not in rr.PUBLIC_NAMES}


def _leaks(text: str, *, host: str, projects: set[str]) -> list[str]:
    left = []
    for raw in rr.private_addresses(text):
        left.append(f"address {raw}")
    if re.search(r"/(?:home|Users)/[\w.-]+", text):
        left.append("home path")
    if host and re.search(rf"(?<![A-Za-z0-9]){re.escape(host)}(?![A-Za-z0-9])", text, re.I):
        left.append("hostname")
    if re.search(r"\b[\w-]+\.(?:lan|internal|home\.arpa)\b", text, re.I):
        left.append(".lan host")
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text):
        left.append("email")
    for p in projects:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(p)}(?![A-Za-z0-9])", text, re.I):
            left.append(f"project {p}")
    return left


def test_every_bug_summary_and_rendering_is_clean():
    bugs = _bugs()
    assert len(bugs) > 50, "the event log should carry bug.found events"
    host = _corpus_hostname()
    projects = _corpus_projects(_events())
    assert projects, "the log should name some other project"
    dirty = 0
    for e in bugs:
        summary = str(e["data"].get("summary", ""))
        rendered = (
            f"# {e['subject']}\nagent: {e.get('agent', '')}\n"
            f"cwd: /home/someone/src/{sorted(projects)[0]}/x\n{summary}\n"
            f"reviewer http://{_ip(10, 220, 1, 8)}:8000 by a@b.example"
        )
        for text in (summary, rendered):
            out = rr.redact_report(text, hostname=host, names=projects)
            left = _leaks(out.text, host=host, projects=projects)
            assert not left, f"{e['subject']}: {left}: {out.text[:300]}"
            again = rr.redact_report(out.text, hostname=host, names=projects)
            assert again.text == out.text and again.total == 0, "not idempotent"
            dirty += out.total > 0
    assert dirty, "the corpus is known to contain private text; something must be removed"


def test_negative_control_versions_survive():
    text = f"ddflow v{_ip(0, 11, 0, 1)} and 0.1.7, build {_ip(1, 2, 3, 4)}, at 12:30:45"
    out = rr.redact_report(text, hostname="", names=())
    assert out.text == text and out.total == 0


def test_secrets_still_redact():
    tok = "ghp_" + "a1B2c3D4e5F6g7H8i9J0"
    out = rr.redact_report(f"api_key = hunter2hunter2 and {tok} and Bearer abcdefghijkl12345")
    assert "hunter2" not in out.text and tok not in out.text and "abcdefghijkl" not in out.text
    assert out.counts["secret"] == 3
    assert "[REDACTED:secret]" in out.text


def test_synthetic_fixture():
    lan, home, corp = _ip(10, 220, 1, 8), _ip(192, 168, 0, 4), _ip(172, 16, 9, 9)
    link, loop = _ip(169, 254, 3, 4), _ip(127, 0, 0, 1)
    ula, fe80, v6loop = "fd" + "00::1", "fe" + "80::2", ":" + ":1"
    text = "\n".join(
        [
            f"critic at http://{lan}:8000/v1, {home}, {corp}, {link}, {loop}",
            f"v6: [{ula}]:80 {fe80} {v6loop}",
            "hosts: nas." + "lan, printer." + "local, db." + "internal, router.home." + "arpa",
            "paths: /home/" + "alice/src/secretproj/a.py /Users/" + "bob/Library/x",
            "mail: alice" + "@corp.example.com",
            "project secretproj and Quux-Tool",
            "machine fixturebox ok",
            "settings.local.json stays",
        ]
    )
    out = rr.redact_report(text, hostname="fixturebox", names=["secretproj", "quux-tool"])
    assert not _leaks(out.text, host="fixturebox", projects={"secretproj", "quux-tool"})
    assert "settings.local.json" in out.text
    for kind in ("ipv4", "ipv6", "host", "path", "email", "name", "hostname"):
        assert out.counts.get(kind, 0) >= 1, (kind, out.counts)
    assert out.counts["ipv4"] == 5 and out.counts["ipv6"] == 3 and out.counts["host"] == 4
    assert rr.redact_report(out.text, hostname="fixturebox", names=["secretproj"]).total == 0


def test_actual_home_repo_root_and_machine_hostname(tmp_path, monkeypatch):
    home = tmp_path / "weird-home-dir"
    repo = home / "work" / "mysecretrepo"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(socket, "gethostname", lambda: "Zeta9.example.test")
    text = f"see {repo}/ddflow/x.py and {home}/notes/y.txt on zeta9 / Zeta9.example.test"
    out = rr.redact_report(text, repo_root=repo)
    assert str(home) not in out.text and "weird-home" not in out.text
    assert "mysecretrepo" not in out.text and "zeta9" not in out.text.lower()
    assert "/ddflow/x.py" in out.text, "the repo-relative tail is kept"


def test_the_tool_s_own_name_is_not_a_project_name():
    out = rr.redact_report("ddflow bug in ddflow-mcp", hostname="", names=["ddflow"])
    assert out.text == "ddflow bug in ddflow-mcp"


@pytest.mark.parametrize(
    "odd", [None, b"\xff\xfe bytes", 42, "", "\x00\ud800 lone", "[REDACTED:", "x" * 100_000]
)
def test_never_raises_on_odd_input(odd):
    out = rr.redact_report(odd, hostname="", names=())
    assert isinstance(out.text, str)


def test_markers_are_not_reredacted_even_if_a_name_matches_them():
    first = rr.redact_report("secretproj at /home/" + "u/x", hostname="", names=["secretproj"])
    second = rr.redact_report(first.text, hostname="", names=["redacted", "path", "name"])
    assert second.text == first.text


def test_private_addresses_helper_matches_the_repo_guard():
    from tests.test_repo_is_generic import _private_addresses

    for t in (
        f"x {_ip(10, 1, 2, 3)}.",
        f"{_ip(8, 8, 8, 8)} {_ip(172, 32, 0, 1)}",
        "fd" + "00::1 fe" + "80::2",
    ):
        assert rr.private_addresses(t) == _private_addresses(t)
    assert ipaddress  # keep import used


def test_configured_secret_patterns_are_applied():
    from ddflow.config import Config

    cfg = Config()
    cfg.session.redact_extra = [r"zzkey-[a-z]{6}"]
    out = rr.redact_report("a zzkey-abcdef b", cfg=cfg)
    assert "zzkey-abcdef" not in out.text and out.counts["secret"] == 1


def test_a_long_unbroken_token_is_not_quadratic():
    import time

    t0 = time.monotonic()
    rr.redact_report("x" * 200_000, hostname="", names=())
    assert time.monotonic() - t0 < 5
