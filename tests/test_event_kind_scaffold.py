"""`scripts/event_kind.py` lists and applies the hand edits a new event kind needs, and
`test_event_upcasters --write` survives a half-written shard (DX-event-kind-scaffold)."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


EK = _load("_event_kind_script", ROOT / "scripts" / "event_kind.py")


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "frag").mkdir()
    monkeypatch.setattr(EK, "FRAGMENTS", tmp_path / "frag")
    shutil.copytree(ROOT / "ddflow/templates/events", tmp_path / "events")
    monkeypatch.setattr(EK, "EVENTS", tmp_path / "events")
    monkeypatch.setattr(EK, "ROOT", tmp_path)
    return tmp_path


def test_every_kind_of_the_tree_has_every_edit(capsys) -> None:
    assert EK.main(["check"]) == 0
    assert "nothing missing" in capsys.readouterr().out


def test_a_kind_with_no_edits_lists_all_of_them(tree: Path, capsys) -> None:
    assert EK.main(["check", "x.new"]) == 1
    out = capsys.readouterr().out
    for part in ("handler", "registry", "fragment"):
        assert f"x.new: {part}:" in out, out


def test_add_writes_the_declaration_and_leaves_the_handler(tree: Path, capsys) -> None:
    rc = EK.main(
        [
            "add", "x.new", "--why", "records a thing",
            "--field", "note=str", "--field", "ref=str", "--field", "n=int",
            "--text", "note", "--lookup", "ref=an id compared with another",
            "--exempt", "a machine record of a thing: no authored text to compare",
        ]
    )  # fmt: skip
    out = capsys.readouterr().out
    assert rc == 1 and "x.new: handler:" in out  # only the code is left
    for part in ("registry", "fragment"):
        assert f"x.new: {part}:" not in out, out
    assert (tree / "events/x.toml").exists()
    text = (tree / "events/x.toml").read_text()
    assert 'name = "x.new"' in text
    assert 'fields = { "n" = ["int"], "note" = ["str"], "ref" = ["str"] }' in text
    assert 'text = ["note"]' in text
    assert 'lookup = { "ref" = "an id compared with another" }' in text
    assert "exempt = " in text
    assert (tree / "frag/event_kind_added.x.new.toml").read_text().count("x.new") == 1


def test_add_twice_changes_nothing(tree: Path) -> None:
    argv = ["add", "x.new", "--why", "what it records", "--field", "n=int", "--exempt", "e" * 30]
    EK.main(argv)
    before = {p: p.read_text() for p in tree.rglob("*") if p.is_file()}
    # a repeated add of the same kind refuses, and the tree is untouched
    argv2 = ["add", "x.new", "--why", "what it records", "--field", "n=int", "--exempt", "e" * 30]
    with pytest.raises(SystemExit):
        EK.main(argv2)
    assert before == {p: p.read_text() for p in tree.rglob("*") if p.is_file()}


def test_a_checked_kind_declares_its_api_function(tree: Path) -> None:
    EK.main(
        [
            "add",
            "y.new",
            "--why",
            "records a note",
            "--field",
            "n=int",
            "--text",
            "n",
            "--checked",
            "items.task_add",
        ]
    )
    assert 'checked = "items.task_add"' in (tree / "events/y.toml").read_text()


def test_write_skips_a_half_written_shard(tmp_path: Path, capsys) -> None:
    up = _load("_event_upcasters_test", ROOT / "tests" / "test_event_upcasters.py")
    shard = tmp_path / "a.jsonl"
    good = (ROOT / "tests/fixtures/upgrade/log-0.1.3.jsonl").read_text().splitlines()[0]
    shard.write_text(good + '\n{"id": "half-writ')
    assert len(up.read_log([shard], lenient=True)) == 1
    assert "skipped a.jsonl:2" in capsys.readouterr().err
    with pytest.raises(ValueError):
        up.read_log([shard])


def test_a_refused_add_writes_nothing(tree: Path) -> None:
    before = {p: p.read_text() for p in tree.rglob("*") if p.is_file()}
    cases = (
        ("x.new", ["--exempt", "too short"]),
        ("x.new", ["--lookup", "nowhy"]),
        ("x.new", ["--field", "a=bogus"]),
        ("x.new", ["--checked", "items.task_add", "--exempt", "e" * 30]),
        ("x.new", ["--owner", "Bad Owner"]),
    )
    for kind, bad in cases:
        with pytest.raises(SystemExit):
            EK.main(["add", kind, "--why", "what it records", "--field", "n=int", *bad])
    assert before == {p: p.read_text() for p in tree.rglob("*") if p.is_file()}


def test_a_dotted_why_cannot_shrink_below_the_minimum(capsys) -> None:
    with pytest.raises(SystemExit):
        EK.main(["add", "w.new", "--why", "." * 30, "--exempt", "e" * 30])


def test_a_released_kind_gets_no_new_fragment(tree: Path, capsys) -> None:
    EK.main(["check", "task.added"])
    assert "task.added: fragment:" not in capsys.readouterr().out
    assert not (tree / "frag/event_kind_added.task.added.toml").exists()


def test_add_of_a_released_kind_writes_nothing(tree: Path) -> None:
    """A released kind is already declared and already shipped: `add` refuses it (it never
    re-declares or re-fragments an entry that exists)."""
    before = {p: p.read_bytes() for p in tree.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit, match=r"task\.added.*already declared"):
        EK.main(["add", "task.added", "--why", "A task was added to the queue."])
    assert before == {p: p.read_bytes() for p in tree.rglob("*") if p.is_file()}
    assert not (tree / "frag/event_kind_added.task.added.toml").exists()


def test_write_still_raises_on_a_line_that_is_json_but_not_an_event(tmp_path: Path) -> None:
    up = _load("_event_upcasters_test2", ROOT / "tests" / "test_event_upcasters.py")
    shard = tmp_path / "a.jsonl"
    shard.write_text('{"data": 5}\n')
    with pytest.raises(TypeError):
        up.read_log([shard], lenient=True)


def test_write_survives_a_cut_multibyte_character(tmp_path: Path) -> None:
    up = _load("_event_upcasters_test3", ROOT / "tests" / "test_event_upcasters.py")
    shard = tmp_path / "a.jsonl"
    shard.write_bytes(b'{"id": "\xe2\x82')
    assert up.read_log([shard], lenient=True) == []
