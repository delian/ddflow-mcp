"""The shared drop-in directory reader and the packaged layer's two layouts (CFG-defaults-d-loader).

`read_dropin_dir` is the one reader for a directory of `*.toml` files (the systemd `.d`
model): sorted by name, one table owned by one file, a failure naming file and line. The
packaged layer reads `templates/default.d/` and falls back to the single `default.toml`;
the same content must load the same values in either layout.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from _wait import slow_factor

from ddflow.config import Config, _packaged
from ddflow.config_sections import _layers as L
from ddflow.views import default_toml as DT

LOAD_BUDGET_MS = 25.0


def _put(d: Path, name: str, text: str) -> None:
    d.mkdir(exist_ok=True)
    (d / name).write_text(text, "utf-8")


def _split(text: str, into: Path) -> list[str]:
    """Split a rendered default.toml into one file per top-level table (`[gate.x]` per id)."""
    blocks: dict[str, list[str]] = {}
    key = "_header"
    for line in text.splitlines():
        m = re.match(r"\[([^\].]+)(?:\.([^\]]+))?\]", line)
        if m:
            key = m.group(1) if m.group(1) != "gate" else f"gate-{m.group(2)}"
        blocks.setdefault(key, []).append(line)
    for name, lines in blocks.items():
        _put(into, f"{name}.toml", "\n".join(lines) + "\n")
    return sorted(blocks)


# -- read_dropin_dir -------------------------------------------------------------------------


def test_files_are_read_in_name_order_and_merged_by_table(tmp_path):
    _put(tmp_path, "20-b.toml", "[b]\nx = 2\n")
    _put(tmp_path, "10-a.toml", "[a]\nx = 1\n")
    got = L.read_dropin_dir(tmp_path)
    assert got == {"a": {"x": 1}, "b": {"x": 2}}
    assert list(got) == ["a", "b"]


def test_a_missing_directory_reads_as_empty_and_non_toml_files_are_ignored(tmp_path):
    assert L.read_dropin_dir(tmp_path / "none") == {}
    _put(tmp_path, "notes.txt", "[a]\n")
    assert L.read_dropin_dir(tmp_path) == {}


def test_a_table_defined_in_two_files_is_an_error_naming_both(tmp_path):
    _put(tmp_path, "a.toml", "[lease]\nttl_s = 1\n")
    _put(tmp_path, "b.toml", "[lease]\nother = 2\n")
    with pytest.raises(L.DropinError) as e:
        L.read_dropin_dir(tmp_path)
    assert "a.toml" in str(e.value) and "b.toml" in str(e.value) and "lease" in str(e.value)


def test_sibling_subtables_of_a_container_in_two_files_merge(tmp_path):
    _put(tmp_path, "gate-a.toml", "[gate.a]\nx = 1\n")
    _put(tmp_path, "gate-b.toml", "[gate.b]\nx = 2\n")
    assert L.read_dropin_dir(tmp_path) == {"gate": {"a": {"x": 1}, "b": {"x": 2}}}


def test_a_table_and_its_subtable_may_live_in_different_files(tmp_path):
    _put(tmp_path, "01.toml", '[gate]\nmode = "strict"\n')
    _put(tmp_path, "02.toml", "[gate.a]\nx = 1\n")
    assert L.read_dropin_dir(tmp_path) == {"gate": {"mode": "strict", "a": {"x": 1}}}


def test_a_dotted_quoted_key_does_not_confuse_which_file_owns_a_table(tmp_path):
    _put(tmp_path, "01.toml", "[a.b]\nx = 1\n")
    _put(tmp_path, "02.toml", '["a.b"]\ny = 1\n')
    _put(tmp_path, "03.toml", "[a.b]\nz = 1\n")
    with pytest.raises(L.DropinError) as e:
        L.read_dropin_dir(tmp_path)
    assert "01.toml" in str(e.value) and "03.toml" in str(e.value)


def test_merge_mode_lets_the_later_file_win(tmp_path):
    _put(tmp_path, "10.toml", "[lease]\nttl_s = 1\nother = 2\n")
    _put(tmp_path, "20.toml", "[lease]\nttl_s = 9\n")
    got = L.read_dropin_dir(tmp_path, same_table="merge")
    assert got == {"lease": {"ttl_s": 9, "other": 2}}


def test_a_parse_failure_names_the_file_and_the_line(tmp_path):
    _put(tmp_path, "ok.toml", "[a]\nx = 1\n")
    _put(tmp_path, "bad.toml", "[b]\nx = 1\ny = = 2\n")
    with pytest.raises(L.DropinError) as e:
        L.read_dropin_dir(tmp_path)
    assert "bad.toml" in str(e.value) and "line 3" in str(e.value)


def test_a_conflicted_file_is_refused_as_a_config_conflict(tmp_path):
    _put(tmp_path, "a.toml", "[a]\nx = 1\n<<<<<<< ours\n=======\n>>>>>>> theirs\n")
    with pytest.raises(L.ConfigConflict):
        L.read_dropin_dir(tmp_path)


# -- the packaged layer: both layouts say the same -------------------------------------------


@pytest.fixture
def layout(monkeypatch):
    def use(single: Path, dropin: Path) -> None:
        monkeypatch.setattr(_packaged, "PACKAGED_DEFAULTS", single)
        monkeypatch.setattr(_packaged, "PACKAGED_DIR", dropin)
        _packaged.packaged.cache_clear()
        _packaged._SAID.clear()

    yield use
    _packaged.packaged.cache_clear()
    _packaged._SAID.clear()


def test_the_two_layouts_load_the_same_values_for_every_section(tmp_path, layout):
    text = DT.render()
    single = tmp_path / "default.toml"
    single.write_text(text, "utf-8")
    d = tmp_path / "default.d"
    names = _split(text, d)
    assert len(names) > 30
    assert L.read_dropin_dir(d) == L.read_toml(single)

    layout(single, tmp_path / "absent.d")
    one = Config()
    _packaged.apply_packaged(one)
    layout(tmp_path / "absent.toml", d)
    many = Config()
    _packaged.apply_packaged(many)
    assert _packaged.packaged() == L.read_toml(single)
    for sec in one._sections():
        assert getattr(one, sec) == getattr(many, sec), sec


def test_a_changed_value_in_a_drop_in_is_what_loads_and_is_called_default(tmp_path, layout):
    d = tmp_path / "default.d"
    d.mkdir()
    (d / "lease.toml").write_text("[lease]\nttl_s = 4321\n", "utf-8")
    layout(tmp_path / "absent.toml", d)
    cfg = Config()
    _packaged.apply_packaged(cfg)
    assert cfg.lease.ttl_s == 4321


def test_the_directory_wins_over_the_single_file_when_both_exist(tmp_path, layout):
    single = tmp_path / "default.toml"
    single.write_text("[lease]\nttl_s = 1111\n", "utf-8")
    d = tmp_path / "default.d"
    d.mkdir()
    (d / "lease.toml").write_text("[lease]\nttl_s = 2222\n", "utf-8")
    layout(single, d)
    assert _packaged.packaged() == {"lease": {"ttl_s": 2222}}


def test_a_damaged_drop_in_warns_names_the_file_and_leaves_the_built_in_defaults(
    tmp_path, layout, capsys
):
    d = tmp_path / "default.d"
    d.mkdir()
    (d / "lease.toml").write_text("[lease]\nttl_s = = 1\n", "utf-8")
    layout(tmp_path / "absent.toml", d)
    assert _packaged.packaged() == {}
    assert "lease.toml" in capsys.readouterr().err


def test_the_drop_in_load_cost_stays_inside_its_budget(tmp_path, layout):
    text = DT.render()
    d = tmp_path / "default.d"
    _split(text, d)
    layout(tmp_path / "absent.toml", d)
    best = float("inf")
    for _ in range(5):
        _packaged.packaged.cache_clear()
        start = time.perf_counter()
        _packaged.apply_packaged(Config())
        best = min(best, time.perf_counter() - start)
    assert best * 1000 < LOAD_BUDGET_MS * slow_factor()


def test_a_directory_named_like_a_drop_in_does_not_hide_the_single_file(tmp_path, layout):
    single = tmp_path / "default.toml"
    single.write_text("[lease]\nttl_s = 1111\n", "utf-8")
    d = tmp_path / "default.d"
    (d / "x.toml").mkdir(parents=True)
    layout(single, d)
    assert L.dropin_files(d) == []
    assert _packaged.packaged() == {"lease": {"ttl_s": 1111}}


def test_a_damaged_drop_in_warning_names_the_directory_not_the_single_file(
    tmp_path, layout, capsys
):
    d = tmp_path / "default.d"
    d.mkdir()
    (d / "lease.toml").write_text("[lease]\nttl_s = = 1\n", "utf-8")
    layout(tmp_path / "default.toml", d)
    _packaged.packaged()
    err = capsys.readouterr().err
    assert str(d) in err and "default.toml:" not in err


def test_an_unreadable_drop_in_directory_warns_and_leaves_the_built_in_defaults(
    tmp_path, layout, capsys, monkeypatch
):
    d = tmp_path / "default.d"
    d.mkdir()
    layout(tmp_path / "default.toml", d)

    def refuse(_directory):
        raise PermissionError(13, "Permission denied", str(d))

    monkeypatch.setattr(_packaged, "dropin_files", refuse)
    assert _packaged.packaged() == {}
    assert "Permission denied" in capsys.readouterr().err


def test_the_conflict_names_the_file_that_defined_the_table_not_one_that_only_held_it(tmp_path):
    _put(tmp_path, "01.toml", "[a.b]\nx = 1\n")
    _put(tmp_path, "02.toml", "[a]\ny = 1\n")
    _put(tmp_path, "03.toml", "[a]\nz = 1\n")
    with pytest.raises(L.DropinError) as e:
        L.read_dropin_dir(tmp_path)
    assert "02.toml" in str(e.value) and "01.toml" not in str(e.value)
