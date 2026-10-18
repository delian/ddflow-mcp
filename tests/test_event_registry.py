"""The event-kind registry: every kind the fold handles is declared as data, once, by one
owner, and the declaration agrees with the payload versions and the snapshot
(SE-event-registry). A kind added without a declaration fails here."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ddflow.core import eventkinds as EK
from ddflow.core.model import HANDLERS

ROOT = Path(__file__).resolve().parent.parent
DECLARED = EK.load_dir()


def test_the_fold_and_the_registry_agree() -> None:
    assert EK.problems(DECLARED, HANDLERS) == []


def test_the_registry_serves_every_declared_kind() -> None:
    assert set(EK.EVENT_KINDS) == set(HANDLERS)
    assert EK.EVENT_KINDS["lease.waiting"].owner == "waiting"


def test_an_unregistered_kind_fails_the_ratchet() -> None:
    bad = EK.problems(DECLARED, {*HANDLERS, "x.new"})
    assert bad == ["x.new: handled by the fold but declared by no owner"]


def test_a_declaration_with_no_handler_fails_the_ratchet() -> None:
    extra = [*DECLARED, EK.EventKind("x.ghost", "waiting", "a kind nothing folds")]
    assert EK.problems(extra, HANDLERS) == ["x.ghost: declared by waiting but nothing folds it"]


def test_a_kind_declared_twice_names_both_owners() -> None:
    dup = [*DECLARED, EK.EventKind("lease.waiting", "other", "the same kind again")]
    assert any("declared twice, by waiting and other" in p for p in EK.problems(dup, HANDLERS))


def test_a_bad_doc_retention_or_version_is_named() -> None:
    k = EK.EventKind("lease.waiting", "waiting", "short", retention="forever", v=3)
    got = EK.problems([k], ["lease.waiting"])
    assert len(got) == 3, got
    assert any("doc" in p for p in got) and any("retention" in p for p in got)
    assert any("v3" in p and "v1" in p for p in got)


def test_a_payload_version_bump_must_be_declared() -> None:
    k = EK.EventKind("lease.waiting", "waiting", "a record of who waits for whom")
    assert EK.problems([k], ["lease.waiting"], versions={"lease.waiting": 2})


def test_every_kind_of_the_snapshot_is_declared() -> None:
    snap = json.loads((ROOT / "tests/fixtures/event_kinds.json").read_text())["kinds"]
    assert set(snap) == {k.name for k in DECLARED}


@pytest.mark.parametrize(
    "text, message",
    [
        ("kind = 5", "must be an array"),
        ("kind = [5]", "must be a table"),
        ("[[kind]]\ndoc = 'x'", "needs a name"),
        ("[[kind]]\nname = 'a.b'\ndoc = 1", "doc must be a string"),
        ("[[kind]]\nname = 'a.b'\ndoc = 'x'\nowner = 'q'", "unknown key.s. owner"),
        ("[[kind]]\nname = 'a.b'\ndoc = 'x'\nv = 0", "v must be a positive integer"),
        ("[other]\nx = 1", "unknown top-level key"),
        ("[[kind", "not TOML"),
    ],
)
def test_a_malformed_declaration_is_refused_with_its_place(text: str, message: str) -> None:
    with pytest.raises(EK.EventKindError, match=message):
        EK.parse(text, owner="o", source="o.toml")


def test_the_owner_is_the_file_name() -> None:
    (k,) = EK.parse("[[kind]]\nname = 'a.b'\ndoc = 'x'", owner="things")
    assert (k.owner, k.retention, k.v) == ("things", "keep", 1)


def test_the_table_lists_every_kind_once() -> None:
    text = EK.table(DECLARED)
    assert text.count("| `") == len(DECLARED)
    assert "| `lease.waiting` | waiting | 1 | keep |" in text


def test_every_owner_file_is_a_whole_file_of_kinds() -> None:
    files = sorted(p.stem for p in EK.EVENTS_DIR.glob("*.toml"))
    assert files == sorted(EK.owners(DECLARED))
    for p in EK.EVENTS_DIR.glob("*.toml"):
        assert p.stat().st_size < 20_000, f"{p.name} is over 20 KB (D-small-files)"
