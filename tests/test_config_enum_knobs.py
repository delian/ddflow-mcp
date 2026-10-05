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
    """The `a | b | c` run in `text`: read with parenthesised asides between the words
    removed (`off | fast (lint, ...) | full`), else from the raw text (`(a | b)`)."""
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


def _load_file(tmp_path: Path, text: str) -> Config:
    (tmp_path / ".ddflow").mkdir(exist_ok=True)
    (tmp_path / ".ddflow" / "config.toml").write_text(text)
    return Config.load(tmp_path, env={})


def test_a_file_with_an_unknown_value_still_loads_and_names_it(tmp_path: Path) -> None:
    # A config FILE may be newer than the code (a later release's value): it is recorded
    # and reported, never fatal -- and the knob takes its STRICTEST value, not its default
    # (decision D-enum-fallback-strict): a typo can only make ddflow more careful.
    cfg = _load_file(tmp_path, '[enforce]\nstale_docs = "blok"\n')
    assert cfg.enforce.stale_docs == "block"
    (entry,) = cfg.unknown_knobs
    assert entry.startswith("enforce.stale_docs = 'blok'")
    # The note doctor prints names the value actually in effect.
    assert "'block'" in entry and "in effect" in entry


def test_every_enum_knob_declares_its_strictest_value() -> None:
    assert set(C.KNOB_STRICTEST) == set(C.KNOB_CHOICES)
    for key, allowed in C.KNOB_CHOICES.items():
        assert C.strictest(key) in allowed, key
        assert C.KNOB_STRICTEST[key][1], f"{key}: say why that value is the strictest"


def test_each_knob_doc_names_the_value_a_bad_file_value_falls_back_to() -> None:
    for key in C.KNOB_STRICTEST:
        assert f"falls back to '{C.strictest(key)}'" in KNOB_DOCS[key], key


@pytest.mark.parametrize(
    ("toml", "attr", "expected"),
    [
        ('[enforce]\nstale_docs = "blok"\n', ("enforce", "stale_docs"), "block"),
        ('[enforce]\ncommit_without_lease = "x"\n', ("enforce", "commit_without_lease"), "block"),
        ('[upgrade]\nskew = "refusee"\n', ("upgrade", "skew"), "refuse"),
        ('[review]\non_exceed = "wran"\n', ("review", "on_exceed"), "refuse"),
        ('[gates]\nenforce_order = "x"\n', ("gates", "enforce_order"), "block"),
        ('[loops]\non_detect = "x"\n', ("loops", "on_detect"), "block"),
        ('[ci]\non_merge = "x"\n', ("ci", "on_merge"), "full"),
        ('[schedule]\nempty_phase = "x"\n', ("schedule", "empty_phase"), "problem"),
    ],
)
def test_a_bad_file_value_takes_the_strictest(
    tmp_path: Path, toml: str, attr: tuple[str, str], expected: str
) -> None:
    cfg = _load_file(tmp_path, toml)
    assert getattr(getattr(cfg, attr[0]), attr[1]) == expected


def test_a_valid_later_layer_still_wins_over_a_bad_file_value(tmp_path: Path) -> None:
    # The fallback applies to the layer that carried the typo; a valid value in the
    # machine-local layer, read after it, still sets the knob as before.
    cfg = _load_file(tmp_path, '[upgrade]\nskew = "of"\n')
    assert cfg.upgrade.skew == "refuse"
    (tmp_path / ".ddflow" / "local").mkdir()
    (tmp_path / ".ddflow" / "local" / "config.toml").write_text('[upgrade]\nskew = "warn"\n')
    assert Config.load(tmp_path, env={}).upgrade.skew == "warn"


def test_config_set_still_refuses_a_bad_value(repo: Path) -> None:
    code, _out, err = run_cli(repo, "init")
    assert code == 0, err
    before = (repo / ".ddflow" / "config.toml").read_text("utf-8")
    code, _out, err = run_cli(repo, "config", "--set", "enforce.stale_docs", "blok")
    assert code != 0 and "enforce.stale_docs" in err
    assert (repo / ".ddflow" / "config.toml").read_text("utf-8") == before


def test_an_env_value_outside_the_choices_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=re.escape("enforce.stale_docs")):
        Config.load(tmp_path, env={"DDFLOW_ENFORCE_STALE_DOCS": "blok"})


def test_this_projects_config_is_still_valid() -> None:
    for path in (ROOT / ".ddflow" / "config.toml", ROOT / ".ddflow" / "local" / "config.toml"):
        if path.is_file():
            Config.check(tomllib.loads(path.read_text("utf-8")))


def _loads_unchanged(tmp_path: Path, text: str) -> None:
    """The file loads with no fallback: every enum knob it sets holds the value written."""
    cfg = _load_file(tmp_path, text)
    assert cfg.unknown_knobs == []
    for sec, values in tomllib.loads(text).items():
        if not isinstance(values, dict) or sec in Config._FOREIGN_TABLES:
            continue
        for knob, value in values.items():
            if f"{sec}.{knob}" in C.KNOB_CHOICES:
                assert getattr(getattr(cfg, sec), knob) == value, f"{sec}.{knob}"


def test_this_projects_config_loads_unchanged(tmp_path: Path) -> None:
    _loads_unchanged(tmp_path, (ROOT / ".ddflow" / "config.toml").read_text("utf-8"))


def test_a_freshly_initialised_config_is_valid(repo: Path, tmp_path: Path) -> None:
    code, _out, err = run_cli(repo, "init")
    assert code == 0, err
    text = (repo / ".ddflow" / "config.toml").read_text("utf-8")
    Config.check(tomllib.loads(text))
    _loads_unchanged(tmp_path, text)


def test_every_declared_choice_is_named_in_the_knobs_doc() -> None:
    # The other direction: each declared value is named AS A VALUE in its knob's doc --
    # quoted ('x', `x`, "x") or inside its `a | b` run -- so a value nobody can find out
    # about, or one the doc dropped, fails here.
    for key, allowed in C.KNOB_CHOICES.items():
        doc = KNOB_DOCS[key]
        run = set(_alternatives(doc) or ())
        quoted = set(re.findall(r"""['`"]([\w-]+)['`"]""", doc))
        missing = [v for v in allowed if v not in run | quoted]
        assert not missing, f"{key}: the doc does not name {missing} as a value"


def test_derived_and_hand_written_checks_never_share_a_key() -> None:
    assert not set(C.KNOB_CHOICES) & set(C._VALUE_CHECKS)


def test_the_scanner_reads_a_parenthesised_run() -> None:
    assert _alternatives("(block | warn | off)") == ("block", "warn", "off")
    assert _alternatives("off | fast (lint, format) | full") == ("off", "fast", "full")


def test_the_modules_that_use_an_enum_take_its_values_from_config() -> None:
    # One declaration per value set: a consumer's own copy drifts from what `check` allows.
    from ddflow.core import flow as F
    from ddflow.services import ci as CI
    from ddflow.services import progress_line as PL

    choices = C.KNOB_CHOICES
    assert CI.ON_MERGE_MODES is choices["ci.on_merge"]
    assert PL.MODES is choices["session.progress_after_complete"]
    assert F.MODELS is choices["flow.model"] and F.FORGES is choices["flow.forge"]
    assert F.INTEGRATIONS is choices["flow.integration"]
    assert F.PR_MERGE is choices["flow.pr_merge"]
    assert F.ON_CHANGES is choices["flow.on_changes_requested"]
    assert F.PORT_STRATEGIES is choices["flow.port_strategy"]
