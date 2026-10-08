"""Rules as recorded definitions (B-uni-rules-import, D-unify 7).

`services.guidance.deffields` is the one mapping between a rule file and the fields of its
`def.*` record. Its guarantees: the fields are plain JSON with every key present, a file the
view renders reads back to the same fields, a timestamp is not content, and the digest is the
one a `def.recorded` event carries.
"""

from __future__ import annotations

import json
from pathlib import Path

from ddflow.api import defs as ADEFS
from ddflow.config import Config
from ddflow.core import defs as D
from ddflow.core import redact as R
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import migrations as M
from ddflow.services.guidance import deffields as DF
from ddflow.services.guidance import fileformat
from ddflow.services.guidance.kinds import DECISION, RULE
from ddflow.services.redact_report import redactor

RICH = """id = "r-naming"
title = "Naming conventions"
tags = ["naming", "style"]
scope = "phase"
priority = 75
globs = ["**/*.py", "**/*.js"]
created = "2026-10-03T12:00:00"
updated = "2026-10-04T08:30:00"
categories = ["testing"]
gates = ["unit_tests"]
category = "style"
enforcement = "warn"
owner = "ops"
review_by = "2027-01-01"
sources = ["docs/style.md"]
checks = [{ run = "ruff check", on = "ci" }]
links = ["D-style"]

Follow snake_case for Python functions.
Use "quotes" and a \\ backslash too.
"""

MINIMAL = """id = "r-small"
title = "Small"

Short body.
"""


def _fields(text: str, spec=RULE):
    return DF.to_fields(fileformat.parse(text, spec), spec)


def test_the_fields_are_plain_json_with_every_key_present() -> None:
    f = _fields(MINIMAL)
    assert json.loads(json.dumps(f, allow_nan=False)) == f
    assert set(DF.FIELDS) <= set(f)
    assert f["title"] == "Small" and f["body"] == "Short body." and f["globs"] == []
    assert f["priority"] == 50 and f["status"] == "accepted" and f["level"] == "project"


def test_a_rendered_file_reads_back_to_the_same_fields() -> None:
    for text in (RICH, MINIMAL):
        rec = fileformat.parse(text, RULE)
        fields, prov = DF.to_fields(rec, RULE), DF.to_provenance(rec)
        again = fileformat.parse(DF.render(rec.id, fields, prov, RULE), RULE)
        assert DF.to_fields(again, RULE) == fields
        assert DF.to_provenance(again) == prov


def test_the_rich_file_keeps_everything_it_said() -> None:
    f = _fields(RICH)
    assert f["categories"] == ["testing"] and f["gates"] == ["unit_tests"]
    assert f["checks"] == [{"run": "ruff check", "on": "ci"}] and f["links"] == ["D-style"]
    assert f["enforcement"] == "warn" and f["owner"] == "ops" and f["review_by"] == "2027-01-01"
    assert f["level"] == "phase" and f["body"].endswith("a \\ backslash too.")
    prov = DF.to_provenance(fileformat.parse(RICH, RULE))
    assert prov == {"created": "2026-10-03T12:00:00", "updated": "2026-10-04T08:30:00"}


def test_a_timestamp_is_not_content(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path)
    later = RICH.replace('updated = "2026-10-04T08:30:00"', 'updated = "2026-12-24T00:00:00"')
    assert DF.digest_of(_fields(RICH), cfg) == DF.digest_of(_fields(later), cfg)
    edited = RICH.replace("snake_case", "camelCase")
    assert DF.digest_of(_fields(RICH), cfg) != DF.digest_of(_fields(edited), cfg)


def test_a_definition_missing_keys_takes_the_kinds_defaults() -> None:
    rec = DF.from_fields("r-old", {"title": "Old", "body": "text"}, {}, RULE)
    assert (rec.level, rec.priority, rec.enforcement, rec.status) == (
        "project",
        50,
        "advisory",
        "accepted",
    )
    assert (
        fileformat.parse(DF.render("r-old", {"title": "Old", "body": "text"}, {}, RULE), RULE).title
        == "Old"
    )


def test_a_kinds_own_fields_ride_along() -> None:
    text = 'id = "D-x"\ntitle = "X"\ncontext = "why"\nsupersedes = ["D-w"]\n\nWe decided.\n'
    f = _fields(text, DECISION)
    assert f["context"] == "why" and f["supersedes"] == ["D-w"]
    rec = DF.from_fields("D-x", f, {}, DECISION)
    assert DF.to_fields(rec, DECISION) == f


def test_the_digest_is_the_one_a_def_recorded_event_carries(repo: Path) -> None:
    cfg = Config.load(repo)
    fields = _fields(RICH)
    out = ADEFS.def_record(repo, "rule", "r-naming", fields, agent="t")
    assert out.exit == 0, out
    assert out.data["digest"] == DF.digest_of(fields, cfg)


