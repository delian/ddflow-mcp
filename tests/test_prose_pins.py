"""`ddflow pins` — which text of an instruction file a test pins (B22).

Compressing a rulebook without checking deleted nine pinned rules in one pass on the
source project: "every sentence picked read like rationale and was a rule". Its tool
matched calls to a `pin()` helper by name and dropped real pins five different ways, each
time reporting held text as free. The errors that matter here are all in that direction,
so most tests below build a suite shaped the way one of those drops was, and check that
the text it holds is NOT reported free.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_mcp import proj, rpc  # noqa: F401 -- `proj` is a fixture

from ddflow.api import pins
from ddflow.services import prosepin as PP

ROOT = Path(__file__).resolve().parents[1]
OK, FAIL, NOTHING = 0, 1, 2

RULEBOOK = """# Rules

Claim before you edit, always.
The duplicate is bookkeeping,
not evidence.

Some rationale nobody pinned sits here and may be compressed.
"""


def _project(repo: Path, suites: dict[str, str], doc: str = RULEBOOK) -> Path:
    (repo / "RULES.md").write_text(doc)
    (repo / "tests").mkdir(exist_ok=True)
    for name, src in suites.items():
        (repo / "tests" / name).write_text(src)
    return repo


def _free_text(out) -> str:
    return " ".join(f["text"] for f in out.data["free"])


def test_a_pin_is_found_across_a_line_break_and_reported_at_the_documents_line(repo):
    _project(
        repo, {"test_r.py": 'def test_x(doc):\n    assert "bookkeeping, not evidence" in doc\n'}
    )
    out = pins(repo, "RULES.md")
    assert out.exit == OK
    [pin] = out.data["pins"]
    assert pin["needle"] == "bookkeeping, not evidence"
    assert pin["lines"] == [4]
    assert pin["tests"] == ["tests/test_r.py"]
    assert "not evidence" not in _free_text(out)
    assert "Some rationale nobody pinned" in _free_text(out)


def test_a_needle_wrapped_by_the_suite_matches_a_document_that_is_not(repo):
    """`"No\\nreviewer sees another's verdict"` is how this repo's own suite pins a line."""
    _project(repo, {"test_r.py": "X = 'Claim before\\n    you edit'\n"})
    [pin] = pins(repo, "RULES.md").data["pins"]
    assert pin["needle"] == "Claim before you edit"
    assert pin["lines"] == [3]


def test_the_shapes_that_dropped_pins_by_callee_name_all_hold_their_text(repo):
    """Each suite is one shape the source project's callee matcher lost a pin to."""
    _project(
        repo,
        {
            # an import alias of the helper
            "test_alias.py": (
                "from tests._prose_pin import pin as _pin\n"
                "def test_a(doc):\n    _pin(doc, 'Claim before you edit')\n"
            ),
            # a module-level list fed to parametrize by name
            "test_list.py": (
                "import pytest\nNEEDLES: list[str] = ['bookkeeping, not evidence']\n"
                "@pytest.mark.parametrize('n', NEEDLES)\n"
                "def test_b(doc, n):\n    assert n in doc\n"
            ),
            # a module alias, and a multi-argument parametrize row
            "test_mod.py": (
                "import pytest\nimport tests._prose_pin as pp\n"
                "@pytest.mark.parametrize('a,b', [('x', 'may be compressed')])\n"
                "def test_c(doc, a, b):\n    pp.pin(doc, b)\n"
            ),
        },
    )
    out = pins(repo, "RULES.md")
    held = {p["needle"] for p in out.data["pins"]}
    assert held == {"Claim before you edit", "bookkeeping, not evidence", "may be compressed"}
    free = _free_text(out)
    for needle in held:
        assert needle not in free, needle


def test_a_docstring_quoting_a_rule_does_not_pin_it(repo):
    _project(
        repo,
        {
            "test_doc.py": (
                '"""Some rationale nobody pinned sits here."""\n'
                "def test_x():\n"
                '    """Claim before you edit, always."""\n'
            )
        },
    )
    assert pins(repo, "RULES.md").data["pins"] == []


