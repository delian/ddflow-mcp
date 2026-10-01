"""Bug B-macro-clash-silent: one `[[macro]]` named like a shipped command hid EVERY macro.

`load_macros` raised on the clash, `macro_commands` swallowed the error and returned
`{}`, so every macro vanished from `prompts list` and from MCP `prompts/list`; `ddflow
prompts show <a valid macro>` said "unknown prompt" (`resolve_any` asks the swallowed
`all_commands` first, so the promised error never surfaced); and `doctor` exited 0 without
a word. The refusal written to stop silent shadowing was itself silent. Now the clashing
macro alone is refused, BY NAME, on `prompts list`/`show` and as a doctor PROBLEM, and the
valid ones load. Also: `prompts list` printed a `prompt_file` macro's path as "None".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import macros as M

CLASH = """
[[macro]]
name = "code-clean"
prompt = "my own cleanup"

[[macro]]
name = "bug-hunting"
prompt = "Hunt bugs the operator's way."
"""


def _with(repo: Path, block: str) -> None:
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + block)


def test_a_valid_macro_still_loads_beside_a_clashing_one(repo):
    _with(repo, CLASH)
    assert list(M.load_macros(repo)) == ["bug-hunting"]
    rc, out, err = run_cli(repo, "prompts", "show", "bug-hunting")
    assert rc == 0, (out, err)
    assert "Hunt bugs the operator's way." in out


def test_prompts_list_shows_the_valid_macro_and_names_the_refused_one(repo):
    _with(repo, CLASH)
    rc, out, err = run_cli(repo, "prompts", "list")
    assert rc == 0, err
    assert "bug-hunting" in out, out
    said = out + err
    assert "code-clean" in said and "shipped workflow command" in said, said


def test_prompts_show_of_the_clashing_name_says_the_macro_was_refused(repo):
    """The shipped command still answers to its name; the operator is told why their
    block is not what they are reading."""
    _with(repo, CLASH)
    rc, out, err = run_cli(repo, "prompts", "show", "code-clean")
    assert rc == 0, err
    assert "my own cleanup" not in out
    assert "code-clean" in err and "shipped workflow command" in err, err


def test_mcp_prompts_list_keeps_the_valid_macro(repo):
    from ddflow.surfaces.mcp import Server

    _with(repo, CLASH)
    listed = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})
    names = [p["name"] for p in listed["result"]["prompts"]]
    assert "bug-hunting" in names and "code-clean" in names, names


def test_doctor_reports_the_clash_as_a_problem(repo):
    _with(repo, CLASH)
    rc, out, _err = run_cli(repo, "--json", "doctor")
    problems = json.loads(out)["problems"]
    assert any("code-clean" in p and "shipped workflow command" in p for p in problems), problems
    assert rc != 0


def test_doctor_reports_a_macro_whose_prompt_file_is_missing(repo):
    _with(repo, '\n[[macro]]\nname = "ghost"\nprompt_file = "prompts/ghost.md"\n')
    _rc, out, _err = run_cli(repo, "--json", "doctor")
    problems = json.loads(out)["problems"]
    assert any("ghost" in p and "does not exist" in p for p in problems), problems


def test_prompts_list_shows_a_prompt_file_macros_file_not_none(repo):
    (repo / "prompts").mkdir()
    (repo / "prompts" / "hunt.md").write_text("hunt {{ x }}\n")
    _with(repo, '\n[[macro]]\nname = "hunting"\nprompt_file = "prompts/hunt.md"\n')
    rc, out, err = run_cli(repo, "prompts", "list")
    assert rc == 0, err
    line = next(ln for ln in out.splitlines() if ln.strip().startswith("hunting"))
    assert "None" not in line and "hunt.md" in line, line
    rc, out, _ = run_cli(repo, "--json", "prompts", "list")
    row = next(r for r in json.loads(out) if r["name"] == "hunting")
    assert row["path"] != "None" and row["path"].endswith("hunt.md"), row
