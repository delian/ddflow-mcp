"""`ddflow docs check` and the `ddflow_docs` tool: the documentation check as `[docs]` asks.

Each `[docs]` knob the command reads has a test that changes what the report says. The repo
fixtures are real git repositories; nothing is mocked but a local HTTP server for links.
"""

from __future__ import annotations

import functools
import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli
from helpers import rpc_call, tool_json

from ddflow import api
from ddflow.core import outcome as O
from ddflow.surfaces.mcp import Server

CLEAN = "# proj\n\nSee `real_name` and [the guide](GUIDE.md).\n"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def put(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    git(repo, "add", rel)


def configure(repo: Path, text: str) -> None:
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text(text, encoding="utf-8")


@pytest.fixture
def proj(repo: Path) -> Path:
    put(repo, "README.md", CLEAN)
    put(repo, "GUIDE.md", "# Guide\n")
    put(repo, "src/code.py", "real_name = 1\n")
    return repo


def check(repo: Path, **kw) -> O.Outcome:
    return api.docs(repo, verb="check", **kw)


def kinds(out: O.Outcome) -> list[tuple[str, str]]:
    return sorted((f["kind"], f["ref"]) for f in out.data["findings"])


def test_a_clean_tree_is_exit_0_with_what_was_checked_and_a_stable_digest(proj):
    one, two = check(proj), check(proj)

    assert one.exit == O.OK and one.data["findings"] == []
    assert "README.md" in one.data["checked"]["docs"]
    assert one.data["digest"] and one.data["digest"] == two.data["digest"]
    assert one.data["severity"] == "warn" and one.data["blocking"] is False


def test_a_finding_is_exit_1_and_changes_the_digest(proj):
    clean = check(proj)
    put(proj, "README.md", CLEAN + "Call `gone_function()`.\n")

    out = check(proj)

    assert out.exit == O.FAIL and kinds(out) == [("identifier", "gone_function")]
    assert out.data["digest"] != clean.data["digest"]


def test_unknown_verb_is_refused_not_run(proj):
    assert api.docs(proj, verb="frobnicate").exit == O.REFUSED


def test_severity_off_runs_nothing_and_says_so(proj):
    configure(proj, '[docs]\nseverity = "off"\n')

    out = check(proj)

    assert out.exit == O.NOTHING and "off" in out.reason and "findings" not in out.data


def test_severity_block_marks_a_report_with_findings_as_blocking(proj):
    put(proj, "README.md", CLEAN + "Call `gone_function()`.\n")
    configure(proj, '[docs]\nseverity = "block"\n')

    blocked = check(proj)
    assert blocked.exit == O.FAIL and blocked.data["blocking"] is True

    configure(proj, '[docs]\nseverity = "warn"\n')
    assert check(proj).data["blocking"] is False


def test_check_paths_is_the_allowlist_of_documents(proj):
    put(proj, "docs/old.md", "Call `ancient_function()`.\n")
    assert [f["ref"] for f in check(proj).data["findings"]] == ["ancient_function"]

    configure(proj, '[docs]\ncheck_paths = ["README.md"]\n')
    out = check(proj)

    assert out.exit == O.OK and out.data["checked"]["docs"] == ["README.md"]


def test_check_paths_matching_nothing_is_nothing_to_check_never_clean(proj):
    configure(proj, '[docs]\ncheck_paths = ["nowhere/*.md"]\n')

    out = check(proj)

    assert out.exit == O.NOTHING and "check_paths" in out.reason


def test_ignore_names_and_ignore_paths_silence_what_another_tool_owns(proj):
    put(proj, "README.md", CLEAN + "Call `other_tool_name`, see `tmp/out.log`.\n")
    loud = check(proj)
    assert [f["ref"] for f in loud.data["findings"]] == ["other_tool_name"]
    assert [n["ref"] for n in loud.data["notes"]] == ["tmp/out.log"]

    configure(proj, '[docs]\nignore_names = ["other_*"]\nignore_paths = ["tmp/*"]\n')
    quiet = check(proj)

    assert quiet.exit == O.OK and quiet.data["notes"] == []


def test_check_links_off_drops_the_link_findings_local_keeps_them(proj):
    put(proj, "README.md", CLEAN + "[x](missing.md) and [y](GUIDE.md#nope)\n")
    assert {k for k, _ in kinds(check(proj))} == {"link", "anchor"}

    configure(proj, '[docs]\ncheck_links = "off"\n')
    out = check(proj)

    assert out.exit == O.OK and "links" not in out.data["checked"]


@pytest.fixture
def web(tmp_path):
    """A local server: /ok answers 200, anything else 404."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "ok").write_text("hi")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    handler.log_message = lambda *a: None  # type: ignore[attr-defined]
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_network_links_are_followed_only_when_asked(proj, web):
    put(proj, "README.md", CLEAN + f"[up]({web}/ok) [gone]({web}/missing)\n")
    assert check(proj).exit == O.OK  # local: remote addresses are not looked at

    configure(proj, '[docs]\ncheck_links = "network"\n')
    out = check(proj)

    assert out.exit == O.FAIL
    assert [(f["kind"], f["ref"]) for f in out.data["findings"]] == [("link", f"{web}/missing")]
    assert out.data["checked"]["remote_links"] == 2


def test_an_unreachable_network_is_unavailable_never_a_clean_pass(proj, web):
    dead = web.rsplit(":", 1)[0] + ":1"  # nothing listens on port 1
    put(proj, "README.md", CLEAN + f"[x]({dead}/a)\n")
    configure(proj, '[docs]\ncheck_links = "network"\nlink_timeout_s = 2\n')

    out = check(proj)

    assert out.exit == O.NOTHING and "links" in out.data["unavailable"]
    assert "not a clean report" in out.reason


def test_lint_command_failure_is_a_finding_success_is_clean(proj):
    configure(proj, '[docs]\nlint_command = "echo trailing space; exit 3"\n')
    bad = check(proj)
    assert bad.exit == O.FAIL and kinds(bad) == [("lint", "echo trailing space; exit 3")]
    assert "trailing space" in bad.data["findings"][0]["detail"]

    configure(proj, '[docs]\nlint_command = "true"\n')
    good = check(proj)
    assert good.exit == O.OK and good.data["checked"]["lint"] == 1


def test_a_lint_that_cannot_run_is_unavailable_and_unset_is_only_said(proj):
    configure(proj, '[docs]\nlint_command = "no-such-linter-xyz --all"\n')
    out = check(proj)
    assert out.exit == O.NOTHING and "lint" in out.data["unavailable"]

    configure(proj, "")
    unset = check(proj)
    assert unset.exit == O.OK
    assert "lint" in unset.data["not_configured"] and unset.data["unavailable"] == {}


EXTRACT = (
    '[[docs.extract]]\nkind = "flags"\npattern = "add_argument\\\\(\\"(--[a-z-]+)\\""\n'
    'source_globs = ["src/cli.py"]\n'
)


def test_diff_keeps_only_the_surface_added_since_the_base(proj):
    put(proj, "src/cli.py", 'p.add_argument("--old")\n')
    git(proj, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "base")
    base = subprocess.run(
        ["git", "-C", str(proj), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    put(proj, "src/cli.py", 'p.add_argument("--old")\np.add_argument("--new")\n')
    configure(proj, EXTRACT)

    whole = check(proj)
    since = check(proj, diff=base)

    assert kinds(whole) == [("flag", "--new"), ("flag", "--old")]
    assert kinds(since) == [("flag", "--new")]
    assert since.data["diff"] == base and since.data["digest"] != whole.data["digest"]


def test_an_unknown_diff_base_could_not_run_it_is_not_an_empty_diff(proj):
    out = check(proj, diff="no-such-ref")

    assert out.exit == O.NOTHING and "could not run" in out.reason


def test_cli_and_mcp_are_one_declaration(proj):
    put(proj, "README.md", CLEAN + "Call `gone_function()`.\n")

    code, out, _ = run_cli(proj, "docs", "check", "--json")
    cli = json.loads(out)
    mcp = tool_json(rpc_call(Server(proj), "ddflow_docs", verb="check"))

    assert code == 1
    assert cli["digest"] == mcp["digest"] and cli["findings"] == mcp["findings"]
    text = run_cli(proj, "docs", "check")[1]
    assert "gone_function" in text and "1 finding(s)" in text


def test_cli_exit_codes_follow_the_outcome(proj):
    assert run_cli(proj, "docs", "check")[0] == 0
    configure(proj, '[docs]\nseverity = "off"\n')
    assert run_cli(proj, "docs", "check")[0] == 2
