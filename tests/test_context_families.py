"""CC-inventory: family and harness context facts are data with provenance."""

from __future__ import annotations

from datetime import date

from ddflow.infra import paths
from ddflow.services import context_families as CF

HARNESSES = paths.package_dir() / "harnesses"
IDS = sorted(p.stem for p in HARNESSES.glob("*.toml"))
FAMILIES = ("claude", "openai", "gemini", "deepseek", "kimi", "qwen", "glm", "unknown")


def test_shipped_tables_are_well_formed():
    assert CF.validate(CF.load(), IDS) == []


def test_every_harness_id_has_a_row():
    rows = CF.load()["context_harnesses.toml"]["harness"]
    assert IDS and not [h for h in IDS if h not in rows]


def test_a_missing_harness_row_is_reported():
    data = CF.load()
    del data["context_harnesses.toml"]["harness"]["cody"]
    assert any("'cody' has no row" in p for p in CF.validate(data, IDS))


def test_every_listed_family_exists_with_provenance():
    rows = CF.load()["context_families.toml"]["family"]
    assert all(f in rows for f in FAMILIES)
    for doc in CF.load().values():
        for _, _, _, f in CF.facts(doc):
            assert f["src"] in doc["sources"] and date.fromisoformat(f["d"])


def test_a_fact_without_source_or_date_is_a_problem():
    data = CF.load()
    data["context_families.toml"]["family"]["claude"]["ttl"] = {"v": ["5m"], "s": "documented"}
    problems = CF.validate(data)
    assert any("source" in p for p in problems) and any("date" in p for p in problems)


def test_unverified_and_none_are_never_read_as_true():
    assert CF.usable({"v": 1.0, "s": "unverified", "src": "x", "d": "2026-01-01"}) is None
    assert CF.usable({"v": 1.0, "s": "none", "src": "x", "d": "2026-01-01"}) is None
    assert CF.usable({"s": "documented", "v": 0.1}) == 0.1
    assert CF.usable(None) is None
    data = CF.load()
    assert CF.usable(data["context_families.toml"]["family"]["kimi"]["read_mult"]) is None
    assert CF.usable(data["context_families.toml"]["family"]["claude"]["read_mult"]) == 0.1


def test_unknown_model_resolves_to_unknown_with_no_facts():
    data = CF.load()
    row = CF.family_row(data, "claude-ish-neighbour")
    assert row is data["context_families.toml"]["family"]["unknown"]
    assert not [k for k, v in row.items() if CF.is_fact(v)]


def test_old_facts_make_doctor_note_them():
    data = CF.load()
    assert CF.stale(data, 90, date(2026, 10, 29)) == []
    old = CF.stale(data, 90, date(2027, 2, 1))
    assert any(s.startswith("family.claude.ttl") for s in old)
    notes = CF.doctor_notes(90, date(2027, 2, 1))
    assert len(notes) == 1 and "older than 90 days" in notes[0]
    assert CF.doctor_notes(90, date(2026, 10, 29)) == []


def test_data_files_stay_small():
    for name in CF._FILES:
        assert (paths.templates_dir() / name).stat().st_size < 20_000


def test_unknown_family_carrying_a_fact_is_a_problem():
    data = CF.load()
    data["context_families.toml"]["family"]["unknown"]["ttl"] = {
        "v": 1,
        "s": "documented",
        "src": "cache-note",
        "d": "2026-10-29",
    }
    assert any("no neighbour" in p for p in CF.validate(data))


def test_a_malformed_or_misspelt_fact_is_not_silently_dropped():
    data = CF.load()
    fam = data["context_families.toml"]["family"]["claude"]
    fam["ttl"] = {
        "v": ["5m"],
        "src": "cache-note",
        "d": "2026-10-23",
    }  # no status: would be invisible
    fam["window"]["sorce"] = "typo"
    problems = CF.validate(data)
    assert any("claude.ttl is not a fact" in p for p in problems)
    assert any("claude.window has unknown field" in p for p in problems)
