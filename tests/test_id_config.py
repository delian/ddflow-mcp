"""`[ids]`: one template per record kind, defaults reproducing today's ids (B-id-config,
decision D-id-schemes-final)."""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

import pytest
from conftest import run_cli

import ddflow.config as C
from ddflow.config import Config
from ddflow.core import ids

OK, REFUSED = 0, 3


# -- golden: with no [ids] section every kind renders exactly today's shape ------------


def test_the_defaults_are_todays_shapes() -> None:
    t = Config().ids
    assert {k: getattr(t, k) for k in C.ID_KINDS} == {
        "bug": "{prefix}{hash}",
        "lesson": "{prefix}{hash}",
        "research": "{prefix}{hash}",
        "decision": "{prefix}{hash}",
        "memory": "{prefix}{hash}",
        "job": "{prefix}{hash}",
        "session": "s{time}-{pid}",
        "fix_task": "fix-{parent}",
        "fix_task_followup": "fix-{parent}-{seq}",
        "promotion": "promote-{env}-{seq}",
        "ci_bug": "Bci-{slug}-{digest}",
        "split_child": "{parent}.{seq}",
        "imported_phase": "{user-text}",
        "imported_task": "{phase}.{slug}",
    }


@pytest.mark.parametrize(("kind", "letter"), sorted(C.ID_PREFIXES.items()))
def test_a_hash_kind_renders_like_auto_id(kind: str, letter: str, monkeypatch) -> None:
    monkeypatch.setattr(ids.time, "time_ns", lambda: 1_700_000_000_000_000_000)  # the salt
    got = ids.render(Config(), kind, hash_parts=("title", "body"))
    assert re.fullmatch(f"{letter}[0-9a-f]{{10}}", got), got
    assert got == ids.auto_id(letter, "title", "body")


def test_the_session_shape() -> None:
    got = ids.render(Config(), "session", time=0.0, pid=4242)
    assert got == time.strftime("s%Y%m%dT%H%M%S", time.gmtime(0.0)) + "-4242"


def test_the_fix_task_shapes() -> None:
    t = Config()
    assert ids.render(t, "fix_task", parent="B0123456789") == "fix-B0123456789"
    assert ids.render(t, "fix_task_followup", parent="B0123456789", seq=2) == "fix-B0123456789-2"


def test_the_promotion_shape() -> None:
    assert ids.render(Config(), "promotion", env="staging", seq=3) == "promote-staging-3"


def test_the_ci_bug_shape_is_todays_bug_id() -> None:
    from ddflow.services.ci import bug_id

    check = "tests/test_x.py::test_y[a-b]"
    slug = re.sub(r"[^A-Za-z0-9]+", "-", check).strip("-").lower()[:30].strip("-")
    digest = hashlib.sha1(check.encode()).hexdigest()[:10]
    assert ids.render(Config(), "ci_bug", slug=slug, digest=digest) == bug_id(check)


def test_the_split_and_import_shapes() -> None:
    t = Config()
    assert ids.render(t, "split_child", parent="P.T", seq=2) == "P.T.2"
    assert ids.render(t, "imported_task", phase="P1", slug="do-it") == "P1.do-it"
    assert ids.render(t, "imported_phase", **{"user-text": "SESSION-ONE"}) == "SESSION-ONE"


def test_a_missing_field_is_an_error_naming_the_token() -> None:
    with pytest.raises(ValueError, match="parent"):
        ids.render(Config(), "fix_task")


# -- validation ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "template", "why"),
    [
        ("bug", "BUG-{sequence}", "unknown token"),
        ("bug", "BUG {seq}", "holds ' '"),
        ("bug", "BUG/{seq}", "holds '/'"),
        ("bug", "BUG-{seq", "unbalanced"),
        ("bug", "-{seq}", "must not start"),
        ("bug", "BUG..{seq}", ".."),
        ("bug", "{seq}..x", ".."),
        ("bug", "BUG-{date}", "source of uniqueness"),
        ("bug", "BUG-{slug}-{digest}-{seq}", "stable"),
        ("session", "{prefix}{hash}", "{prefix}"),
        ("lesson", "", "non-empty"),
    ],
)
def test_an_invalid_template_is_refused(kind: str, template: str, why: str) -> None:
    problem = C.id_template_problem(kind, template)
    assert why in problem, problem


@pytest.mark.parametrize(
    ("kind", "template"),
    [
        ("bug", "BUG-{seq}"),
        ("bug", "{prefix}-{date}-{seq}"),
        ("lesson", "LES-{time}-{pid}"),
        ("ci_bug", "CI-{slug}-{digest}"),
        ("decision", "ADR-{seq}"),
        ("split_child", "{parent}.{seq}.x"),  # a token between dots is not '..'
        ("lesson", "L.{seq}.{slug}"),
    ],
)
def test_a_valid_template_is_accepted(kind: str, template: str) -> None:
    assert C.id_template_problem(kind, template) == ""


def test_every_shipped_default_passes_its_own_rules() -> None:
    for kind in C.ID_KINDS:
        assert C.id_template_problem(kind, getattr(Config().ids, kind)) == "", kind


def test_config_set_refuses_an_invalid_template_naming_the_key(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == OK
    code, _o, err = run_cli(repo, "config", "--set", "ids.bug", "BUG-{date}")
    assert code == REFUSED and "ids.bug" in err, err
    assert run_cli(repo, "config", "--set", "ids.bug", "BUG-{seq}")[0] == OK
    assert Config.load(repo).ids.bug == "BUG-{seq}"


def test_an_invalid_template_in_a_file_still_loads_and_doctor_reports_it(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == OK
    with (repo / ".ddflow" / "config.toml").open("a", encoding="utf-8") as fh:
        fh.write('\n[ids]\nlesson = "LES-{date}"\n')
    cfg = Config.load(repo)
    assert cfg.ids.lesson == "{prefix}{hash}"  # the default stays in effect
    code, out, _e = run_cli(repo, "doctor")
    assert "ids.lesson" in out, out
    assert code != OK


def test_the_bugs_phase_name_is_held_to_the_id_characters(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == OK
    code, _o, err = run_cli(repo, "config", "--set", "bugs.phase", "my bugs")
    assert code == REFUSED and "bugs.phase" in err, err


def test_config_explain_documents_every_kind(repo: Path) -> None:
    for kind in C.ID_KINDS:
        assert len(C.KNOB_DOCS.get(f"ids.{kind}", "")) > 40, kind


def test_every_kind_has_a_template_reader() -> None:
    assert set(ids.TEMPLATE_OF) == set(C.ID_KINDS)


def test_a_given_prefix_wins() -> None:
    assert ids.render(Config(), "bug", prefix="X", hash_parts=("a",)).startswith("X")
