"""An empty `search` / `session list` names the owner filter by its flag (bug Bf83faf3610).

The CLI echoed `{'agent': 'bob'}` in its human text while the flag is `--owner` and `--json`
(and the MCP read) say `owner`. Both CLI viewers now render the api's `view_read` answer, so
the text and the body cannot name a filter two ways.
"""

from __future__ import annotations

import json

from conftest import run_cli


def test_an_empty_search_names_the_owner_filter_by_its_flag(repo):
    run_cli(repo, "init")
    code, out, _err = run_cli(repo, "search", "zzzqq", "--owner", "bob")
    assert code == 2, out
    assert "{'owner': 'bob'}" in out and "'agent'" not in out, out


def test_an_empty_session_list_names_the_owner_filter_by_its_flag(repo):
    run_cli(repo, "init")
    code, out, _err = run_cli(repo, "session", "list", "--owner", "bob")
    assert code == 2, out
    assert "{'owner': 'bob'}" in out and "'agent'" not in out, out


def test_json_and_text_agree_on_the_filter_name(repo):
    run_cli(repo, "init")
    _code, out, _err = run_cli(repo, "search", "zzzqq", "--owner", "bob", "--json")
    assert json.loads(out)["filters"] == {"owner": "bob"}
