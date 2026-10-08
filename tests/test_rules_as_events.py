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
    assert ARULES.rule_remove(repo, "r-w", agent="t").exit == 0
    assert "rule:r-w" not in _defs(repo)
    assert len(_events(repo, "def.retired")) == 0


def test_a_log_that_cannot_record_leaves_the_file_and_says_so(repo: Path, monkeypatch) -> None:
    def refuse(*a, **k):
        raise RuntimeError("log is locked")

    monkeypatch.setattr(ARULES, "def_record_unchecked", refuse)
    out = ARULES.rule_add(repo, _rule(), agent="t")
    # a failure, so no surface shows it as a success; the file is there as the caller asked
    assert out.exit == 1 and (repo / ".ddflow" / "rules" / "r-w.toml").exists()
    assert "not recorded in the log" in out.reason and "log is locked" in out.reason
    assert "ddflow upgrade" in out.reason
    monkeypatch.undo()
    # the one-time import records it later
    assert [o.status for o in _run(repo)] == ["applied"]
    assert _defs(repo)["rule:r-w"].live


def test_a_removal_the_log_cannot_retire_says_the_log_still_holds_the_rule(
    repo: Path, monkeypatch
) -> None:
    ARULES.rule_add(repo, _rule(), agent="t")

    def refuse(*a, **k):
        raise RuntimeError("log is locked")

    monkeypatch.setattr(ARULES, "def_retire", refuse)
    out = ARULES.rule_remove(repo, "r-w", agent="t")
    assert out.exit == 1 and "file was removed but the log still holds the rule" in out.reason
    assert "log is locked" in out.reason
    assert not (repo / ".ddflow" / "rules" / "r-w.toml").exists()
    assert _defs(repo)["rule:r-w"].live


def test_the_mcp_tools_show_an_unrecorded_write_as_an_error(repo: Path, monkeypatch) -> None:
    from ddflow.surfaces.mcp import Server

    def call(name: str, args: dict):
        return Server(repo).handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
        )["result"]

    def refuse(*a, **k):
        raise RuntimeError("log is locked")

    monkeypatch.setattr(ARULES, "def_record_unchecked", refuse)
    res = call("ddflow_rule_add", {"id": "r-mcp", "title": "T", "content": "Body of the rule."})
    assert res.get("isError") and res["_meta"]["exit"] == 1, res
    text = res["content"][0]["text"]
    assert "not recorded in the log" in text and "log is locked" in text
    assert (repo / ".ddflow" / "rules" / "r-mcp.toml").exists()


# -- the rule files as a checked generated view -------------------------------------------

import subprocess  # noqa: E402

from ddflow.services import enforce as E  # noqa: E402
from ddflow.services.guidance import ruleview as RV  # noqa: E402


def _state(repo: Path):
    return fold(EventLog(repo, "reader").read_all(), strict=False)


def _drift(repo: Path):
    return RV.drift(repo, Config.load(repo), _state(repo))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_files_the_log_says_have_no_drift(repo: Path) -> None:
    ARULES.rule_add(repo, _rule(), agent="t")
    assert _drift(repo) == []
    assert RV.notes(repo, Config.load(repo), _state(repo)) == []


def test_drift_names_an_unrecorded_file_an_edit_and_a_missing_file(repo: Path) -> None:
    ARULES.rule_add(repo, _rule("r-a"), agent="t")
    ARULES.rule_add(
        repo,
        Rule(id="r-b", title="Name modules plainly", content="Modules take short nouns."),
        agent="t",
    )
    _put(repo, "r-c", MINIMAL)
    edited = repo / ".ddflow" / "rules" / "r-a.toml"
    edited.write_text(edited.read_text().replace("Always run", "Never skip"))
    (repo / ".ddflow" / "rules" / "r-b.toml").unlink()
    assert [(d.id, d.how) for d in _drift(repo)] == [
        ("r-a", "edited"),
        ("r-b", "missing"),
        ("r-c", "unrecorded"),
    ]
    (note,) = RV.notes(repo, Config.load(repo), _state(repo))
    assert "3 rule file(s)" in note and "ddflow rule sync" in note


def test_doctor_notes_a_hand_edited_rule_file(repo: Path) -> None:
    from ddflow.api.reporting import doctor

    ARULES.rule_add(repo, _rule(), agent="t")
    path = repo / ".ddflow" / "rules" / "r-w.toml"
    path.write_text(path.read_text().replace("Always run", "Never skip"))
    out = doctor(repo, agent="t")
    assert any("rule file(s) disagree with the log" in n for n in out.data.get("notes", [])), (
        out.data
    )