def test_the_log_profile_is_applied_before_the_digest(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path)
    secret = _fields(RICH) | {"body": "token ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"}
    assert DF.logged(secret, cfg)["body"] != secret["body"]
    # the digest is of the LOGGED form: hashing the raw fields would differ from it
    independent = R.redact_leaves(dict(secret), redactor("log", cfg))
    assert DF.digest_of(secret, cfg) == D.digest(independent)
    assert DF.digest_of(secret, cfg) != D.digest(secret)


def test_nulls_in_an_old_definition_mean_the_default_not_the_word_none() -> None:
    nulls = dict.fromkeys(DF.FIELDS) | {"title": "T", "body": "b"}
    rec = DF.from_fields("r-old", nulls, {}, RULE)
    assert (rec.owner, rec.category, rec.priority, rec.level, rec.status) == (
        "",
        "",
        50,
        "project",
        "accepted",
    )
    text = DF.render("r-old", nulls, {}, RULE)
    assert "None" not in text and fileformat.parse(text, RULE).title == "T"


# -- the rules-to-events migration ---------------------------------------------------------

ID = "rules-to-events"


def _put(repo: Path, rid: str, text: str) -> Path:
    path = repo / ".ddflow" / "rules" / f"{rid}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _defs(repo: Path):
    return fold(EventLog(repo, "importer").read_all(), strict=False).defs


def _run(repo: Path):
    return M.run(repo, EventLog(repo, "importer"), Config.load(repo), [ID])


def test_a_rule_file_is_detected_with_its_file(repo: Path) -> None:
    _put(repo, "r-naming", RICH)
    ctx = M.context(repo, EventLog(repo, "importer"), Config.load(repo))
    (got,) = [p for p in M.pending(ctx) if p.migration.id == ID]
    assert [(f.key, f.path) for f in got.findings] == [
        ("record:r-naming", ".ddflow/rules/r-naming.toml")
    ]
    assert got.changes == [], "only the log is appended to: no file for a backup to hold"


def test_importing_records_each_file_with_imported_provenance_and_is_idempotent(repo: Path) -> None:
    path = _put(repo, "r-naming", RICH)
    _put(repo, "r-small", MINIMAL)
    before = path.read_bytes()
    out = _run(repo)
    assert [(o.migration, o.status, o.findings) for o in out] == [(ID, "applied", 2)], out
    rec = _defs(repo)["rule:r-naming"]
    cfg = Config.load(repo)
    assert rec.live and rec.fields == DF.logged(_fields(RICH), cfg)
    assert rec.digest == DF.digest_of(_fields(RICH), cfg)
    assert rec.source == ".ddflow/rules/r-naming.toml"
    assert rec.provenance["via"] == "import" and rec.provenance["created"] == "2026-10-03T12:00:00"
    assert sorted(k for k in _defs(repo) if k.startswith("rule:")) == [
        "rule:r-naming",
        "rule:r-small",
    ]
    assert path.read_bytes() == before, "the file is not touched"
    assert _run(repo) == [], "a second run finds nothing"


def test_a_file_edited_since_is_recorded_as_an_update(repo: Path) -> None:
    path = _put(repo, "r-naming", RICH)
    _run(repo)
    path.write_text(RICH.replace("snake_case", "camelCase"))
    (o,) = _run(repo)
    assert o.status == "applied"
    rec = _defs(repo)["rule:r-naming"]
    assert "camelCase" in rec.fields["body"]
    assert [h["event"] for h in rec.history] == ["def.recorded", "def.updated"]
    assert rec.provenance["via"] == "import"
    assert _run(repo) == []


def test_a_timestamp_only_change_is_no_edit(repo: Path) -> None:
    path = _put(repo, "r-naming", RICH)
    _run(repo)
    path.write_text(
        RICH.replace('updated = "2026-10-04T08:30:00"', 'updated = "2027-01-01T00:00:00"')
    )
    assert _run(repo) == []


def test_a_retired_rule_is_left_alone(repo: Path) -> None:
    path = _put(repo, "r-naming", RICH)
    _run(repo)
    assert ADEFS.def_retire(repo, "rule", "r-naming", reason="obsolete", agent="t").exit == 0
    path.write_text(RICH.replace("snake_case", "camelCase"))
    assert _run(repo) == []
    assert _defs(repo)["rule:r-naming"].status == "retired"


def test_a_file_that_does_not_load_is_not_imported(repo: Path) -> None:
    _put(repo, "r-broken", "this is not a rule file")
    _put(repo, "r-small", MINIMAL)
    (o,) = _run(repo)
    assert o.findings == 1 and sorted(k for k in _defs(repo) if k.startswith("rule:")) == [
        "rule:r-small"
    ]


