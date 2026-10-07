"""Config knobs declared once, on their dataclass field (B-uni-knobs, D-unify 4).

`knob(default, doc=, choices=, strictest=, outward=, check=)` is the one declaration; the
older form -- a bare field, a `_doc()` call and entries in config.py's tables -- is being
retired section by section, and the ratchet below counts what is left of it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, fields
from pathlib import Path

import pytest

import ddflow.config as C
from ddflow.config_sections import _docs as K

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ROOT / "ddflow" / "config_sections"
CONFIG_PY = ROOT / "ddflow" / "config.py"

#: The older declarations still standing. Lower these in the change that moves a section
#: onto `knob()`; the failure message prints the number to write. Never raise one.
LEGACY_BASELINE = {
    "_doc() calls": 42,
    "KNOB_CHOICES entries": 9,
    "KNOB_STRICTEST entries": 9,
    "KNOB_OUTWARD entries": 9,
}

_TABLES = ("KNOB_CHOICES", "KNOB_STRICTEST", "KNOB_OUTWARD")


def _legacy_counts() -> dict[str, int]:
    calls = 0
    for path in [*sorted(SECTIONS.glob("*.py")), CONFIG_PY]:
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            # `_doc(...)` and `anything._doc(...)` alike
            if (getattr(node.func, "id", "") or getattr(node.func, "attr", "")) == "_doc":
                calls += 1
    out = {"_doc() calls": calls}
    for name, table in _table_literals().items():
        if name in _TABLES:
            # `**_DC` and friends (keys None) are the declared knobs, not legacy entries
            out[f"{name} entries"] = sum(k is not None for d in table for k in d.keys)
    return out


@pytest.mark.parametrize("what", sorted(LEGACY_BASELINE))
def test_the_older_declarations_only_shrink(what: str) -> None:
    count, baseline = _legacy_counts()[what], LEGACY_BASELINE[what]
    assert count <= baseline, (
        f"{what}: {count}, the baseline is {baseline}. Declare a new knob with knob() on "
        "its dataclass field (config_sections/_docs.py), not with _doc() or a table entry."
    )
    assert count >= baseline, (
        f"{what}: {count}, below the baseline of {baseline}. Good -- now lower it: set "
        f'LEGACY_BASELINE["{what}"] = {count} in tests/test_config_knobs_declared.py.'
    )


def test_every_knob_is_declared_exactly_one_way() -> None:
    cfg = C.Config()
    for sec in cfg._sections():
        for f in fields(getattr(cfg, sec)):
            key = f"{sec}.{f.name}"
            declared = K.KNOB in f.metadata
            assert key in C.KNOB_DOCS, f"{key} has no doc"
            assert declared == (key in K.DECLARED), key


def test_a_declared_enum_is_in_every_table_and_checked() -> None:
    key = "loops.on_detect"
    assert key in K.DECLARED
    assert C.KNOB_CHOICES[key] == ("warn", "block")
    assert C.KNOB_STRICTEST[key][0] == "block"
    assert C.KNOB_OUTWARD[key] == frozenset()
    assert key in C._TOLERANT_VALUES
    assert C._KNOB_CHECKS[key]("block") == ""
    assert "must be one of warn, block" in C._KNOB_CHECKS[key]("blok")
    assert "falls back to 'block'" in C.KNOB_DOCS[key]  # the strictest appendix, as before


def test_a_typo_in_a_declared_enum_falls_back_to_its_strictest(tmp_path: Path) -> None:
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text('[loops]\non_detect = "blok"\n')
    cfg = C.Config.load(tmp_path)
    assert cfg.loops.on_detect == "block"
    assert any("loops.on_detect" in n for n in cfg.unknown_knobs)


def test_a_declared_mutable_default_is_not_shared() -> None:
    a, b = C.Config(), C.Config()
    a.lease.shared_globs.append("x")
    assert b.lease.shared_globs == []


def test_knob_refuses_an_incomplete_enum() -> None:
    with pytest.raises(ValueError, match="strictest"):
        K.knob("a", doc="d", choices=("a", "b"))
    with pytest.raises(ValueError, match="not one of"):
        K.knob("a", doc="d", choices=("a", "b"), strictest=("c", "why"))
    with pytest.raises(ValueError, match="not choices"):
        K.knob("a", doc="d", choices=("a", "b"), strictest=("a", "why"), outward={"z"})


def test_a_knob_declared_twice_is_an_error() -> None:
    with pytest.raises(ValueError, match="declared twice"):

        @K.declare("loops")
        @dataclass
        class Again:
            on_detect: str = K.knob("warn", doc="again")

    with pytest.raises(ValueError, match="declared twice"):
        K._doc("loops", "on_detect", "again")


def _table_literals() -> dict[str, list[ast.Dict]]:
    """config.py's knob tables and hand-written checks, by name, annotated or not -- every
    assignment of each (a table rebound later is still read whole). A table that is not a
    dict literal fails: this guard reads literals, and would otherwise go blind."""
    found: dict[str, list[ast.Dict]] = {}
    for node in ast.parse(CONFIG_PY.read_text("utf-8")).body:
        targets = [node.target] if isinstance(node, ast.AnnAssign) else []
        targets += node.targets if isinstance(node, ast.Assign) else []
        for t in targets:
            if getattr(t, "id", "") in (*_TABLES, "_VALUE_CHECKS"):
                assert isinstance(node.value, ast.Dict), t.id
                found.setdefault(t.id, []).append(node.value)
    return found


def _literal_keys() -> set[str]:
    """Keys written out in config.py's knob tables and its hand-written checks."""
    tables = _table_literals()
    assert set(tables) == {*_TABLES, "_VALUE_CHECKS"}, "a table moved: this guard went blind"
    return {
        k.value for ds in tables.values() for d in ds for k in d.keys if isinstance(k, ast.Constant)
    }


def test_no_declared_knob_is_left_in_a_config_table() -> None:
    """A table literal comes after the declared entries, so one left behind would shadow
    the field's declaration with no error."""
    assert not set(K.DECLARED) & _literal_keys()


#: Sections whose module is named otherwise: `[importer]` lives in imports.py (`import`
#: is a keyword, so neither the section nor the module can take the plain name).
_MODULE_OF = {"importer": "imports"}


def test_a_section_declares_only_its_own_knobs() -> None:
    for key, module in K.DECLARED_IN.items():
        section = key.split(".", 1)[0]
        assert module == f"ddflow.config_sections.{_MODULE_OF.get(section, section)}", (
            key,
            module,
        )