def test_sync_records_a_hand_edit_a_new_file_and_restores_a_deleted_one(repo: Path) -> None:
    ARULES.rule_add(repo, _rule("r-a"), agent="t")
    ARULES.rule_add(
        repo,
        Rule(id="r-b", title="Name modules plainly", content="Modules take short nouns."),
        agent="t",
    )
    a = repo / ".ddflow" / "rules" / "r-a.toml"
    a.write_text(a.read_text().replace("Always run", "Never skip"))
    _put(repo, "r-c", MINIMAL)
    gone = repo / ".ddflow" / "rules" / "r-b.toml"
    want = gone.read_text()
    gone.unlink()
    out = ARULES.rule_sync(repo, agent="t")
    assert out.exit == 0, out
    assert (out.data["recorded"], out.data["updated"], out.data["restored"]) == (
        ["r-c"],
        ["r-a"],
        ["r-b"],
    )
    defs = _defs(repo)
    assert (
        "Never skip" in defs["rule:r-a"].fields["body"]
        and defs["rule:r-a"].provenance["via"] == "sync"
    )
    assert defs["rule:r-c"].live
    assert fileformat.parse(gone.read_text(), RULE).body == fileformat.parse(want, RULE).body
    assert _drift(repo) == []
    again = ARULES.rule_sync(repo, agent="t")
    assert again.exit == 0 and not any(again.data[k] for k in ("recorded", "updated", "restored"))


def test_sync_leaves_a_retired_rules_file_alone(repo: Path) -> None:
    ARULES.rule_add(repo, _rule(), agent="t")
    assert ADEFS.def_retire(repo, "rule", "r-w", reason="obsolete", agent="t").exit == 0
    out = ARULES.rule_sync(repo, agent="t")
    assert (
        out.exit == 0 and out.data["restored"] == [] and _defs(repo)["rule:r-w"].status == "retired"
    )


def test_the_sync_command_and_tool_run_the_api(repo: Path) -> None:
    from conftest import run_cli

    _put(repo, "r-c", MINIMAL)
    code, out, err = run_cli(repo, "--json", "rule", "sync")
    assert code == 0, (out, err)
    assert json.loads(out)["recorded"] == ["r-c"]
    code, out, err = run_cli(repo, "rule", "sync")
    assert code == 0 and "agree" in out, (out, err)


def test_a_staged_hand_edit_is_refused_until_it_is_recorded(repo: Path) -> None:
    ARULES.rule_add(repo, _rule(), agent="t")
    _git(repo, "add", "-A")
    assert E.check_views(repo, Config.load(repo)) == (0, "")
    path = repo / ".ddflow" / "rules" / "r-w.toml"
    path.write_text(path.read_text().replace("Always run", "Never skip"))
    _git(repo, "add", "-A")
    code, msg = E.check_views(repo, Config.load(repo))
    assert code == 1 and "r-w.toml" in msg and "ddflow rule sync" in msg, msg
    assert ARULES.rule_sync(repo, agent="t").exit == 0
    _git(repo, "add", "-A")
    assert E.check_views(repo, Config.load(repo)) == (0, "")


def test_a_staged_rule_file_the_log_never_recorded_is_refused(repo: Path) -> None:
    _put(repo, "r-c", MINIMAL)
    _git(repo, "add", "-A")
    code, msg = E.check_views(repo, Config.load(repo))
    assert code == 1 and "r-c.toml" in msg and "no record" in msg, msg


def test_replay_rebuilds_the_rule_files_from_the_log(repo: Path, tmp_path: Path) -> None:
    from ddflow.api.reporting import replay

    ARULES.rule_add(repo, _rule(), agent="t")
    out = replay(repo, out_dir=str(tmp_path / "kit"))
    rebuilt = tmp_path / "kit" / "rules" / "r-w.toml"
    assert out.exit == 0 and rebuilt.is_file()
    want = fileformat.parse((repo / ".ddflow" / "rules" / "r-w.toml").read_text(), RULE)
    got = fileformat.parse(rebuilt.read_text(), RULE)
    assert DF.to_fields(got, RULE) == DF.to_fields(want, RULE)


def test_a_staged_rule_file_without_its_log_events_is_refused(repo: Path) -> None:
    """In a project that commits its log, the check compares with the log only when the log is
    committed beside the file: a rule file staged alone (its record unstaged) is refused,
    never judged by the working log."""
    ARULES.rule_add(repo, _rule("r-a"), agent="t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base", "--no-verify")
    ARULES.rule_add(
        repo,
        Rule(id="r-b", title="Name modules plainly", content="Modules take short nouns."),
        agent="t",
    )
    _git(repo, "add", ".ddflow/rules")
    code, msg = E.check_views(repo, Config.load(repo))
    assert code == 1 and "the commit does not record" in msg, msg


def test_a_rule_file_that_cannot_be_written_back_is_a_failure_not_a_traceback(
    repo: Path, monkeypatch
) -> None:
    ARULES.rule_add(repo, _rule(), agent="t")
    (repo / ".ddflow" / "rules" / "r-w.toml").unlink()

    def boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(RV.files(repo).__class__, "write", boom)
    out = ARULES.rule_sync(repo, agent="t")
    assert out.exit == 1 and "read-only file system" in out.reason, out