def test_matching_ignores_case_because_suites_lowercase_before_pinning(repo):
    _project(
        repo,
        {"test_low.py": "def test_x(doc):\n    assert 'claim before you edit' in doc.lower()\n"},
    )
    [pin] = pins(repo, "RULES.md").data["pins"]
    assert pin["lines"] == [3]


def test_a_short_literal_is_not_a_pin_unless_the_floor_is_lowered(repo):
    _project(repo, {"test_s.py": "def test_x(doc):\n    assert 'Claim' in doc\n"})
    assert pins(repo, "RULES.md").data["pins"] == []
    assert [p["needle"] for p in pins(repo, "RULES.md", min_chars=5).data["pins"]] == ["Claim"]


def test_every_occurrence_of_a_needle_is_held(repo):
    doc = "Claim before you edit.\n\nfiller text here\n\nClaim before you edit.\n"
    _project(repo, {"test_r.py": "X = 'Claim before you edit'\n"}, doc=doc)
    out = pins(repo, "RULES.md")
    assert out.data["pins"][0]["lines"] == [1, 5]
    assert "Claim" not in _free_text(out)


def test_a_suite_that_does_not_parse_is_admitted_not_ignored(repo):
    _project(repo, {"test_ok.py": "X = 'Claim before you edit'\n", "test_bad.py": "def (:\n"})
    out = pins(repo, "RULES.md")
    assert out.exit == OK
    assert out.data["unparsed"] == ["tests/test_bad.py"]
    code, stdout, _ = run_cli(repo, "pins", "RULES.md")
    assert code == OK and "did not parse" in stdout and "tests/test_bad.py" in stdout


def test_suites_naming_the_document_are_listed_first(repo):
    _project(
        repo,
        {
            "test_a.py": "X = 'Claim before you edit'\n",
            "test_z.py": "DOC = 'RULES.md'\nY = 'bookkeeping, not evidence'\n",
        },
    )
    assert pins(repo, "RULES.md").data["tests"] == ["tests/test_z.py", "tests/test_a.py"]


def test_no_suite_to_read_is_exit_2_and_says_treat_it_all_as_pinned(repo):
    """Reporting the whole file free because nothing was read is the failure itself."""
    (repo / "RULES.md").write_text(RULEBOOK)
    out = pins(repo, "RULES.md")
    assert out.exit == NOTHING
    assert "treat all of it as pinned" in out.reason
    code, stdout, _ = run_cli(repo, "pins", "RULES.md")
    assert code == NOTHING and "treat all of it as pinned" in stdout


def test_a_missing_document_is_a_failure(repo):
    code, _, err = run_cli(repo, "pins", "NOPE.md")
    assert code == FAIL and "no such document" in err


def test_the_cli_names_the_suites_to_rerun_and_json_carries_the_same_body(repo):
    _project(repo, {"test_r.py": "X = 'Claim before you edit'\n"})
    code, stdout, _ = run_cli(repo, "pins", "RULES.md", "--tests", "tests")
    assert code == OK
    assert "pytest tests/test_r.py" in stdout
    assert "Read what you delete" in stdout
    code, stdout, _ = run_cli(repo, "--json", "pins", "RULES.md")
    body = json.loads(stdout)
    assert body == pins(repo, "RULES.md").body()


def test_the_mcp_tool_returns_the_report(proj):  # noqa: F811
    _project(proj, {"test_r.py": "X = 'Claim before you edit'\n"})
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ddflow_pins", "arguments": {"document": "RULES.md"}},
            }
        ],
    )
    result = r[0]["result"]
    assert not result.get("isError"), result
    body = json.loads(result["content"][0]["text"])
    assert [p["needle"] for p in body["pins"]] == ["Claim before you edit"]


