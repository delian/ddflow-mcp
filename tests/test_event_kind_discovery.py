"""A new event kind is declared in its OWN ``ddflow/templates/events/<owner>.toml`` and edits
no shared file (LK-events-autodiscover): the field schema, the redaction tables and the
duplicate-check classification are DERIVED from the declarations, so
``tests/fixtures/event_kinds.json``, ``core/redact/log.py`` and ``test_coherence_coverage.py``
regenerate from the declarations byte-identically."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_event_upcasters import dump, load

from ddflow.core import eventkinds as EK
from ddflow.core import redact as R

FIXTURE = Path(__file__).parent / "fixtures" / "event_kinds.json"
LOG = Path(__file__).resolve().parent.parent / "ddflow" / "core" / "redact" / "log.py"
COHERENCE = Path(__file__).resolve().parent / "test_coherence_coverage.py"
COHERENCE_EXEMPT = Path(__file__).resolve().parent / "coherence_exempt.py"
MODEL = Path(__file__).resolve().parent.parent / "ddflow" / "core" / "model.py"

_THROWAWAY = """\
# Event kinds owned by zzz. One [[kind]] per kind.

[[kind]]
name = "zzz.throwaway"
doc = "A throwaway kind that proves discovery edits no shared file."
fields = { "n" = ["int"], "note" = ["str"], "ref" = ["str"] }
text = ["note"]
lookup = { "ref" = "an id compared with another" }
exempt = "a machine record of a throwaway thing: no authored text to compare"
"""


def test_the_fixture_regenerates_byte_identically() -> None:
    declared = EK.load_dir()
    assert EK.field_schema(declared) == load()
    assert FIXTURE.read_text(encoding="utf-8") == dump(EK.field_schema(declared))


def test_the_redaction_tables_are_the_declarations_rows() -> None:
    declared = EK.load_dir()
    assert dict(R.LOG_TEXT_FIELDS) == dict(EK.text_fields(declared))
    assert dict(R.LOG_VERBATIM_FIELDS) == dict(EK.verbatim_fields(declared))


def test_the_coherence_classification_is_the_declarations_rows() -> None:
    declared = EK.load_dir()
    import coherence_exempt as CE

    assert dict(EK.exempt(declared)) == CE.EXEMPT
    # `CHECKED` in test_coherence_coverage.py is the same derivation (kept here in the same
    # form the test module computes it), so import it to pin the parity.
    from test_coherence_coverage import CHECKED

    assert dict(EK.checked(declared)) == CHECKED


def test_a_throwaway_kind_is_discovered_and_edits_no_shared_file(tmp_path: Path) -> None:
    """The production seam: `load_dir` (the scan the registry and every derived surface use) is
    what discovers a new kind, and the shared files regenerate from it byte-identically -- so a
    new kind is declared in its own file and none of the former shared files (nor the fold) is
    edited."""
    shared = (FIXTURE, LOG, COHERENCE, COHERENCE_EXEMPT, MODEL)
    before = {p: p.read_bytes() for p in shared}
    (tmp_path / "zzz_throwaway.toml").write_text(_THROWAWAY, encoding="utf-8")

    # the committed fixture IS the scan's field schema (byte-identical regeneration), so the
    # shared files hold no per-kind hand-written data to edit
    assert FIXTURE.read_text(encoding="utf-8") == dump(EK.field_schema(EK.load_dir()))

    declared = EK.load_dir(tmp_path)
    assert [k.name for k in declared] == ["zzz.throwaway"]
    assert EK.field_schema(declared)["kinds"]["zzz.throwaway"]["fields"] == {
        "n": ["int"],
        "note": ["str"],
        "ref": ["str"],
    }
    assert EK.text_fields(declared)["zzz.throwaway"] == ("note",)
    assert EK.verbatim_fields(declared)[("zzz.throwaway", "ref")] == "an id compared with another"
    assert EK.exempt(declared)["zzz.throwaway"]
    # none of the former shared files (nor the fold) was edited to declare the kind
    assert {p: p.read_bytes() for p in shared} == before


def test_a_declaration_rejecting_the_new_keys_is_refused() -> None:
    """The new keys are validated at parse, so a kind with contradictory classification or a
    bad type refuses with its place named."""
    with pytest.raises(EK.EventKindError, match="checked and exempt"):
        EK.parse("[[kind]]\nname = 'a.b'\ndoc = 'x'\nchecked = 'a'\nexempt = 'b'", owner="o")
    with pytest.raises(EK.EventKindError, match="types must name"):
        EK.parse("[[kind]]\nname = 'a.b'\ndoc = 'x'\nfields = { f = ['bogus'] }", owner="o")
