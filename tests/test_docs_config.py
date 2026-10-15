"""`[docs]`: the config surface of the mechanical documentation check (D-docs-stage).

The check itself is `ddflow/services/docscheck.py`, reported by `ddflow docs check`
(B-docs-check-command); this section is what a project configures it WITH. These tests pin
that surface: the quiet defaults the operator chose, the values each knob accepts, that a
typo in a config file cannot make the check LESS careful, and that the documents to check
REUSE `[enforce].doc_globs`/`doc_exclude` instead of copying them (the task's own words:
"reuses ... not duplicated").
"""

from __future__ import annotations

from pathlib import Path

import pytest

import ddflow.config as C
from ddflow.config import DOCS_LINK_MODES, EXTRACT_KINDS, KNOB_CHOICES, KNOB_OUTWARD, Config


def _load_file(tmp_path: Path, text: str) -> Config:
    (tmp_path / ".ddflow").mkdir(exist_ok=True)
    (tmp_path / ".ddflow" / "config.toml").write_text(text)
    return Config.load(tmp_path, env={})


def test_the_defaults_are_the_quiet_ones_the_operator_chose():
    """Warn (not block), local links (not network), no lint, nothing checked yet.

    D-docs-stage rejected both alternatives by name: block by default (every false positive
    then needs a skip) and network link checks by default (CI flakiness).
    """
    cfg = Config()

    assert cfg.docs.severity == "warn"
    assert cfg.docs.check_links == "local"
    assert cfg.docs.check_paths == []  # empty = the [enforce] doc set, reused below
    assert cfg.docs.lint_command == ""
    assert cfg.docs.cli_command == []
    assert cfg.docs.ignore_paths == []
    assert cfg.docs.ignore_names == []
    assert cfg.docs.extract == []


def test_every_knob_is_documented_and_explained():
    explained = [k for k, *_ in Config().explain() if k.startswith("docs.")]
    assert explained == [
        "docs.severity",
        "docs.check_links",
        "docs.link_timeout_s",
        "docs.check_paths",
        "docs.lint_command",
        "docs.ignore_paths",
        "docs.ignore_names",
        "docs.extract",
        "docs.count",
        "docs.cli_command",
        "docs.command_timeout_s",
    ]
    assert all(len(C.KNOB_DOCS[k]) > 80 for k in explained), "each knob needs a real doc"


def test_the_section_is_re_exported_under_its_own_name():
    from ddflow.config_sections import docs as D

    assert C.DocsConfig is D.DocsConfig
    assert "docs" in Config()._sections()


def test_severity_reuses_the_shared_policy_triple():
    """One definition of block|warn|off, not a second copy of the triple."""
    from ddflow.config_sections.enforce import BLOCK_WARN_OFF

    assert KNOB_CHOICES["docs.severity"] == BLOCK_WARN_OFF


def test_the_link_modes_are_the_three_the_checker_switches_on():
    assert KNOB_CHOICES["docs.check_links"] == DOCS_LINK_MODES == ("off", "local", "network")


def test_the_extract_kinds_are_the_three_families():
    assert EXTRACT_KINDS == ("flags", "tools", "knobs")


def test_network_links_are_not_an_outward_value():
    """A link check is a read-only GET, so it is NOT outward.

    Follows the precedent `upgrade.release_check` records in its own test ("a read-only GET,
    no write"): `outward` marks what WRITES outside this clone. The consequence here is that
    `check_links`'s strictest fallback ("local") is a legitimate fallback, not an outward one.
    """
    assert KNOB_OUTWARD["docs.check_links"] == frozenset()


@pytest.mark.parametrize(
    ("toml", "attr", "expected"),
    [
        ('[docs]\nseverity = "blck"\n', ("docs", "severity"), "warn"),
        ('[docs]\ncheck_links = "remote"\n', ("docs", "check_links"), "local"),
    ],
)
def test_a_typo_in_a_file_takes_the_strictest_value_never_a_looser_one(
    tmp_path: Path, toml: str, attr: tuple[str, str], expected: str
) -> None:
    """D-enum-fallback-strict: reported, and the knob goes to its strictest value.

    Both strictest values are also the defaults, which is the point: a typo in `severity`
    cannot silently stop reporting (whether by falling to `off` or by surprising the
    operator with `block`).
    """
    cfg = _load_file(tmp_path, toml)

    assert getattr(getattr(cfg, attr[0]), attr[1]) == expected
    assert any(attr[1] in note for note in cfg.unknown_knobs), cfg.unknown_knobs


def test_a_non_positive_link_timeout_is_refused() -> None:
    with pytest.raises(ValueError):
        Config.check({"docs": {"link_timeout_s": 0}})


def test_a_valid_extract_table_round_trips(tmp_path: Path) -> None:
    cfg = _load_file(
        tmp_path,
        "[docs]\n"
        'severity = "block"\n'
        "check_paths = ['README.md', 'docs/**']\n"
        "[[docs.extract]]\n"
        'kind = "flags"\n'
        "pattern = '(--[a-z][a-z-]+)'\n"
        "source_globs = ['ddflow/surfaces/**']\n",
    )

    assert cfg.docs.severity == "block"
    assert cfg.docs.check_paths == ["README.md", "docs/**"]
    (entry,) = cfg.docs.extract
    assert entry["kind"] == "flags"
    assert entry["pattern"] == "(--[a-z][a-z-]+)"
    assert entry["source_globs"] == ["ddflow/surfaces/**"]
