"""`scripts/event_kind.py` lists and applies the hand edits a new event kind needs, and
`test_event_upcasters --write` survives a half-written shard (DX-event-kind-scaffold)."""

from __future__ import annotations

import importlib.util
import json
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


def test_every_kind_of_the_tree_has_all_five_edits(capsys) -> None:
    assert EK.main(["check"]) == 0
    assert "nothing missing" in capsys.readouterr().out


def test_a_kind_with_no_edits_lists_all_of_them(capsys) -> None:
    assert EK.main(["check", "x.new"]) == 1
    out = capsys.readouterr().out
    for part in ("handler", "fixture", "coherence", "fragment"):
        assert f"x.new: {part}:" in out, out


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for rel in (
        "tests/fixtures/event_kinds.json",
        "ddflow/core/redact.py",
        "tests/test_coherence_coverage.py",
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tmp_path / rel)
    (tmp_path / "frag").mkdir()
    monkeypatch.setattr(EK, "FIXTURE", tmp_path / "tests/fixtures/event_kinds.json")
    monkeypatch.setattr(EK, "REDACT", tmp_path / "ddflow/core/redact.py")
    monkeypatch.setattr(EK, "COHERENCE", tmp_path / "tests/test_coherence_coverage.py")
    monkeypatch.setattr(EK, "FRAGMENTS", tmp_path / "frag")
    monkeypatch.setattr(EK, "ROOT", tmp_path)
    return tmp_path


def test_add_writes_the_data_edits_and_leaves_the_handler(tree: Path, capsys) -> None:
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
    for part in ("fixture", "redact", "coherence", "fragment"):
        assert f"x.new: {part}:" not in out, out
    snap = json.loads((tree / "tests/fixtures/event_kinds.json").read_text())
    assert snap["kinds"]["x.new"]["fields"] == {"n": ["int"], "note": ["str"], "ref": ["str"]}
    assert '"x.new": ("note",),' in (tree / "ddflow/core/redact.py").read_text()
    assert (tree / "frag/event_kind_added.x.new.toml").read_text().count("x.new") == 1


def test_add_twice_changes_nothing(tree: Path) -> None:
    argv = ["add", "x.new", "--why", "w", "--field", "n=int", "--exempt", "e" * 30]
    EK.main(argv)
    before = {p: p.read_text() for p in tree.rglob("*") if p.is_file()}
    EK.main(argv)
    assert before == {p: p.read_text() for p in tree.rglob("*") if p.is_file()}


def test_write_skips_a_half_written_shard(tmp_path: Path, capsys) -> None:
    up = _load("_event_upcasters_test", ROOT / "tests" / "test_event_upcasters.py")
    shard = tmp_path / "a.jsonl"
    good = (ROOT / "tests/fixtures/upgrade/log-0.1.3.jsonl").read_text().splitlines()[0]
    shard.write_text(good + '\n{"id": "half-writ')
    assert len(up.read_log([shard], lenient=True)) == 1
    assert "skipped a.jsonl:2" in capsys.readouterr().err
    with pytest.raises(ValueError):
        up.read_log([shard])
