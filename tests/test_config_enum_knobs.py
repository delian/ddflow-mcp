"""Bug Beea0744a7b: enum-like config knobs accepted any value.

Twenty-odd knobs wrote their allowed values only in a comment (`# block | warn | off`),
so `Config.check` -- the gate every config WRITE goes through -- passed
`enforce.stale_docs = "blok"`, and the misspelt policy quietly behaved as some other
value. These tests find every knob whose comment or doc lists alternatives (`a | b | c`)
straight from the source, so a knob added later with only a comment fails here too.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from conftest import run_cli

import ddflow.config as C
from ddflow.config import KNOB_DOCS, Config

ROOT = Path(__file__).resolve().parents[1]
_ALTS = r"[\w-]+(?:\s*\|\s*[\w-]+)+"


def _alternatives(text: str) -> tuple[str, ...] | None:
    """The `a | b | c` run in `text`, ignoring parenthesised asides between the words."""
    for candidate in (re.sub(r"\s*\([^)]*\)", "", text), text):
        if m := re.search(_ALTS, candidate):
            return tuple(re.split(r"\s*\|\s*", m.group(0)))
    return None


def _section_of_class() -> dict[str, str]:
    cfg = Config()
    return {type(getattr(cfg, sec)).__name__: sec for sec in cfg._sections()}


def _listed_alternatives() -> dict[str, tuple[str, ...]]:
    """`section.knob` -> the alternatives its source comment or knob doc lists."""
    sections = _section_of_class()
    found: dict[str, tuple[str, ...]] = {}
    cls = None
    comment = ""  # the `#:` comment block above the next field
    for line in Path(C.__file__).read_text("utf-8").splitlines():
        if m := re.match(r"class (\w+)", line):
            cls, comment = m.group(1), ""
            continue
        if cls not in sections:
            continue
        if m := re.match(r"\s+#:(.*)", line):
            comment += " " + m.group(1)
            continue
        if m := re.match(r"\s+(\w+): str = [^#]*(?:#(.*))?$", line):
            alts = _alternatives(m.group(2) or "") or _alternatives(comment)
            if alts:
                found[f"{sections[cls]}.{m.group(1)}"] = alts
        comment = ""
    cfg = Config()
    for key, doc in KNOB_DOCS.items():
        sec, knob = key.split(".", 1)
        if not isinstance(getattr(getattr(cfg, sec, None), knob, None), str):
            continue
        if key not in found and (alts := _alternatives(doc)):
            found[key] = alts
    return found


LISTED = _listed_alternatives()


def test_the_scan_finds_the_knobs_the_bug_named() -> None:
    # Guard the scanner itself: a regex that matched nothing would pass every test below.
    for key in (
        "enforce.stale_docs",
        "lease.reclaim_policy",
        "worktree.merge_strategy",
        "session.progress_after_complete",
        "ci.on_merge",
        "dedupe.on_match",
    ):
        assert key in LISTED, key
    assert len(LISTED) >= 21


@pytest.mark.parametrize("key", sorted(LISTED))
def test_a_value_outside_the_listed_alternatives_is_refused(key: str) -> None:
    sec, knob = key.split(".", 1)
    with pytest.raises(ValueError, match=re.escape(key)):
        Config.check({sec: {knob: "not-a-listed-value"}})


@pytest.mark.parametrize("key", sorted(LISTED))
def test_every_listed_alternative_is_accepted(key: str) -> None:
    sec, knob = key.split(".", 1)
    for value in LISTED[key]:
        Config.check({sec: {knob: value}})


def test_the_probe_from_the_bug() -> None:
    with pytest.raises(ValueError, match=re.escape("enforce.stale_docs")):
        Config.check({"enforce": {"stale_docs": "blok"}})
    with pytest.raises(ValueError, match=re.escape("lease.reclaim_policy")):
        Config.check({"lease": {"reclaim_policy": "autoo"}})
    # gates.enforce_order lists its values in quotes in its doc, not as `a | b`.
    with pytest.raises(ValueError, match=re.escape("gates.enforce_order")):
        Config.check({"gates": {"enforce_order": "blok"}})


def test_the_declared_choices_are_the_listed_ones_and_hold_the_default() -> None:
    choices = C.KNOB_CHOICES
    cfg = Config()
    for key, alts in LISTED.items():
        assert set(choices.get(key, ())) == set(alts), key
    for key, allowed in choices.items():
        sec, knob = key.split(".", 1)
        assert getattr(getattr(cfg, sec), knob) in allowed, key


def test_a_file_with_an_unknown_value_still_loads_and_names_it(tmp_path: Path) -> None:
    # A config FILE may be newer than the code (a later release's value): it is recorded
    # and reported, never fatal, and the knob keeps its default -- like an unknown knob.
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text('[enforce]\nstale_docs = "blok"\n')
    cfg = Config.load(tmp_path, env={})
    assert cfg.enforce.stale_docs == "warn"
    assert "enforce.stale_docs = 'blok'" in cfg.unknown_knobs


def test_an_env_value_outside_the_choices_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=re.escape("enforce.stale_docs")):
        Config.load(tmp_path, env={"DDFLOW_ENFORCE_STALE_DOCS": "blok"})


def test_this_projects_config_is_still_valid() -> None:
    for path in (ROOT / ".ddflow" / "config.toml", ROOT / ".ddflow" / "local" / "config.toml"):
        if path.is_file():
            Config.check(tomllib.loads(path.read_text("utf-8")))


def test_a_freshly_initialised_config_is_valid(repo: Path) -> None:
    code, _out, err = run_cli(repo, "init")
    assert code == 0, err
    Config.check(tomllib.loads((repo / ".ddflow" / "config.toml").read_text("utf-8")))


def test_every_declared_choice_is_named_in_the_knobs_doc() -> None:
    # The other direction: a value declared for a knob that its doc never mentions is a
    # value nobody can find out about, or one the doc dropped.
    for key, allowed in C.KNOB_CHOICES.items():
        doc = KNOB_DOCS[key]
        missing = [v for v in allowed if not re.search(rf"(?<![\w-]){re.escape(v)}(?![\w-])", doc)]
        assert not missing, f"{key}: the doc does not name {missing}"


def test_the_scanner_reads_a_parenthesised_run() -> None:
    assert _alternatives("(block | warn | off)") == ("block", "warn", "off")
    assert _alternatives("off | fast (lint, format) | full") == ("off", "fast", "full")