def test_the_shipped_loop_command_reports_the_pins_its_own_suite_holds():
    """Dogfood: the reason this exists, on the instruction file ddflow ships."""
    out = pins(ROOT, "ddflow/templates/prompts/commands/implement.md")
    by_needle = {p["needle"]: p["tests"] for p in out.data["pins"]}
    for rule in (
        "exactly FOUR cases",
        "never a terminal stop",
        "disarm it before any deliberate stop",
        "Termination checklist",
    ):
        assert "tests/test_implement_command.py" in by_needle.get(rule, []), rule
    # Among the suites to re-run, and ahead of every suite that does not name the file.
    # Not "first": this file names it too, and ordering among those is by path (critic).
    suites = out.data["tests"]
    assert "tests/test_implement_command.py" in suites
    assert suites.index("tests/test_implement_command.py") < suites.index("tests/test_api_layer.py")


def test_flatten_maps_back_to_source_offsets():
    flat, where = PP.flatten("a \n\t b\nc")
    assert flat == "a b c"
    assert [where[i] for i in range(len(flat))] == [0, 1, 5, 6, 7]


def test_the_driver_tells_an_agent_to_check_pins_before_compressing():
    driver = (ROOT / "ddflow/templates/drivers/implement-phase.md").read_text()
    assert "Before compressing or rewording an instruction file" in driver
    assert "ddflow pins <file>" in driver


def test_an_unreadable_test_file_is_admitted_and_the_rest_still_reported(repo):
    """A dangling symlink crashed the whole report (rubber-duck, B22-symlink): one bad
    file hid every pin the readable ones hold."""
    _project(repo, {"test_ok.py": "X = 'Claim before you edit'\n"})
    (repo / "tests" / "test_gone.py").symlink_to(repo / "nowhere.py")
    out = pins(repo, "RULES.md")
    assert out.exit == OK
    assert out.data["unparsed"] == ["tests/test_gone.py"]
    assert [p["needle"] for p in out.data["pins"]] == ["Claim before you edit"]


def test_a_repo_reached_through_a_symlink_still_reports_repo_relative_paths(repo, tmp_path):
    """B-relpath-helper: test_files() resolves the suite directory and the repo argument
    was not, so a symlinked checkout reported every suite as an absolute path. The CLI
    hid it by resolving --repo; the API did not."""
    _project(repo, {"test_a.py": "X = 'Claim before you edit'\n"})
    link = tmp_path / "link-to-repo"
    link.symlink_to(repo, target_is_directory=True)
    out = pins(link, "RULES.md")
    assert out.exit == OK, out
    assert out.data["document"] == "RULES.md"
    assert out.data["tests"] == ["tests/test_a.py"]
    assert out.data["pins"][0]["tests"] == ["tests/test_a.py"]


def test_a_document_under_a_symlinked_directory_is_reported_by_its_listed_name(repo, tmp_path):
    """critic on B-relpath-helper: resolving the document's directories reported
    docs/RULES.md (docs -> outside the repo) as an absolute path."""
    _project(repo, {"test_a.py": "X = 'Claim before you edit'\n"})
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "RULES.md").write_text(RULEBOOK)
    (repo / "docs").symlink_to(shared, target_is_directory=True)
    out = pins(repo, "docs/RULES.md")
    assert out.exit == OK, out
    assert out.data["document"] == "docs/RULES.md"


