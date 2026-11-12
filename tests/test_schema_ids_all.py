"""Every `--json` body can carry a schema id and version (MP-schema-all, R-mode-parity 5).

Bare arrays (`progress`, `list`, ...) keep their exact shape by default (D-compat,
`ARRAY_PAYLOADS`). `[output].json_envelope` is the additive opt-in: with it on, a command whose
body is a bare array answers `{"schema": "<command>@<n>", "rows": [...]}` instead. Nothing that
was an object changes, and no schema version is bumped."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

A = "agent-a"
#: Commands whose `--json` body is a bare array today.
ARRAYS = (("progress",), ("loops",))


def _setup(repo: Path) -> None:
    assert run_cli(repo, "init", agent=A)[0] == 0
    run_cli(repo, "task", "add", "T1", "--title", "one", agent=A)


def _body(repo: Path, *argv: str):
    code, out, err = run_cli(repo, "--json", *argv, agent=A)
    assert code in (0, 1, 2), (argv, out, err)
    return json.loads(out)


@pytest.mark.parametrize("argv", ARRAYS)
def test_a_bare_array_stays_a_bare_array_by_default(repo, argv):
    _setup(repo)
    assert isinstance(_body(repo, *argv), list), "D-compat: the default shape is unchanged"


@pytest.mark.parametrize("argv", ARRAYS)
def test_with_the_envelope_on_every_array_carries_a_schema_id_and_version(repo, argv):
    _setup(repo)
    assert run_cli(repo, "config", "output.json_envelope", "true", agent=A)[0] == 0
    body = _body(repo, *argv)
    assert isinstance(body, dict), "the falsifier: an untagged array is still returned"
    schema, _, version = body["schema"].partition("@")
    assert schema and version.isdigit()
    assert isinstance(body["rows"], list)


def test_the_envelope_holds_exactly_the_rows_the_array_held(repo):
    _setup(repo)
    plain = _body(repo, "progress")
    run_cli(repo, "config", "output.json_envelope", "true", agent=A)
    assert _body(repo, "progress")["rows"] == plain


def test_an_object_body_is_the_same_with_the_envelope_on(repo):
    _setup(repo)
    before = _body(repo, "board")
    run_cli(repo, "config", "output.json_envelope", "true", agent=A)
    assert _body(repo, "board")["schema"] == before["schema"]


def test_a_refusal_body_is_never_wrapped(repo):
    _setup(repo)
    run_cli(repo, "config", "output.json_envelope", "true", agent=A)
    body = _body(repo, "show", "NOPE")
    assert "refusal" in body and "rows" not in body


def test_an_unrecognised_value_does_not_turn_it_on(repo):
    _setup(repo)
    code, _, _ = run_cli(repo, "config", "output.json_envelope", "maybe", agent=A)
    assert code != 0
    assert isinstance(_body(repo, "progress"), list)
