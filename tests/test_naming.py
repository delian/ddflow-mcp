"""Lane and agent names (D-lane-naming-2026-11-11): the template, its charset and length,
uniqueness scopes, the collision policy, resume, and the declared-name rule."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from conftest import run_cli

import ddflow.config as C
from ddflow.config import Config
from ddflow.core import naming

REFUSED = 3
OK = 0


def _cfg(**knobs: object) -> Config:
    cfg = Config()
    for k, v in knobs.items():
        setattr(cfg.naming, k, v)
    return cfg


def _seq_draw():
    """A deterministic entropy source: 'b', 'c', 'd', ... one symbol per call."""
    it = itertools.cycle(naming.RAND_ALPHABET)
    return lambda _alphabet: next(it)


def _fixed(_alphabet: str) -> str:
    return "b"


def test_the_default_template_makes_role_family_host_rand() -> None:
    got = naming.generate(Config(), role="lane3", family="kilo", host="box", draw=_seq_draw())
    assert got == naming.Resolved("lane3-kilo-box-bcdf", naming.GENERATED)


def test_every_shipped_default_passes_its_own_rules() -> None:
    n = Config().naming
    assert naming.template_problem(n.template, n.scope) == ""
    for knob in (
        "template",
        "scope",
        "on_collision",
        "max_length",
        "rand_length",
        "max_tries",
        "host_length",
        "host_label",
    ):
        assert f"naming.{knob}" in C.KNOB_DOCS


def test_a_generated_name_is_lower_case_and_dash_safe() -> None:
    got = naming.generate(Config(), role="Close B/Viewers..", family="Kilo", host="Box_1")
    assert naming.name_problem(got.name, max_length=48) == "", got.name
    assert got.name.startswith("close-b-viewers-kilo-box-1-")


def test_an_empty_part_drops_out_with_its_dash() -> None:
    got = naming.generate(Config(), role="lane3", family="", host="", draw=_seq_draw())
    assert got.name == "lane3-bcdf"
    assert naming.generate(Config(), role="  ").name.startswith("agent-")


def test_max_tries_bounds_the_redraws() -> None:
    cfg = _cfg(max_tries=2)
    taken = [naming.generate(cfg, role="r", family="k", host="h", draw=_fixed).name]
    with pytest.raises(naming.NameRefused, match="2 tries"):
        naming.generate(cfg, role="r", family="k", host="h", taken=taken, draw=_fixed)


def test_the_rand_alphabet_has_no_vowels_or_lookalikes() -> None:
    assert not set("aeiou013") & set(naming.RAND_ALPHABET)
    assert len(set(naming.RAND_ALPHABET)) == 27
    assert len(naming.draw_rand(7)) == 7
    assert set(naming.draw_rand(200)) <= set(naming.RAND_ALPHABET)


def test_a_long_name_loses_role_first_then_family_never_host_or_rand() -> None:
    got = naming.generate(
        _cfg(max_length=24),
        role="a-very-long-role-label",
        family="kilo",
        host="box",
        draw=_seq_draw(),
    ).name
    assert len(got) <= 24 and got.endswith("-kilo-box-bcdf"), got
    squeezed = naming.generate(
        _cfg(max_length=16), role="role", family="verylongfamily", host="host", draw=_seq_draw()
    ).name
    assert len(squeezed) <= 16 and squeezed.endswith("-host-bcdf"), squeezed


def test_a_host_that_cannot_fit_is_refused_with_the_remedy() -> None:
    with pytest.raises(naming.NameRefused, match="max_length"):
        naming.render(_cfg(max_length=16), role="r", family="", host="x" * 12, rand="bcdfghjklmnp")


def test_a_taken_name_gets_a_new_suffix_by_default() -> None:
    first = naming.generate(Config(), role="lane3", family="kilo", host="box", draw=_seq_draw())
    # `taken` holds the name in another CASE: the check is case-folded.
    second = naming.generate(
        Config(),
        role="lane3",
        family="kilo",
        host="box",
        taken=[first.name.upper()],
        draw=_seq_draw(),
    )
    assert naming.fold(second.name) != naming.fold(first.name)
    assert second.source == naming.GENERATED


def test_refuse_names_the_taken_name_and_the_remedy() -> None:
    cfg = _cfg(on_collision="refuse")
    first = naming.generate(cfg, role="r", family="k", host="h", draw=_seq_draw())
    with pytest.raises(naming.NameRefused, match=first.name) as err:
        naming.generate(cfg, role="r", family="k", host="h", taken=[first.name], draw=_seq_draw())
    assert "on_collision" in str(err.value)


def test_seq_is_a_readable_attempt_counter_beside_rand() -> None:
    cfg = _cfg(template="{role}-{seq}-{rand}")
    first = naming.generate(cfg, role="lane", draw=_fixed)
    assert first.name == "lane-1-bbbb"
    again = naming.generate(cfg, role="lane", taken=[first.name], draw=_fixed)
    assert again.name == "lane-2-bbbb"  # the number moved on, so the same draw is a free name


def test_the_same_agent_resuming_keeps_its_name() -> None:
    held = "lane3-kilo-box-bcdf"
    got = naming.generate(
        Config(), role="other", family="kilo", host="box", taken=[held], current=held
    )
    assert got == naming.Resolved(held, naming.RESUMED)


def test_a_current_name_that_is_not_generated_shaped_is_not_kept() -> None:
    got = naming.generate(Config(), role="lane3", family="kilo", host="box", current="Bad_Name")
    assert got.source == naming.GENERATED


def test_a_name_that_never_ends_in_a_free_one_is_refused_not_looped() -> None:
    taken = [naming.generate(Config(), role="r", family="k", host="h", draw=_fixed).name]
    with pytest.raises(naming.NameRefused, match=f"{Config().naming.max_tries} tries"):
        naming.generate(Config(), role="r", family="k", host="h", taken=taken, draw=_fixed)


@pytest.mark.parametrize(
    ("template", "scope", "why"),
    [
        ("{role}-{family}", "global", "needs {rand}"),
        ("{role}-{seq}", "global", "needs {rand}"),
        ("{role}-{host}-{seq}", "host", "needs {rand}"),
        ("{role}-{host}", "host", "needs {rand}"),
        ("{role}-{rand}", "family", "needs {family}"),
        ("{family}-{rand}", "global", "{role}"),
        ("Lane-{role}-{rand}", "global", "lower-case"),
        ("{role}_{rand}", "global", "'_'"),
        ("{role}-{nope}-{rand}", "global", "unknown token {nope}"),
        ("{role}-{rand}-{rand}", "global", "twice"),
        ("{role}-{rand", "global", "unbalanced"),
        ("", "global", "non-empty"),
    ],
)
def test_an_invalid_template_is_refused(template: str, scope: str, why: str) -> None:
    problem = naming.template_problem(template, scope)
    assert why in problem, problem


@pytest.mark.parametrize(
    ("template", "scope"),
    [
        ("{role}-{family}-{host}-{rand}", "global"),
        ("{role}-{host}-{rand}", "global"),
        ("{role}-{rand}", "host"),
        ("{role}-{family}-{rand}-{seq}", "family"),
        ("x-{role}-{rand}", "global"),
    ],
)
def test_a_valid_template_is_accepted(template: str, scope: str) -> None:
    assert naming.template_problem(template, scope) == ""


def test_generate_refuses_a_template_its_scope_cannot_trust() -> None:
    cfg = _cfg(template="{role}-{family}-{host}-{seq}")  # global needs {rand}
    with pytest.raises(naming.NameRefused, match=r"needs \{rand\}"):
        naming.generate(cfg, role="r", family="k", host="h")


@pytest.mark.parametrize(
    "name",
    ["lane3-kilo", "bfix-lane1", "close-bviewers", "Monster3-ddflow-7267fe", "bugfixA", "a.b_c"],
)
def test_existing_names_stay_valid_as_declared(name: str) -> None:
    assert naming.declared(name) == naming.Resolved(name, naming.EXPLICIT)


def test_a_declared_name_is_never_rewritten_and_a_case_twin_is_noted() -> None:
    got = naming.declared("bugfixa", taken=["bugfixA", "other"])
    assert got.name == "bugfixa" and got.source == naming.EXPLICIT
    assert "bugfixA" in got.note and "case" in got.note
    assert naming.declared("bugfixA", taken=["bugfixA"]).note == ""  # itself, resuming


@pytest.mark.parametrize("bad", ["", "a/b", "has space", "x" * 65, "line\nbreak"])
def test_a_declared_name_must_still_be_a_usable_agent_name(bad: str) -> None:
    with pytest.raises(naming.NameRefused):
        naming.declared(bad)


def test_host_part_uses_the_label_then_the_short_hostname() -> None:
    assert naming.host_part(Config(), "Build-Box.example.org") == "build-box"
    assert naming.host_part(_cfg(host_label="ci-7"), "whatever.local") == "ci-7"
    assert len(naming.host_part(Config(), "a" * 40)) == Config().naming.host_length


@pytest.mark.parametrize(
    ("key", "value", "why"),
    [
        ("naming.template", "x_{role}-{rand}", "'_'"),
        ("naming.template", "x-{rand}", "{role}"),
        ("naming.scope", "galaxy", "naming.scope"),
        ("naming.max_length", "70", "16 to 63"),
        ("naming.rand_length", "1", "3 to 12"),
        ("naming.host_label", "Bad_Host", "lower-case"),
    ],
)
# A value beginning with '{' is refused by `config --set` itself: B-config-set-leading-brace.
def test_config_set_refuses_an_invalid_value(repo: Path, key: str, value: str, why: str) -> None:
    assert run_cli(repo, "init")[0] == OK
    code, out, err = run_cli(repo, "config", "--set", key, value)
    assert code == REFUSED and why in out + err, (out, err)


def test_config_set_accepts_a_valid_template_and_host_label_goes_local(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == OK
    assert run_cli(repo, "config", "--set", "naming.template", "x-{role}-{host}-{rand}")[0] == OK
    assert Config.load(repo).naming.template == "x-{role}-{host}-{rand}"
    assert run_cli(repo, "config", "--set", "naming.host_label", "ci-7")[0] == OK
    local = (repo / ".ddflow" / "local" / "config.toml").read_text("utf-8")
    assert "ci-7" in local
    assert "ci-7" not in (repo / ".ddflow" / "config.toml").read_text("utf-8")


def test_a_resumed_name_survives_a_template_that_is_invalid_now() -> None:
    held = "worker-bcdf"
    got = naming.generate(_cfg(template="{role}"), role="x", current=held)
    assert got == naming.Resolved(held, naming.RESUMED)


def test_a_resumed_name_that_is_a_case_twin_of_a_taken_one_carries_a_note() -> None:
    got = naming.generate(Config(), role="x", current="abc-12", taken=["ABC-12"])
    assert got.source == naming.RESUMED and "case" in got.note and "ABC-12" in got.note


def test_zero_tries_is_a_refusal_not_a_crash() -> None:
    with pytest.raises(naming.NameRefused, match="0 tries"):
        naming.generate(_cfg(max_tries=0), role="x")


def test_generate_derives_the_host_label_from_a_hostname() -> None:
    got = naming.generate(
        Config(), role="r", family="k", hostname="Build-Box.example.org", draw=_fixed
    )
    assert got.name == "r-k-build-box-bbbb"
    labelled = naming.generate(
        _cfg(host_label="ci-7"), role="r", family="k", hostname="x.y", draw=_fixed
    )
    assert labelled.name == "r-k-ci-7-bbbb"