def test_repo_relative_is_the_one_inside_the_repository_decision(repo, tmp_path):
    from ddflow.infra.worktree import repo_relative, store_path
    from ddflow.services.enforce import _rel

    link = tmp_path / "link"
    link.symlink_to(repo, target_is_directory=True)
    (repo / "sub").mkdir()
    (repo / "sub" / "alias.md").symlink_to(repo / "target.md")
    assert repo_relative(link, repo / "sub") == "sub"
    assert repo_relative(repo, link / "sub") == "sub"
    assert repo_relative(repo, tmp_path / "elsewhere") is None
    # the name as written is kept only when asked: a symlinked leaf, a path under a
    # symlinked directory (critic, B-relpath-helper), and one under the linked repo
    assert repo_relative(repo, repo / "sub" / "alias.md") == "target.md"
    assert repo_relative(repo, repo / "sub" / "alias.md", as_given=True) == "sub/alias.md"
    assert repo_relative(link, link / "sub" / "alias.md", as_given=True) == "sub/alias.md"
    assert repo_relative(link, repo / "sub" / "alias.md", as_given=True) == "sub/alias.md"
    (tmp_path / "shared").mkdir()
    (repo / "docs").symlink_to(tmp_path / "shared", target_is_directory=True)
    (repo / "inner").symlink_to(repo / "sub", target_is_directory=True)
    assert repo_relative(repo, repo / "docs" / "R.md", as_given=True) == "docs/R.md"
    assert repo_relative(repo, repo / "inner" / "x.py", as_given=True) == "inner/x.py"
    assert repo_relative(repo, repo / "docs" / "R.md") is None
    # `..` escapes the repository even when the text starts with it (critic #2)
    assert repo_relative(repo, repo / ".." / "secret.md", as_given=True) is None
    assert repo_relative(repo, repo / "sub" / ".." / "R.md", as_given=True) == "R.md"
    # store_path and enforce._rel agree with it inside, and keep their own fallbacks
    assert store_path(link, repo / "sub") == _rel(link, repo / "sub") == "sub"
    assert store_path(repo, tmp_path / "elsewhere") == "../elsewhere"
    assert _rel(repo, tmp_path / "elsewhere") == str(tmp_path / "elsewhere")


def test_an_explicit_floor_is_honoured_and_one_below_1_is_refused(proj):  # noqa: F811
    """`--min-needle 0` became the default 12 because 0 doubled as 'unset'
    (rubber-duck, B22-minzero): a lowered floor silently reported short pins free."""
    doc = "Go here.\n"
    _project(proj, {"test_r.py": "def test_x(doc):\n    assert 'Go' in doc\n"}, doc=doc)
    assert [p["needle"] for p in pins(proj, "RULES.md", min_chars=1).data["pins"]] == ["Go"]
    code, _, err = run_cli(proj, "pins", "RULES.md", "--min-needle", "0")
    assert code == FAIL and "--min-needle" in err
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "ddflow_pins",
                    "arguments": {"document": "RULES.md", "min_needle": 0},
                },
            }
        ],
    )
    assert r[0]["result"].get("isError"), r[0]["result"]


def test_a_free_stretch_after_a_pin_that_ends_its_line_starts_on_the_next_line(repo):
    """The run began on the collapsed newline and was reported a line early (B22-freeline)."""
    doc = "The pinned rule sentence\nfree text on line two\n"
    _project(repo, {"test_a.py": "X = 'The pinned rule sentence'\n"}, doc=doc)
    [free] = pins(repo, "RULES.md").data["free"]
    assert free["lines"] == [2, 2] and free["text"] == "free text on line two"


def test_a_suite_this_python_cannot_parse_still_holds_its_quoted_text(repo):
    """A file with newer syntax than ddflow's interpreter is still a live suite for the
    project's own. Its pins were reported FREE, behind a warning nobody acts on
    (critic, B22-unparsed). Quoted text is read from it by a lexer instead."""
    _project(
        repo,
        {
            "test_new.py": (
                "match (:\n"  # no interpreter parses this; stands in for newer syntax
                "def test_x(doc):\n    assert 'The duplicate is bookkeeping' in doc\n"
            )
        },
    )
    out = pins(repo, "RULES.md")
    assert out.data["unparsed"] == ["tests/test_new.py"]
    assert "The duplicate is bookkeeping" not in _free_text(out)
    assert [p["needle"] for p in out.data["pins"]] == ["The duplicate is bookkeeping"]


def test_where_even_the_tokenizer_gives_up_quoted_text_is_still_read():
    """An unterminated triple quote swallows the rest of the file for the tokenizer."""
    assert "held text here" in PP.quoted('"""oops\nx = \'held text here\'\n', 5)
