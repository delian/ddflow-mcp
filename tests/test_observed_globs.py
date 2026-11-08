"""Declared versus written files (LK-observed-globs)."""

from __future__ import annotations

from pathlib import Path

from ddflow.config import Config
from ddflow.core.records.items import Item
from ddflow.core.records.state import State
from ddflow.services import observed_globs as OG

REPO = Path("/nowhere")


def _state(*items: Item) -> State:
    st = State()
    for it in items:
        st.items[it.id] = it
    return st


def _task(id_: str, globs, tags=(), **kw) -> Item:
    return Item(id=id_, kind="task", title=id_, globs=list(globs), tags=list(tags), **kw)


def _reader(table: dict[str, list[str] | None]):
    return lambda repo, it: table[it.id]


def test_split_names_undeclared_and_untouched_ignoring_own_log():
    undeclared, untouched = OG.split(
        ["a/x.py", "tests/t.py", ".ddflow/events/e.json"], ["a/**", "docs/**"]
    )
    assert undeclared == ["tests/t.py"]
    assert untouched == ["docs/**"]


def test_split_drops_shared_globs():
    # B38c3ac0f60: a shared, regenerated pin (README.md) is never the task's to declare,
    # while a genuinely undeclared file beside it still is.
    undeclared, _ = OG.split(
        ["a/x.py", "README.md", "tests/t.py"], ["a/**"], shared=["README.md"]
    )
    assert undeclared == ["tests/t.py"]


def test_directory_globs_are_narrow():
    assert OG.directory_globs(["tests/a.py", "tests/b.py", "README.md"]) == ["README.md", "tests/*"]


def test_undeclared_report_names_exact_call_cli_and_mcp():
    st = _state(_task("T", ["a/**"]))
    text = OG.undeclared_report(
        st, Config(), "T", repo=REPO, read=_reader({"T": ["a/1", "tests/t.py"]})
    )
    assert "tests/t.py" in text
    assert "ddflow update T --globs 'a/**,tests/*'" in text
    assert "ddflow_task action=update id=T globs='a/**,tests/*'" in text


def test_undeclared_report_clean_off_and_unknown():
    st = _state(_task("T", ["a/**"]))
    cfg = Config()
    assert OG.undeclared_report(st, cfg, "T", repo=REPO, read=_reader({"T": ["a/1"]})) == ""
    assert OG.undeclared_report(st, cfg, "T", repo=REPO, read=_reader({"T": None})) == OG.unknown(
        "T"
    )
    cfg.locks.undeclared_write = "off"
    assert OG.undeclared_report(st, cfg, "T", repo=REPO, read=_reader({"T": ["z"]})) == ""


def test_undeclared_report_drops_shared_globs():
    # B38c3ac0f60: writing a shared, regenerated pin (README.md) must not be reported as
    # "written outside the declared globs" -- it is never leased, unlike a real undeclared
    # write next to it, which is still named.
    st = _state(_task("T", ["a/**"]))
    cfg = Config()
    cfg.lease.shared_globs = ["README.md"]
    only_shared = OG.undeclared_report(
        st, cfg, "T", repo=REPO, read=_reader({"T": ["a/1", "README.md"]})
    )
    assert only_shared == ""  # a/1 is declared, README.md is shared
    mixed = OG.undeclared_report(
        st, cfg, "T", repo=REPO, read=_reader({"T": ["README.md", "tests/t.py"]})
    )
    assert "README.md" not in mixed
    assert "tests/t.py" in mixed


def test_untouched_report_shared_only_writes_say_nothing():
    # B38c3ac0f60: when every declared glob went untouched and the only writes are shared
    # pins, there is nothing to declare OR narrow -- never a degenerate `--globs ''`.
    st = _state(_task("T", ["a/**"]))
    cfg = Config()
    cfg.lease.shared_globs = ["README.md"]
    assert (
        OG.untouched_report(st, cfg, "T", repo=REPO, read=_reader({"T": ["README.md"]}))
        == ""
    )


