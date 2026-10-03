"""Export surfaces, service and api layer (B-export-surfaces): the [export] config, the
selection, print / diff / check / write, and the MCP tool's write rules.

The project is built at test time through the CLI (a real adopted repository, a phase, a task
and a bug), so nothing here quotes another project's log.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api
from ddflow.config import EXPORT_DEFAULT_TARGETS, Config
from ddflow.core.schedule import shared_globs
from ddflow.services.export import ExportError
from ddflow.services.export import registry as R
from ddflow.services.export import write as W


@pytest.fixture
def proj(repo: Path) -> Path:
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "phase", "add", "P1", "--title", "Billing", "--globs", "src/**")[0] == 0
    assert run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--title", "Tax rules")[0] == 0
    bug = ("bug", "found", "--summary", "tax rounds wrong", "--item", "P1.T1", "--no-task")
    assert run_cli(repo, *bug)[0] == 0  # --no-task: `open_tasks` below counts the hand-filed ones
    return repo


def _select(repo: Path, toml: str) -> None:
    p = repo / ".ddflow" / "config.toml"
    p.write_text(p.read_text() + "\n" + toml)


def _results(out) -> list[dict]:
    return out.data["results"]


# -- [export] config -------------------------------------------------------------------


def test_export_config_defaults():
    e = Config().export
    assert (e.documents, e.redact, e.max_bytes, e.refresh, e.tables) == ([], True, 60000, "off", {})


def test_export_tables_load_and_unknown_keys_are_skipped_not_fatal(proj):
    _select(
        proj,
        '[export]\ndocuments = ["roadmap"]\nrefresh = "docs_gate"\nfuture_knob = 1\n'
        '[export.roadmap]\npath = "docs/R.md"\nmode = "region"\nbrand_new_key = true\n',
    )
    cfg = Config.load(proj)
    assert cfg.export.documents == ["roadmap"] and cfg.export.refresh == "docs_gate"
    assert cfg.export.tables["roadmap"] == {"path": "docs/R.md", "mode": "region"}
    assert "export.future_knob" in cfg.unknown_knobs
    assert "export.roadmap.brand_new_key" in cfg.unknown_knobs


def test_a_written_export_config_is_validated():
    with pytest.raises(ValueError, match="unknown key"):
        Config.check({"export": {"roadmap": {"nope": 1}}})
    with pytest.raises(ValueError, match="mode"):
        Config.check({"export": {"roadmap": {"mode": "sideways"}}})
    with pytest.raises(ValueError, match="refresh"):
        Config.check({"export": {"refresh": "sometimes"}})
    Config.check({"export": {"refresh": "merge", "roadmap": {"filters": {"limit": 5}}}})


def test_default_targets_agree_with_the_kinds():
    for name in R.names():
        assert EXPORT_DEFAULT_TARGETS[name] == R.get(name).default_target, name


def test_selected_targets_are_shared_paths_for_leases():
    cfg = Config()
    base = shared_globs(cfg)
    cfg.export.documents = ["roadmap", "bugs"]
    cfg.export.tables = {"bugs": {"path": "docs/BUGS.md", "mode": "append"}}
    assert shared_globs(cfg) == [*base, "ROADMAP.md", "docs/BUGS.md"]
    assert ("bugs", "docs/BUGS.md", "append") in cfg.export.targets()


# -- list ------------------------------------------------------------------------------


def test_list_default_selection_is_empty(proj):
    out = api.export_list_documents(proj)
    assert out.exit == 0 and out.data["selected"] == []
    assert {r["doc"] for r in out.data["documents"]} >= {"roadmap", "bugs", "status"}
    assert {r["state"] for r in out.data["documents"]} == {"not selected"}


def test_list_states_missing_fresh_stale_hand_edited(proj):
    _select(proj, '[export]\ndocuments = ["roadmap", "status"]\n')

    def states() -> dict[str, str]:
        return {r["doc"]: r["state"] for r in api.export_list_documents(proj).data["documents"]}

    assert states()["roadmap"] == "missing"
    assert api.export_documents(proj, all_docs=True, update=True).exit == 0
    assert states()["roadmap"] == states()["status"] == "fresh"
    assert run_cli(proj, "task", "add", "P1.T2", "--phase", "P1", "--title", "More")[0] == 0
    assert states()["roadmap"] == "stale"
    (proj / "STATUS.md").write_text((proj / "STATUS.md").read_text() + "\nhand note\n")
    assert states()["status"] == "hand-edited"
    (proj / "ROADMAP.md").write_text("# mine\n")
    assert states()["roadmap"] == "hand-edited"


# -- print -----------------------------------------------------------------------------


def test_print_any_kind_without_selection_and_writes_nothing(proj):
    out = api.export_documents(proj, "roadmap")
    r = _results(out)[0]
    assert out.exit == 0 and r["action"] == "print" and r["truncated"] is False
    assert r["text"].startswith("<!-- ddflow:generated doc=roadmap")
    assert "Billing" in r["text"] and not (proj / "ROADMAP.md").exists()


def test_print_is_capped_with_an_explicit_truncation_field(proj):
    for i in range(30):
        run_cli(proj, "task", "add", f"P1.X{i}", "--phase", "P1", "--title", f"Task number {i}")
    full = _results(api.export_documents(proj, "roadmap", max_bytes=0))[0]
    cut = _results(api.export_documents(proj, "roadmap", max_bytes=900))[0]
    assert full["truncated"] is False
    assert cut["truncated"] is True and cut["truncated_more"] > 0
    assert cut["bytes"] <= 900 and "[truncated:" in cut["text"]


def test_unknown_kind_is_refused_with_the_list_of_kinds(proj):
    out = api.export_documents(proj, "nosuchdoc")
    assert out.exit == 3 and "roadmap" in out.reason and "bugs" in out.reason


def test_a_filter_the_kind_does_not_take_is_refused(proj):
    out = api.export_documents(proj, "status", status="open")
    assert out.exit == 3 and "status" in out.reason


def test_item_filters_bugs_as_phase(proj):
    ok = api.export_documents(proj, "bugs", item="P1.T1")
    assert ok.exit == 0, ok.reason
    bad = api.export_documents(proj, "bugs", item="no-such-item")
    assert bad.exit == 3


def test_version_is_the_tag_filter(proj):
    from ddflow.api.export import _filters

    none = {"since": "", "phase": "", "status": "", "limit": 0, "session": ""}
    assert _filters(version="1.2.3", tag="", **none).tag == "1.2.3"
    assert _filters(tag="1.2.3", version="1.2.3", **none).tag == "1.2.3"
    with pytest.raises(ExportError):
        _filters(tag="a", version="b", **none)
    # a kind that does not take `tag` refuses --version instead of ignoring it
    assert api.export_documents(proj, "roadmap", version="0.1.0").exit == 3
    # the changelog kind: an unknown version is refused (exit 3), the Unreleased section prints
    assert api.export_documents(proj, "changelog", version="9.9.9").exit == 3
    assert api.export_documents(proj, "changelog", version="unreleased").exit == 0


def test_changelog_appends_through_the_kinds_producer(proj):
    _select(proj, '[export.changelog]\nmode = "append"\npath = "CHANGES.md"\n')
    out = api.export_documents(proj, "changelog", update=True)
    assert out.exit == 0 and not (proj / "CHANGES.md").exists()  # nothing finished: no entries
    code, _o, err = run_cli(proj, "bug", "found", "--id", "B9", "--summary", "rounding error")
    assert code == 0, err
    (proj / "tests").mkdir()
    (proj / "tests" / "t.py").write_text("def t():\n    pass\n")
    code, _o, err = run_cli(
        proj,
        "bug",
        "fixed",
        "B9",
        "--regression-test",
        "tests/t.py::t",
        "--changelog",
        "Fixed: tax rounding",
    )
    assert code == 0, err
    out = api.export_documents(proj, "changelog", update=True)
    assert out.exit == 0, out.reason
    assert "ddflow:generated doc=changelog" in (proj / "CHANGES.md").read_text()
    # roadmap has no producer: append is refused for it
    _select(proj, '[export.roadmap]\nmode = "append"\n')
    assert api.export_documents(proj, "roadmap", update=True).exit == 3


def test_selecting_nothing_then_all_does_nothing_and_says_so(proj):
    out = api.export_documents(proj, all_docs=True, update=True)
    assert out.exit == 2 and "no documents are selected" in out.reason
    assert not (proj / "ROADMAP.md").exists()


def test_doc_and_all_are_alternatives(proj):
    assert api.export_documents(proj).exit == 3
    assert api.export_documents(proj, "roadmap", all_docs=True).exit == 3
    assert api.export_documents(proj, "roadmap", diff=True, check=True).exit == 3


# -- write: update / out / diff / check ------------------------------------------------


def test_all_acts_on_exactly_the_selected_documents(proj):
    _select(proj, '[export]\ndocuments = ["roadmap", "status"]\n')
    out = api.export_documents(proj, all_docs=True, update=True)
    assert out.exit == 0 and {r["doc"] for r in _results(out)} == {"roadmap", "status"}
    assert (proj / "ROADMAP.md").is_file() and (proj / "STATUS.md").is_file()
    assert not (proj / "BUGS.md").exists()
    # print still works for a document that is not selected
    assert api.export_documents(proj, "bugs").exit == 0
    chk = api.export_documents(proj, all_docs=True, check=True)
    assert chk.exit == 0 and {r["action"] for r in _results(chk)} == {"fresh"}


def test_check_exit_one_when_stale_and_diff_shows_the_change(proj):
    _select(proj, '[export]\ndocuments = ["roadmap"]\n')
    assert api.export_documents(proj, "roadmap", update=True).exit == 0
    run_cli(proj, "task", "add", "P1.T2", "--phase", "P1", "--title", "Added later")
    before = (proj / "ROADMAP.md").read_bytes()
    chk = api.export_documents(proj, "roadmap", check=True)
    assert chk.exit == 1 and _results(chk)[0]["action"] == "stale"
    d = api.export_documents(proj, "roadmap", diff=True)
    assert d.exit == 0 and "+" in _results(d)[0]["text"] and "Added later" in _results(d)[0]["text"]
    assert (proj / "ROADMAP.md").read_bytes() == before  # neither wrote


def test_diff_and_check_against_another_path(proj):
    """--out names the file a --diff or --check compares against (CLI and MCP)."""
    assert api.export_documents(proj, "status", out="docs/S.md").exit == 0
    chk = api.export_documents(proj, "status", check=True, out="docs/S.md")
    assert chk.exit == 0 and _results(chk)[0]["action"] == "fresh"
    via_tool = api.export_tool(proj, {"doc": "status", "check": True, "path": "docs/S.md"})
    assert via_tool.exit == 0 and _results(via_tool)[0]["path"] == "docs/S.md"
    via_tool = api.export_tool(proj, {"doc": "status", "diff": True, "path": "docs/none.md"})
    assert via_tool.exit == 0 and "+# Status" in _results(via_tool)[0]["text"]
    assert not (proj / "docs" / "none.md").exists()
    # still alternatives: two comparisons at once, or a comparison and a write
    assert api.export_documents(proj, "status", check=True, diff=True).exit == 3
    assert api.export_documents(proj, "status", check=True, update=True).exit == 3


def test_a_tool_argument_is_never_silently_dropped(proj):
    for args in ({"path": "x.md"}, {"write": True}, {"diff": True}, {"check": True}):
        assert api.export_tool(proj, args).exit == 3, args
    assert api.export_tool(proj, {"all": True, "write": True, "path": "x.md"}).exit == 3


def test_an_unreadable_target_is_listed_as_hand_edited_not_a_traceback(proj):
    _select(proj, '[export]\ndocuments = ["status"]\n')
    (proj / "STATUS.md").write_bytes(b"\xff\xfe\x00 not utf-8")
    rows = {r["doc"]: r for r in api.export_list_documents(proj).data["documents"]}
    assert rows["status"]["state"] == "hand-edited"
    assert api.export_documents(proj, all_docs=True, update=True).exit in (2, 3)


def test_export_tables_knob_is_validated_like_the_sub_tables():
    for bad in (
        {"R": {"mode": "nonsense"}},
        {"R": {"filters": {"limit": True}}},
        {"R": 1},
        {"R": "docs/x.md"},
        "not a table",
    ):
        with pytest.raises(ValueError):
            Config.check({"export": {"tables": bad}})
    Config.check({"export": {"tables": {"roadmap": {"path": "a.md", "filters": {"limit": 3}}}}})
    Config.check({"export": {"tables": {"roadmap": {"a_future_key": 1}}}})  # forward-compat
    lenient = Config()  # a file from a newer release: skipped with a warning, never fatal
    lenient._apply({"export": {"tables": {"R": {"mode": "from-the-future"}}}}, "file")
    assert lenient.export.tables == {} and any("export.tables" in k for k in lenient.unknown_knobs)
    with pytest.raises(ValueError):
        Config.check({"export": {"roadmap": {"filters": {"limit": True}}}})


def test_update_refuses_a_hand_edited_file_and_force_replaces_it(proj):
    (proj / "ROADMAP.md").write_text("# my own roadmap\n")
    out = api.export_documents(proj, "roadmap", update=True)
    assert out.exit == 3 and (proj / "ROADMAP.md").read_text() == "# my own roadmap\n"
    assert "--force" in out.reason
    forced = api.export_documents(proj, "roadmap", update=True, force=True)
    assert forced.exit == 0 and "ddflow:generated" in (proj / "ROADMAP.md").read_text()


def test_out_writes_a_repo_relative_path_and_refuses_unsafe_ones(proj):
    ok = api.export_documents(proj, "status", out="docs/out/STATUS.md")
    assert ok.exit == 0 and (proj / "docs" / "out" / "STATUS.md").is_file()
    for bad in ("../escape.md", "/tmp/escape.md", ".ddflow/x.md", ".git/x.md"):
        out = api.export_documents(proj, "status", out=bad)
        assert out.exit == 3, bad


def test_update_asks_on_a_terminal_and_declining_writes_nothing(proj):
    asked = []
    out = api.export_documents(
        proj, "roadmap", update=True, confirm=lambda rel, diff: asked.append((rel, diff)) or False
    )
    assert asked and asked[0][0] == "ROADMAP.md"
    assert out.exit == 2 and _results(out)[0]["action"] == "declined"
    assert not (proj / "ROADMAP.md").exists()
    ok = api.export_documents(proj, "roadmap", update=True, confirm=lambda rel, diff: True)
    assert ok.exit == 0 and (proj / "ROADMAP.md").exists()


def test_region_mode_keeps_the_hand_written_text(proj):
    (proj / "README.md").write_text("intro\n\n" + W.region_text("status", "old\n") + "\noutro\n")
    _select(proj, '[export.status]\npath = "README.md"\nmode = "region"\n')
    out = api.export_documents(proj, "status", update=True)
    assert out.exit == 0, out.reason
    text = (proj / "README.md").read_text()
    assert text.startswith("intro\n\n") and text.endswith("\noutro\n") and "# Status" in text


def test_a_kind_that_renders_whole_documents_cannot_be_appended(proj):
    _select(proj, '[export.roadmap]\nmode = "append"\n')
    out = api.export_documents(proj, "roadmap", update=True)
    assert out.exit == 3 and "cannot be appended" in out.reason
    assert not (proj / "ROADMAP.md").exists()


def test_config_template_override_and_filters_apply(proj):
    (proj / "t.md.j2").write_text("CUSTOM {{ open_tasks }}\n")
    _select(proj, '[export.roadmap]\ntemplate = "t.md.j2"\n')
    r = _results(api.export_documents(proj, "roadmap"))[0]
    assert "CUSTOM 1" in r["text"]
    _select(proj, '[export.bugs]\nfilters = { status = "fixed" }\n')
    assert api.export_documents(proj, "bugs").exit == 0


def test_template_flag_renders_once_and_writes_nothing(proj):
    (proj / "adhoc.j2").write_text("ADHOC {{ open_tasks }}\n")
    out = api.export_documents(proj, "roadmap", template="adhoc.j2", base=proj)
    assert "ADHOC 1" in _results(out)[0]["text"] and not (proj / "ROADMAP.md").exists()
    broken = proj / "broken.j2"
    broken.write_text("{{ nothing_here.x }}\n")
    assert api.export_documents(proj, "roadmap", template="broken.j2", base=proj).exit == 2
    assert api.export_documents(proj, "roadmap", template="missing.j2", base=proj).exit == 2


def test_a_log_that_cannot_be_read_is_could_not_run_not_an_empty_document(tmp_path):
    out = api.export_documents(tmp_path, "roadmap")
    assert out.exit == 2 and "event log" in out.reason


def test_listing_selected_documents_over_an_unreadable_log_is_exit_two_not_stale(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text('[export]\ndocuments = ["roadmap"]\n')
    out = api.export_list_documents(tmp_path)
    assert out.exit == 2 and "event log" in out.reason


def test_append_mode_refuses_a_template_instead_of_ignoring_it(proj):
    (proj / "t.j2").write_text("X\n")
    _select(proj, '[export.changelog]\nmode = "append"\npath = "CHANGES.md"\ntemplate = "t.j2"\n')
    out = api.export_documents(proj, "changelog", update=True)
    assert out.exit == 3 and "template" in out.reason and not (proj / "CHANGES.md").exists()


def test_append_mode_refuses_filters_and_a_last_event_the_log_lost(proj):
    _select(proj, '[export.changelog]\nmode = "append"\npath = "CHANGES.md"\n')
    out = api.export_documents(proj, "changelog", update=True, tag="unreleased")
    assert out.exit == 3 and "no template and no filters" in out.reason
    # a header whose `last` is not in this log: refuse rather than repeat entries
    from ddflow.services.export import frame

    (proj / "CHANGES.md").write_text(
        frame.frame("- Fixed: old\n", "changelog", "0", {"last": "E-gone"})
    )
    out = api.export_documents(proj, "changelog", update=True)
    assert out.exit == 3 and "not in the log" in out.reason
    assert "old" in (proj / "CHANGES.md").read_text()


def test_printing_an_append_mode_document_still_honours_its_filters(proj):
    _select(proj, '[export.changelog]\nmode = "append"\npath = "CHANGES.md"\n')
    out = api.export_documents(proj, "changelog", version="unreleased")
    assert out.exit == 0, out.reason  # a preview: print never appends
    assert api.export_documents(proj, "changelog", check=True, tag="unreleased").exit == 3


def test_template_never_writes(proj):
    (proj / "adhoc.j2").write_text("X {{ open_tasks }}\n")
    for kw in ({"update": True}, {"out": "docs/x.md"}):
        out = api.export_documents(proj, "roadmap", template="adhoc.j2", base=proj, **kw)
        assert out.exit == 3 and "never writes" in out.reason
    assert not (proj / "docs").exists() and not (proj / "ROADMAP.md").exists()
    ok = api.export_documents(proj, "roadmap", template="adhoc.j2", base=proj, diff=True)
    assert ok.exit == 0 and "+X" in _results(ok)[0]["text"]


# -- the MCP tool ----------------------------------------------------------------------


def test_tool_lists_without_a_doc(proj):
    out = api.export_tool(proj, {})
    assert out.exit == 0 and "documents" in out.data


def test_tool_never_writes_without_write_true(proj):
    out = api.export_tool(proj, {"doc": "roadmap"})
    assert out.exit == 0 and not (proj / "ROADMAP.md").exists()
    out = api.export_tool(proj, {"doc": "roadmap", "path": "ROADMAP.md"})
    assert out.exit == 3 and not (proj / "ROADMAP.md").exists()
    out = api.export_tool(proj, {"doc": "roadmap", "write": True})
    assert out.exit == 3 and "path" in out.reason and not (proj / "ROADMAP.md").exists()


def test_tool_writes_with_write_and_a_repo_relative_path(proj):
    out = api.export_tool(proj, {"doc": "roadmap", "write": True, "path": "docs/ROADMAP.md"})
    assert out.exit == 0 and (proj / "docs" / "ROADMAP.md").is_file()


def test_tool_refuses_a_path_outside_the_repo(proj):
    for bad in ("../escape.md", "/tmp/escape-export.md", ".ddflow/a.md"):
        out = api.export_tool(proj, {"doc": "roadmap", "write": True, "path": bad})
        assert out.exit == 3, bad
    assert not Path("/tmp/escape-export.md").exists()


def test_tool_has_no_force_or_template_and_is_bounded(proj):
    out = api.export_tool(proj, {"doc": "roadmap", "max_bytes": 0})
    assert out.exit == 0
    assert _results(out)[0]["bytes"] <= api.export.MCP_CEILING
    (proj / "ROADMAP.md").write_text("# mine\n")
    out = api.export_tool(
        proj, {"doc": "roadmap", "write": True, "path": "ROADMAP.md", "force": True}
    )
    assert out.exit == 3 and (proj / "ROADMAP.md").read_text() == "# mine\n"


def test_no_dependency_beyond_the_standard_library_and_jinja():
    root = Path(__file__).resolve().parents[1] / "ddflow"
    allowed = {"jinja2", "ddflow"}  # ddflow itself: `import ddflow` for the version
    files = [
        *sorted((root / "services" / "export").glob("*.py")),
        root / "api/export.py",
        root / "surfaces/commands/export.py",
    ]
    assert len(files) > 10
    for f in files:
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = a.name.split(".")[0]
                    assert top in sys.stdlib_module_names or top in allowed, (f, top)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                top = (node.module or "").split(".")[0]
                assert top in sys.stdlib_module_names or top in allowed, (f, top)