def test_a_file_whose_id_differs_from_its_name_is_imported_under_the_name(repo: Path) -> None:
    _put(repo, "r-x", MINIMAL.replace("r-small", "r-y"))
    (o,) = _run(repo)
    assert o.status == "applied" and sorted(k for k in _defs(repo) if k.startswith("rule:")) == [
        "rule:r-x"
    ]
    assert _run(repo) == []


def test_detecting_does_not_create_the_rules_directory(repo: Path) -> None:
    rules = repo / ".ddflow" / "rules"
    assert not rules.exists()
    ctx = M.context(repo, EventLog(repo, "importer"), Config.load(repo))
    assert [p for p in M.pending(ctx) if p.migration.id == ID] == []
    assert not rules.exists()


# -- the write path: rule add / update / remove record the definition ---------------------

from ddflow.api import rules as ARULES  # noqa: E402
from ddflow.services.rules import Rule, RulesStorage  # noqa: E402


def _rule(rid: str = "r-w", content: str = "Always run the tests.") -> Rule:
    return Rule(id=rid, title="Run tests", content=content, tags=["ci"], priority=60)


def _events(repo: Path, kind: str):
    return [e for e in EventLog(repo, "reader").read_all() if e.kind == kind]


def test_adding_a_rule_records_the_definition_the_file_is_a_view_of(repo: Path) -> None:
    out = ARULES.rule_add(repo, _rule(), agent="t")
    assert out.exit == 0, out
    rec = _defs(repo)["rule:r-w"]
    cfg = Config.load(repo)
    on_disk = RulesStorage(repo).get("r-w").to_record()
    assert rec.live and rec.fields == DF.logged(DF.to_fields(on_disk, RULE), cfg)
    assert rec.digest == DF.digest_of(DF.to_fields(on_disk, RULE), cfg)
    assert rec.source == ".ddflow/rules/r-w.toml"
    assert rec.provenance["via"] == "write" and rec.provenance["by"]
    assert "unrecorded" not in out.data
    assert _run(repo) == [], "the file and the log already agree: nothing for the import"


def test_updating_a_rule_appends_an_update_and_a_no_change_appends_nothing(repo: Path) -> None:
    ARULES.rule_add(repo, _rule(), agent="t")
    out = ARULES.rule_update(repo, "r-w", priority=80, agent="t")
    assert out.exit == 0, out
    rec = _defs(repo)["rule:r-w"]
    assert rec.fields["priority"] == 80
    assert [h["event"] for h in rec.history] == ["def.recorded", "def.updated"]
    before = len(_events(repo, "def.updated"))
    again = ARULES.rule_update(repo, "r-w", priority=80, agent="t")
    assert again.exit == 0 and len(_events(repo, "def.updated")) == before
    assert _run(repo) == []


def test_extending_a_rule_records_the_longer_text(repo: Path) -> None:
    ARULES.rule_add(repo, _rule(), agent="t")
    out = ARULES._extend_rule(repo, "r-w", "And lint.", agent="t")
    assert out.exit == 0, out
    assert "And lint." in _defs(repo)["rule:r-w"].fields["body"]
    assert _run(repo) == []


def test_removing_a_rule_retires_it_and_adding_it_again_brings_it_back(repo: Path) -> None:
    ARULES.rule_add(repo, _rule(), agent="t")
    assert ARULES.rule_remove(repo, "r-w", agent="t").exit == 0
    assert _defs(repo)["rule:r-w"].status == "retired"
    assert not (repo / ".ddflow" / "rules" / "r-w.toml").exists()
    assert ARULES.rule_add(repo, _rule(content="Run the tests, always."), agent="t").exit == 0
    rec = _defs(repo)["rule:r-w"]
    assert rec.live and "always" in rec.fields["body"]
    assert _run(repo) == []


def test_removing_a_rule_the_log_never_recorded_writes_nothing(repo: Path) -> None:
    RulesStorage(repo).add(_rule())  # a file from before rules were events
    before = len(EventLog(repo, "reader").read_all())
    assert ARULES.rule_remove(repo, "r-w", agent="t").exit == 0
    assert "rule:r-w" not in _defs(repo)
    assert len(_events(repo, "def.retired")) == 0
    assert len(EventLog(repo, "reader").read_all()) >= before  # the log only ever grows


def test_a_log_that_cannot_record_leaves_the_file_and_says_so(repo: Path, monkeypatch) -> None:
    def refuse(*a, **k):
        raise RuntimeError("log is locked")

    monkeypatch.setattr(ARULES, "def_record_unchecked", refuse)
    out = ARULES.rule_add(repo, _rule(), agent="t")
    assert out.exit == 0 and (repo / ".ddflow" / "rules" / "r-w.toml").exists()
    assert (
        "not recorded in the log" in out.data["unrecorded"]
        and "log is locked" in out.data["unrecorded"]
    )
    monkeypatch.undo()
    # the one-time import records it later
    assert [o.status for o in _run(repo)] == ["applied"]
    assert _defs(repo)["rule:r-w"].live