def test_block_blocks_only_a_known_write_never_unknown():
    st = _state(_task("T", ["a/**"]))
    cfg = Config()
    cfg.locks.undeclared_write = "block"
    import ddflow.services.observed_globs as mod

    orig = mod._read
    try:
        mod._read = lambda read: _reader({"T": ["z/1"]})
        assert OG.verdict_notice(st, cfg, "T", REPO)[1] is True
        mod._read = lambda read: _reader({"T": None})
        text, blocks = OG.verdict_notice(st, cfg, "T", REPO)
        assert text == OG.unknown("T") and blocks is False
    finally:
        mod._read = orig
    cfg.locks.undeclared_write = "warn"
    mod._read = lambda read: _reader({"T": ["z/1"]})
    try:
        assert OG.verdict_notice(st, cfg, "T", REPO)[1] is False
    finally:
        mod._read = orig


def test_untouched_report_offers_narrowing():
    st = _state(_task("T", ["a/**", "docs/**"]))
    text = OG.untouched_report(st, Config(), "T", repo=REPO, read=_reader({"T": ["a/1"]}))
    assert "docs/**" in text and "ddflow update T --globs 'a/**'" in text
    cfg = Config()
    cfg.locks.suggest_globs = False
    assert OG.untouched_report(st, cfg, "T", repo=REPO, read=_reader({"T": ["a/1"]})) == ""
    assert OG.untouched_report(st, Config(), "T", repo=REPO, read=_reader({"T": None})) == ""


def test_learned_suggests_habitual_misses_of_same_tag_tasks():
    a = _task("A", ["src/**"], ["locking"])
    b = _task("B", ["src/**"], ["locking"])
    c = _task("C", ["src/**"], ["other"])
    new = _task("N", ["src/**"], ["locking"])
    st = _state(a, b, c, new)
    table = {
        "A": ["src/1", "tests/a.py"],
        "B": ["src/2", "tests/b.py", "README.md"],
        "C": ["tests/c.py"],
    }
    for it in (a, b, c):
        it.state = "done"
    assert a.terminal
    found = OG.learned(st, Config(), "N", repo=REPO, read=_reader(table))
    assert found == ["tests/*"]
    text = OG.learned_report(st, Config(), "N", repo=REPO, read=_reader(table))
    assert "ddflow update N --globs 'src/**,tests/*'" in text
    cfg = Config()
    cfg.locks.suggest_globs = False
    assert OG.learned(st, cfg, "N", repo=REPO, read=_reader(table)) == []


def test_locks_knobs_are_declared():
    cfg = Config()
    assert cfg.locks.undeclared_write == "warn" and cfg.locks.suggest_globs is True


def test_cli_call_quotes_globs_and_bookkeeping_only_diff_offers_no_narrowing():
    assert "--globs 'a/**,my dir/*'" in OG.update_call("T", ["a/**"], ["my dir/*"])
    st = _state(_task("T", ["a/**"]))
    assert (
        OG.untouched_report(st, Config(), "T", repo=REPO, read=_reader({"T": [".ddflow/x"]})) == ""
    )
    assert "gate status T" in OG.unknown("T")


def test_every_glob_untouched_offers_to_redeclare_what_was_written():
    st = _state(_task("T", ["docs/**"]))
    text = OG.untouched_report(st, Config(), "T", repo=REPO, read=_reader({"T": ["src/m.py"]}))
    assert "docs/**" in text and "ddflow update T --globs 'src/*'" in text
    only_log = _reader({"T": [".ddflow/e.json"]})
    assert OG.untouched_report(st, Config(), "T", repo=REPO, read=only_log) == ""
    assert "ddflow_gate action=status id=T" in OG.unknown("T")


def test_a_task_with_no_declaration_has_every_write_reported():
    st = _state(_task("T", []))
    text = OG.undeclared_report(st, Config(), "T", repo=REPO, read=_reader({"T": ["src/a.py"]}))
    assert "src/a.py" in text and "ddflow update T --globs 'src/*'" in text


def test_mcp_form_quotes_a_path_with_a_space():
    assert "globs='a/**,my dir/*'" in OG.update_call("T", ["a/**"], ["my dir/*"])


def test_a_comma_in_a_glob_uses_the_json_list_form():
    call = OG.update_call("T", [], ["a,b/*"])
    cli, mcp = call.split(" (MCP: ", 1)
    assert """--globs '["a,b/*"]'""" in cli  # CLI half: the JSON list form
    assert mcp.startswith("ddflow_task action=update id=T ")
    assert """globs='["a,b/*"]'""" in mcp  # MCP half: the same JSON list form


def test_the_json_list_form_round_trips_through_the_globs_parser():
    from ddflow.core import globspec

    assert globspec.parse(['["a,b/*","c/**"]']) == ["a,b/*", "c/**"]
