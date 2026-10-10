"""B-upgrade.8-release-check: the periodic check for a newer ddflow release.

Decision D-self-upgrade (1) and (2). No test here touches the public network: the unit tests
inject the transport and the clock, the CLI and MCP tests ask a local http server that plays
the package index.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.events import is_older, version_key
from ddflow.infra import release_index as RI
from ddflow.services import upgrade_check as UC
from ddflow.surfaces.mcp import Server

NOW = 1_000_000.0
RUNNING = "0.1.9"


def doc(*versions: str, yanked: tuple[str, ...] = ()) -> bytes:
    releases = {v: [{"filename": f"x-{v}.whl", "yanked": v in yanked}] for v in versions}
    return json.dumps({"info": {"version": versions[-1]}, "releases": releases}).encode()


class Index:
    """A fake release index: counts requests, answers a fixed document or fails."""

    def __init__(self, body: bytes | Exception) -> None:
        self.body = body
        self.urls: list[str] = []

    def __call__(self, url: str, timeout: float) -> bytes:
        self.urls.append(url)
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


@pytest.fixture(autouse=True)
def _check_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(UC.ENV_OFF, raising=False)
    monkeypatch.setattr(UC, "_dev_tree", lambda: False)  # the suite runs from a checkout


@pytest.fixture
def project(repo: Path) -> Path:
    (repo / ".ddflow").mkdir()
    return repo


def cfg_with(**knobs) -> Config:
    c = Config()
    for k, v in knobs.items():
        setattr(c.upgrade, k, v)
    return c


# --- the index client -----------------------------------------------------------------


def test_version_order_is_numeric_and_prereleases_rank_below_their_release() -> None:
    assert is_older("0.1.9", "0.1.10")
    assert not is_older("0.1.10", "0.1.9")
    assert is_older("0.2.0rc1", "0.2.0")
    assert RI.is_prerelease("0.2.0rc1") and RI.is_prerelease("0.2.0.dev3")
    assert not RI.is_prerelease("0.2.0")


def test_newest_picks_the_highest_final_release_numerically() -> None:
    fake = Index(doc("0.1.2", "0.1.10", "0.1.9", "0.2.0rc1"))
    assert RI.newest("ddflow-mcp", fetch=fake) == "0.1.10"
    assert fake.urls == ["https://pypi.org/pypi/ddflow-mcp/json"]


def test_prereleases_only_when_asked() -> None:
    fake = Index(doc("0.1.9", "0.2.0rc1"))
    assert RI.newest("d", fetch=fake) == "0.1.9"
    assert RI.newest("d", fetch=fake, prereleases=True) == "0.2.0rc1"


def test_yanked_and_fileless_releases_are_skipped() -> None:
    body = json.loads(doc("0.1.9", "0.1.10", yanked=("0.1.10",)))
    body["releases"]["0.1.11"] = []
    assert RI.newest("d", fetch=Index(json.dumps(body).encode())) == "0.1.9"


def test_a_private_mirror_and_the_scheme_check() -> None:
    fake = Index(doc("0.1.9"))
    RI.newest("d", index_url="https://mirror.example/simple/", fetch=fake)
    assert fake.urls == ["https://mirror.example/simple/d/json"]
    with pytest.raises(RI.ReleaseIndexError):
        RI.newest("d", index_url="file:///etc", fetch=fake)


@pytest.mark.parametrize(
    "body", [b"not json", b"[]", b"{}", ConnectionError("down"), TimeoutError()]
)
def test_every_failure_is_one_exception(body) -> None:
    with pytest.raises(RI.ReleaseIndexError):
        RI.newest("d", fetch=Index(body))


# --- the check ------------------------------------------------------------------------


def test_a_newer_release_is_proposed_once_and_the_second_run_makes_no_request(project) -> None:
    fake = Index(doc("0.1.9", "0.1.10"))
    cfg = Config()
    first = UC.proposal(project, cfg, consume=True, fetch=fake, now=NOW, running=RUNNING)
    assert "0.1.10" in first and RUNNING in first and "\n" not in first
    again = UC.proposal(project, cfg, consume=True, fetch=fake, now=NOW + 60, running=RUNNING)
    assert again == "" and len(fake.urls) == 1, "said once; asked once within the interval"
    # status / doctor read the cache and say it whenever a newer release is known
    assert UC.known_line(project, cfg, running=RUNNING) == first
    assert UC.proposal(project, cfg, fetch=fake, now=NOW + 120, running=RUNNING) == first
    assert len(fake.urls) == 1


def test_the_cache_lives_under_ddflow_local_and_is_ignored_by_git(project) -> None:
    UC.check(project, Config(), fetch=Index(doc("0.1.10")), now=NOW, running=RUNNING)
    cache = project / ".ddflow" / "local" / "release-check.json"
    assert json.loads(cache.read_text())["newest"] == "0.1.10"
    ignored = (project / ".ddflow" / "local" / ".gitignore").read_text()
    assert "*" in ignored


def test_the_interval_is_honoured_and_a_new_release_after_it_is_proposed_again(project) -> None:
    fake = Index(doc("0.1.10"))
    cfg = cfg_with(check_interval_h=2.0)
    UC.proposal(project, cfg, consume=True, fetch=fake, now=NOW, running=RUNNING)
    UC.proposal(project, cfg, consume=True, fetch=fake, now=NOW + 3600, running=RUNNING)
    assert len(fake.urls) == 1
    fake.body = doc("0.1.10", "0.1.11")
    line = UC.proposal(project, cfg, consume=True, fetch=fake, now=NOW + 7300, running=RUNNING)
    assert len(fake.urls) == 2 and "0.1.11" in line, "a newer version is a new proposal"


@pytest.mark.parametrize("failure", [ConnectionError("offline"), TimeoutError(), b"<html>"])
def test_offline_or_timeout_is_silent_and_not_retried_within_the_interval(project, failure) -> None:
    fake = Index(failure)
    assert UC.proposal(project, Config(), consume=True, fetch=fake, now=NOW, running=RUNNING) == ""
    out = UC.check(project, Config(), fetch=fake, now=NOW + 3700, running=RUNNING)
    assert out["status"] == "cached" and len(fake.urls) == 1, "at most one request per interval"
    UC.check(project, Config(), fetch=fake, now=NOW + 24 * 3600 + 1, running=RUNNING)
    assert len(fake.urls) == 2
    UC.check(project, Config(), force=True, fetch=fake, now=NOW + 24 * 3600 + 2, running=RUNNING)
    assert len(fake.urls) == 3, "an explicit check asks at once"


def test_off_and_the_environment_make_no_request_at_all(project, monkeypatch) -> None:
    fake = Index(doc("0.1.10"))
    off = cfg_with(release_check="off")
    assert UC.proposal(project, off, consume=True, fetch=fake, now=NOW, running=RUNNING) == ""
    assert UC.check(project, off, force=True, fetch=fake, now=NOW)["status"] == "off"
    monkeypatch.setenv(UC.ENV_OFF, "1")
    assert UC.proposal(project, Config(), fetch=fake, now=NOW, running=RUNNING) == ""
    assert UC.check(project, Config(), force=True, fetch=fake, now=NOW)["status"] == "off"
    assert fake.urls == [] and not (project / ".ddflow" / "local").exists()
    monkeypatch.setenv(UC.ENV_OFF, "0")
    assert UC.enabled(Config())


def test_a_cached_answer_is_not_spoken_once_the_check_is_turned_off(project) -> None:
    UC.check(project, Config(), fetch=Index(doc("0.1.10")), now=NOW, running=RUNNING)
    assert UC.known_line(project, cfg_with(release_check="off"), running=RUNNING) == ""


def test_the_newest_release_proposes_nothing(project) -> None:
    fake = Index(doc("0.1.8", "0.1.9"))
    assert UC.proposal(project, Config(), fetch=fake, now=NOW, running=RUNNING) == ""


def test_a_directory_ddflow_never_adopted_is_not_adopted_by_the_check(repo) -> None:
    fake = Index(doc("0.1.10"))
    assert UC.proposal(repo, Config(), consume=True, fetch=fake, now=NOW, running=RUNNING) == ""
    assert fake.urls == [] and not (repo / ".ddflow").exists()


def test_a_read_only_store_costs_nothing_but_a_repeat(project) -> None:
    local = project / ".ddflow" / "local"
    local.mkdir()
    local.chmod(0o500)
    try:
        out = UC.check(project, Config(), fetch=Index(doc("0.1.10")), now=NOW, running=RUNNING)
    finally:
        local.chmod(0o700)
    assert out["newer"] is True


# --- the surfaces ---------------------------------------------------------------------


@pytest.fixture
def index_server():
    """A local http server that plays the package index; `.versions` is what it serves."""
    state = {"versions": ("0.1.9", "99.0.0"), "hits": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["hits"] += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(doc(*state["versions"]))

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{server.server_port}/pypi"
    yield state
    server.shutdown()


def test_upgrade_check_on_the_cli_and_over_mcp_agree(project, index_server) -> None:
    run_cli(project, "config", "upgrade.index_url", index_server["url"])
    code, out, err = run_cli(project, "--json", "upgrade", "--check")
    assert code == 1, err  # a newer release exists
    cli = json.loads(out)
    assert cli["newest"] == "99.0.0" and cli["newer"] is True and cli["status"] == "checked"
    assert "newest release 99.0.0" in cli["text"]
    assert "project stamp" in cli["text"]
    reply = Server(project).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_upgrade", "arguments": {"check": True}},
        }
    )
    body = json.loads(reply["result"]["content"][0]["text"])
    assert body["newest"] == "99.0.0" and body["newer"] is True
    assert reply["result"].get("isError") is not True or "newer" in body


def test_upgrade_check_stands_alone(project) -> None:
    code, _out, err = run_cli(project, "upgrade", "--check", "--apply")
    assert code == 3 and "stands alone" in err


def test_upgrade_check_when_off_makes_no_request_and_says_so(project, index_server) -> None:
    run_cli(project, "config", "upgrade.index_url", index_server["url"])
    run_cli(project, "config", "upgrade.release_check", "off")
    code, out, _err = run_cli(project, "upgrade", "--check")
    assert code == 2 and "OFF" in out and index_server["hits"] == 0


def test_upgrade_check_offline_is_exit_2_not_a_traceback(project) -> None:
    run_cli(project, "config", "upgrade.index_url", "http://127.0.0.1:9/pypi")
    code, out, err = run_cli(project, "upgrade", "--check")
    assert code == 2 and "Traceback" not in err and "could not be reached" in out


def test_brief_proposes_once_and_status_and_doctor_keep_saying_it(project, index_server) -> None:
    from ddflow import api as A

    run_cli(project, "config", "upgrade.index_url", index_server["url"])
    first = A.brief(project).data["text"]
    assert "99.0.0 is available" in first and first.count("99.0.0 is available") == 1
    assert "99.0.0 is available" not in A.brief(project).data["text"]
    assert index_server["hits"] == 1, "the second brief is inside the interval"
    assert "99.0.0 is available" in A.status(project).data["release"]
    assert any("99.0.0 is available" in n for n in A.doctor(project).data["notes"])
    assert index_server["hits"] == 1


def test_the_mcp_handshake_proposes_from_the_cache_without_asking(project, index_server) -> None:
    run_cli(project, "config", "upgrade.index_url", index_server["url"])
    run_cli(project, "upgrade", "--check")
    hits = index_server["hits"]
    reply = Server(project).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert "99.0.0 is available" in reply["result"]["instructions"]
    assert index_server["hits"] == hits


def test_the_knobs_are_declared() -> None:
    from ddflow.config import KNOB_CHOICES, KNOB_OUTWARD, KNOB_STRICTEST

    assert KNOB_CHOICES["upgrade.release_check"] == ("on", "off")
    assert KNOB_STRICTEST["upgrade.release_check"][0] == "off"
    assert KNOB_OUTWARD["upgrade.release_check"] == frozenset(), "a read-only GET, no write"
    c = Config()
    assert (c.upgrade.release_check, c.upgrade.check_interval_h) == ("on", 24.0)
    assert (c.upgrade.index_url, c.upgrade.prereleases) == ("", False)
    assert version_key("0.1.10") > version_key("0.1.9")


def test_a_source_checkout_is_not_proposed_an_index_release(project, monkeypatch) -> None:
    monkeypatch.setattr(UC, "_dev_tree", lambda: True)
    fake = Index(doc("0.1.10"))
    assert UC.proposal(project, Config(), consume=True, fetch=fake, now=NOW, running=RUNNING) == ""
    assert fake.urls == []
    assert (
        UC.check(project, Config(), force=True, fetch=fake, now=NOW, running=RUNNING)["newer"]
        is True
    )
