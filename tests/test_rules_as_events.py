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
from ddflow.services.guidance import deffields as DF
from ddflow.services.guidance import fileformat
from ddflow.services.guidance.kinds import DECISION, RULE

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
    assert DF.digest_of(secret, cfg) != DF.digest_of(_fields(RICH), cfg)
