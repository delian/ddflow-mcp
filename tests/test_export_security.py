"""The ``security`` export kind (B-export-security-md): SECURITY.md from recorded bugs and
version tags, in named regions, with the operator's text left alone."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ddflow import api
from ddflow.config import EXPORT_DEFAULT_TARGETS, Config
from ddflow.config_sections.export import EXPORT_DEFAULT_MODES
from ddflow.core.events import Event
from ddflow.services.export import ExportError, Filters, ops, query, refresh, registry
from ddflow.services.export import kind_security as ks
from ddflow.services.export import regions as RG

ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(repo: Path, *args: str, date: str = "2026-09-01T12:00:00+00:00") -> str:
    env = {**os.environ, **ENV, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date}
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def commit(repo: Path, name: str, date: str) -> str:
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-m", f"add {name}", date=date)
    return git(repo, "rev-parse", "HEAD")


def tag(repo: Path, name: str, date: str) -> None:
    git(repo, "tag", "-a", name, "-m", name, date=date)


def ev(name: str, subject: str, ts: str, **data) -> Event:
    return Event(kind=name, subject=subject, data=data, ts=ts, agent="a", lamport=0)


def bug(bid: str, found: str, *, tags=("security",), severity="high", **extra) -> Event:
    summary = extra.pop("summary", f"summary of {bid}")
    return ev("bug.found", bid, found, summary=summary, tags=list(tags), severity=severity, **extra)


def fix(bid: str, at: str) -> Event:
    return ev("bug.fixed", bid, at, regression_test="tests/t.py::t")


def task(tid: str, at: str, sha: str = "", tags=()) -> list[Event]:
    out = [ev("task.added", tid, "2026-09-01T00:00:00Z", title=tid, kind="task", tags=list(tags))]
    return [*out, ev("item.completed", tid, at, sha=sha)] if sha else out


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """v1.0.0 (09-10), v1.1.0 (09-20), v2.0.0 (10-01); one commit after each."""
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".ddflow" / "events").mkdir(parents=True)
    tmp_path_shas = {"a": commit(tmp_path, "a", "2026-09-05T10:00:00+00:00")}
    tag(tmp_path, "v1.0.0", "2026-09-10T12:00:00+00:00")
    tmp_path_shas["b"] = commit(tmp_path, "b", "2026-09-15T10:00:00+00:00")
    tag(tmp_path, "v1.1.0", "2026-09-20T12:00:00+00:00")
    tmp_path_shas["c"] = commit(tmp_path, "c", "2026-09-25T10:00:00+00:00")
    tag(tmp_path, "v2.0.0", "2026-10-01T12:00:00+00:00")
    tmp_path_shas["d"] = commit(tmp_path, "d", "2026-10-05T10:00:00+00:00")
    (tmp_path / "shas").write_text(" ".join(f"{k}={v}" for k, v in tmp_path_shas.items()))
    return tmp_path


def sha(repo: Path, name: str) -> str:
    return dict(p.split("=") for p in (repo / "shas").read_text().split())[name]


def config(repo: Path, toml: str) -> None:
    p = repo / ".ddflow" / "config.toml"
    p.write_text((p.read_text() if p.exists() else "") + "\n" + toml)


def load(repo: Path, events: list[Event]) -> query.Query:
    q = query.build(events)
    q.repo = repo
    return q


def collect(repo: Path, events: list[Event]) -> dict:
    return ks.collect(load(repo, events))


def lines_of(data: dict) -> dict[str, dict]:
    return {v["line"]: v for v in data["versions"]}


# -- registration ----------------------------------------------------------------------


def test_the_kind_is_registered_as_a_region_document():
    k = registry.get("security")
    assert k.default_target == "SECURITY.md" == EXPORT_DEFAULT_TARGETS["security"]
    assert k.update_mode == registry.REGION


def test_the_default_modes_agree_with_the_kinds():
    for name in registry.names():
        assert EXPORT_DEFAULT_MODES.get(name, "whole") == registry.get(name).update_mode, name


def test_a_selected_security_target_is_a_region_not_a_generated_file():
    # every core reader of `targets()` treats "whole" as "ddflow owns the file"
    cfg = Config()
    cfg.export.documents = ["security"]
    assert cfg.export.targets() == [("security", "SECURITY.md", "region")]


# -- supported versions ----------------------------------------------------------------


def test_newest_line_is_supported_and_older_ones_are_unsupported_by_default(repo):
    data = collect(repo, [])
    assert [v["line"] for v in data["versions"]] == ["2.0", "1.1", "1.0"]
    assert [v["status"] for v in data["versions"]] == ["supported", "unsupported", "unsupported"]
    assert data["versions"][0]["latest"] == "2.0.0"
    assert data["versions"][0]["released"] == "2026-10-01"


def test_supported_and_deprecated_lines_come_from_the_table(repo):
    config(repo, '[export.security]\nsupported_lines = ["1.1"]\ndeprecated_lines = ["1.0"]\n')
    assert [v["status"] for v in collect(repo, [])["versions"]] == [
        "supported",
        "supported",
        "deprecated",
    ]


def test_a_line_name_may_be_a_whole_major(repo):
    config(repo, '[export.security]\nsupported_lines = ["1"]\n')
    assert [v["status"] for v in collect(repo, [])["versions"]] == ["supported"] * 3


def test_a_maintenance_line_of_flow_lines_counts_as_supported(repo):
    config(repo, '[flow.lines]\n"1" = "maint/1.x"\n')
    assert [v["status"] for v in collect(repo, [])["versions"]] == ["supported"] * 3


def test_deprecating_the_newest_line_wins_over_the_default(repo):
    config(repo, '[export.security]\ndeprecated_lines = ["2.0"]\n')
    assert collect(repo, [])["versions"][0]["status"] == "deprecated"


def test_no_version_tag_says_so_instead_of_inventing_a_version(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".ddflow" / "events").mkdir(parents=True)
    data = collect(tmp_path, [])
    assert data["versions"] == []
    body = registry.render_body("security", load(tmp_path, []), Filters())
    assert "No version has been released yet" in body


# -- known issues ----------------------------------------------------------------------


def test_no_security_bug_is_stated_as_none_recorded(repo):
    plain = bug("B1", "2026-09-02T00:00:00Z", tags=())
    data = collect(repo, [plain])
    assert data["open"] == [] and data["fixed"] == []
    body = registry.render_body("security", load(repo, [plain]), Filters())
    assert "None recorded." in body and "B1" not in body
    assert "These are the bugs" not in body  # there are none to point at


def test_tags_on_the_bug_or_its_fix_task_select_it_and_invalid_never_lists(repo):
    events = [
        bug("B1", "2026-09-02T00:00:00Z"),
        bug("B2", "2026-09-03T00:00:00Z", tags=("secbug",)),
        *task("fix-B3", "", tags=("security",)),
        bug("B3", "2026-09-04T00:00:00Z", tags=(), fix_task="fix-B3"),
        bug("B4", "2026-09-05T00:00:00Z"),
        ev("bug.invalid", "B4", "2026-09-06T00:00:00Z", reason="not a bug"),
        bug("B5", "2026-09-07T00:00:00Z", tags=("perf",)),
    ]
    assert [r["id"] for r in collect(repo, events)["open"]] == ["B1", "B2", "B3"]


def test_the_tags_that_mean_security_are_configurable(repo):
    config(repo, '[export.security]\ntags = ["vuln"]\n')
    events = [
        bug("B1", "2026-09-02T00:00:00Z"),
        bug("B2", "2026-09-03T00:00:00Z", tags=("vuln",)),
    ]
    assert [r["id"] for r in collect(repo, events)["open"]] == ["B2"]


def test_an_empty_tag_list_counts_nothing(repo):
    config(repo, "[export.security]\ntags = []\n")
    assert collect(repo, [bug("B1", "2026-09-02T00:00:00Z")])["open"] == []


def test_a_fixed_bug_names_the_oldest_version_holding_its_landing(repo):
    events = [
        *task("fix-B1", "2026-09-16T00:00:00Z", sha=sha(repo, "b")),
        bug("B1", "2026-09-12T00:00:00Z", fix_task="fix-B1"),
        fix("B1", "2026-09-16T00:00:00Z"),
    ]
    (row,) = collect(repo, events)["fixed"]
    assert (row["fixed_in"], row["by_date"]) == ("1.1.0", False)


def test_a_fix_landed_after_the_last_tag_is_unreleased(repo):
    events = [
        *task("fix-B1", "2026-10-06T00:00:00Z", sha=sha(repo, "d")),
        bug("B1", "2026-10-02T00:00:00Z", fix_task="fix-B1"),
        fix("B1", "2026-10-06T00:00:00Z"),
    ]
    assert collect(repo, events)["fixed"][0]["fixed_in"] == ks.UNRELEASED


def test_a_fix_with_no_landing_is_placed_by_date_and_says_so(repo):
    events = [bug("B1", "2026-09-12T00:00:00Z"), fix("B1", "2026-09-16T00:00:00Z")]
    (row,) = collect(repo, events)["fixed"]
    assert (row["fixed_in"], row["by_date"]) == ("1.1.0", True)
    body = registry.render_body("security", load(repo, events), Filters())
    assert "1.1.0 (by date)" in body


# -- vulnerable marks ------------------------------------------------------------------


def test_an_open_bug_marks_the_newest_release_and_those_tagged_after_it_was_found(repo):
    events = [bug("B1", "2026-09-15T00:00:00Z")]
    rows = lines_of(collect(repo, events))
    assert rows["2.0"]["vulnerable"] == ["vulnerable: B1, not fixed yet"]
    assert rows["1.1"]["vulnerable"] == ["vulnerable: B1, not fixed yet"]
    assert rows["1.0"]["vulnerable"] == []  # tagged before it was found: nothing is known


def test_an_open_bug_found_after_every_release_still_marks_the_newest(repo):
    rows = lines_of(collect(repo, [bug("B1", "2026-10-10T00:00:00Z")]))
    assert rows["2.0"]["vulnerable"] and not rows["1.1"]["vulnerable"]


def test_a_shipped_fix_clears_the_versions_holding_it(repo):
    events = [
        *task("fix-B1", "2026-09-26T00:00:00Z", sha=sha(repo, "c")),
        bug("B1", "2026-09-15T00:00:00Z", fix_task="fix-B1"),
        fix("B1", "2026-09-26T00:00:00Z"),
    ]
    rows = lines_of(collect(repo, events))
    assert rows["2.0"]["vulnerable"] == []
    assert rows["1.1"]["vulnerable"] == ["vulnerable: B1, fixed in 2.0.0"]


def test_a_fix_on_a_maintenance_line_clears_that_line_by_ancestry(repo):
    # a fix reaching 1.1.1 only: the newer 2.0.0 does not hold it and stays marked
    git(repo, "checkout", "-q", "-b", "maint", "v1.1.0")
    landed = commit(repo, "m", "2026-10-02T10:00:00+00:00")
    tag(repo, "v1.1.1", "2026-10-03T12:00:00+00:00")
    git(repo, "checkout", "-q", "main")
    events = [
        *task("fix-B1", "2026-10-02T12:00:00Z", sha=landed),
        bug("B1", "2026-09-15T00:00:00Z", fix_task="fix-B1"),
        fix("B1", "2026-10-02T12:00:00Z"),
    ]
    rows = lines_of(collect(repo, events))
    assert rows["1.1"]["vulnerable"] == []
    assert rows["2.0"]["vulnerable"] == ["vulnerable: B1, fixed in 1.1.1"]
    # 1.1.1 was tagged after 2.0.0, and the highest version is still the current line
    assert (rows["2.0"]["status"], rows["1.1"]["status"]) == ("supported", "unsupported")


def test_a_date_placed_fix_after_every_tag_is_for_the_next_release(repo):
    events = [bug("B1", "2026-09-15T00:00:00Z"), fix("B1", "2026-10-20T00:00:00Z")]
    data = collect(repo, events)
    assert (data["fixed"][0]["fixed_in"], data["fixed"][0]["by_date"]) == (ks.UNRELEASED, True)
    assert lines_of(data)["2.0"]["vulnerable"] == [f"vulnerable: B1, fixed in {ks.UNRELEASED}"]


def test_a_date_placed_fix_never_clears_a_release_tagged_before_it(repo):
    # 1.1.1 sorts below 2.0.0 and was tagged after it: version order is not date order
    git(repo, "checkout", "-q", "-b", "maint", "v1.1.0")
    commit(repo, "m", "2026-10-02T10:00:00+00:00")
    tag(repo, "v1.1.1", "2026-10-03T12:00:00+00:00")
    git(repo, "checkout", "-q", "main")
    events = [bug("B1", "2026-09-15T00:00:00Z"), fix("B1", "2026-10-02T12:00:00Z")]
    data = collect(repo, events)
    rows = lines_of(data)
    assert rows["1.1"]["vulnerable"] == []
    assert rows["2.0"]["vulnerable"] == ["vulnerable: B1, fixed in 1.1.1"]
    assert (data["fixed"][0]["fixed_in"], data["fixed"][0]["by_date"]) == ("1.1.1", True)


# -- disclosure ------------------------------------------------------------------------

SECRET = "the parser runs attacker input through eval"


def _disclosure(repo: Path) -> list[Event]:
    return [
        bug("B1", "2026-09-12T00:00:00Z", title="open detail", summary=SECRET),
        bug("B2", "2026-09-12T00:00:00Z", title="fixed detail", summary=SECRET),
        fix("B2", "2026-09-16T00:00:00Z"),
    ]


def test_by_default_only_ids_and_severity_are_published(repo):
    body = registry.render_body("security", load(repo, _disclosure(repo)), Filters())
    assert "B1" in body and "B2" in body and "high" in body
    assert "detail" not in body and SECRET not in body


def test_disclose_fixed_titles_the_fixed_ones_and_never_the_open_ones(repo):
    config(repo, '[export.security]\ndisclose = "fixed"\n')
    body = registry.render_body("security", load(repo, _disclosure(repo)), Filters())
    assert "fixed detail" in body and "open detail" not in body and SECRET not in body


def test_disclose_all_is_the_operators_say_so_and_is_the_only_way_to_title_an_open_bug(repo):
    config(repo, '[export.security]\ndisclose = "all"\n')
    body = registry.render_body("security", load(repo, _disclosure(repo)), Filters())
    assert "open detail" in body and "fixed detail" in body


def test_bad_table_values_are_refused():
    for table, word in (
        ({"disclose": "everything"}, "disclose"),
        ({"supported_lines": "1"}, "supported_lines"),
        ({"deprecated_lines": [1]}, "deprecated_lines"),
        ({"tags": "security"}, "tags"),
    ):
        with pytest.raises(ValueError, match=word):
            Config.check({"export": {"security": table}})
    Config.check({"export": {"security": {"disclose": "ids", "supported_lines": ["1"]}}})


def test_the_new_keys_belong_to_the_security_table_only():
    with pytest.raises(ValueError, match="unknown key"):
        Config.check({"export": {"roadmap": {"disclose": "ids"}}})


def test_a_repository_is_required(repo):
    with pytest.raises(ExportError):
        ks.collect(query.build([]))


def test_two_renders_are_the_same_bytes_and_the_log_moving_changes_nothing(repo):
    # no "as of": a document that went stale on every event would churn on every merge
    a = registry.render_body("security", load(repo, _disclosure(repo)), Filters())
    moved = [*_disclosure(repo), ev("task.added", "T9", "2026-10-30T00:00:00Z", title="x")]
    assert a == registry.render_body("security", load(repo, moved), Filters())


# -- the file --------------------------------------------------------------------------


def _spec(cfg: Config, **kw) -> ops.Spec:
    return ops.spec_for(cfg, "security", writing=True, **kw)


def _write(repo: Path, events: list[Event], **kw):
    cfg = Config.load(repo)
    return ops.write_doc(repo, cfg, load(repo, events), _spec(cfg), **kw)


def test_a_new_file_is_seeded_with_a_todo_and_never_a_contact(repo):
    assert _write(repo, []).action == "created"
    text = (repo / "SECURITY.md").read_text()
    assert "## Reporting a Vulnerability" in text and "TODO" in text
    assert "@" not in text.split("## Reporting a Vulnerability")[1]
    assert {r.name for r in RG.parse(text)} == {"versions", "known-issues"}
    reporting = text.index("## Reporting a Vulnerability")
    assert all(not (r.begin <= reporting < r.stop) for r in RG.parse(text))


def test_a_refresh_keeps_the_operators_text_byte_for_byte(repo):
    _write(repo, [])
    p = repo / "SECURITY.md"
    mine = p.read_text().replace("TODO", "Mail security@example.org;")
    mine = "Intro by hand.\n\n" + mine + "\nOutro by hand.\n"
    p.write_text(mine)
    assert _write(repo, [bug("B1", "2026-09-15T00:00:00Z")]).action == "updated"
    new = p.read_text()
    assert "B1" in new
    assert new.startswith("Intro by hand.\n\n") and new.endswith("\nOutro by hand.\n")
    assert "Mail security@example.org;" in new


def test_a_file_with_no_markers_is_refused_until_force_appends_the_regions(repo):
    p = repo / "SECURITY.md"
    p.write_text("# Ours\n\nMail us.")
    cfg = Config.load(repo)
    assert ops.state_of(repo, cfg, load(repo, []), _spec(cfg))[0] == "hand-edited"
    with pytest.raises(ExportError, match="--force"):
        _write(repo, [])
    assert _write(repo, [], check=True).code == 1  # never "fresh": none of it is in the file
    assert p.read_text() == "# Ours\n\nMail us."
    res = _write(repo, [], force=True)
    text = p.read_text()
    assert res.action == "updated" and text.startswith("# Ours\n\nMail us.\n\n<!-- ddflow:begin")
    assert [r.name for r in RG.parse(text)] == ["versions", "known-issues"]
    assert "-->\n\n<!-- ddflow:begin" in text  # the regions keep a blank line between them
    assert not any("not inserted" in n for n in res.notes)
    assert "Reporting" not in text  # the operator's file has its own policy text
    assert ops.state_of(repo, cfg, load(repo, []), _spec(cfg))[0] == "fresh"


def test_force_in_a_crlf_file_keeps_its_line_ends(repo):
    p = repo / "SECURITY.md"
    p.write_bytes(b"# Ours\r\n")
    _write(repo, [], force=True)
    raw = p.read_bytes()
    assert raw.startswith(b"# Ours\r\n\r\n<!-- ddflow:begin") and b"\n" not in raw.replace(
        b"\r\n", b""
    )
    assert not any(r.edited for r in RG.parse(raw.decode()))
    cfg = Config.load(repo)
    assert ops.state_of(repo, cfg, load(repo, []), _spec(cfg))[0] == "fresh"
    assert _write(repo, []).action == "unchanged"  # the next refresh needs no --force


def test_a_created_file_with_redacted_text_is_not_read_back_as_hand_edited(repo):
    # the markers carry the digest of the body: it must be the REDACTED body's
    config(repo, '[export.security]\ndisclose = "all"\n')
    leak = bug("B1", "2026-09-15T00:00:00Z", title="reach 10.1.2.3 from outside")
    assert _write(repo, [leak]).action == "created"
    text = (repo / "SECURITY.md").read_text()
    assert "10.1.2.3" not in text
    assert not any(r.edited for r in RG.parse(text))


def test_a_check_of_an_unmarked_file_never_says_it_appended(repo):
    (repo / "SECURITY.md").write_text("# Ours\n")
    notes = _write(repo, [], check=True).notes
    assert notes and all("not inserted" in n for n in notes)


def test_force_with_nothing_missing_adds_nothing_after_the_file(repo):
    _write(repo, [])
    p = repo / "SECURITY.md"
    p.write_text(p.read_text().replace("None recorded.", "Edited by hand."))
    _write(repo, [], force=True)
    text = p.read_text()
    assert "None recorded." in text and not text.endswith("\n\n")


def test_a_region_the_file_lacks_reaches_the_person_who_ran_the_update(repo):
    _write(repo, [])
    p = repo / "SECURITY.md"
    gone = next(r for r in RG.parse(p.read_text()) if r.name == "known-issues")
    p.write_text(p.read_text()[: gone.begin])
    out = api.export_documents(repo, "security", update=True)
    assert out.exit == 0 and "known-issues" in out.data["results"][0]["message"]


@pytest.mark.parametrize("where", [".github/SECURITY.md", "docs/SECURITY.md"])
def test_an_existing_policy_elsewhere_is_the_target(repo, where):
    p = repo / where
    p.parent.mkdir()
    p.write_text("# Theirs\n")
    cfg = Config.load(repo)
    spec = _spec(cfg)
    assert ops.target_of(repo, spec) == where
    res = ops.write_doc(repo, cfg, load(repo, []), spec, force=True)
    assert res.rel == where and not (repo / "SECURITY.md").exists()
    assert ops.state_of(repo, cfg, load(repo, []), spec)[0] == "fresh"


def test_an_unreadable_candidate_is_not_a_crash(repo):
    # a status listing must not die on a directory it may not look into
    (repo / ".github").mkdir()
    (repo / ".github").chmod(0)
    try:
        assert ops.target_of(repo, _spec(Config.load(repo))) == "SECURITY.md"
    finally:
        (repo / ".github").chmod(0o755)


def test_root_wins_and_a_configured_path_is_never_second_guessed(repo):
    (repo / ".github").mkdir()
    (repo / ".github" / "SECURITY.md").write_text("# Theirs\n")
    cfg = Config.load(repo)
    (repo / "SECURITY.md").write_text("# Root\n")
    assert ops.target_of(repo, _spec(cfg)) == "SECURITY.md"
    (repo / "SECURITY.md").unlink()
    config(repo, '[export.security]\npath = "SEC.md"\n')
    assert ops.target_of(repo, _spec(Config.load(repo))) == "SEC.md"


def test_the_listing_names_the_file_it_would_write(repo):
    (repo / ".github").mkdir()
    (repo / ".github" / "SECURITY.md").write_text("# Theirs\n")
    rows = {r["doc"]: r for r in ops.listing(repo, Config.load(repo))}
    assert rows["security"]["target"] == ".github/SECURITY.md"


def test_a_refresh_writes_the_regions_and_reports_the_real_file(repo):
    (repo / ".github").mkdir()
    (repo / ".github" / "SECURITY.md").write_text("# Theirs\n")
    _write_at = ".github/SECURITY.md"
    config(repo, '[export]\ndocuments = ["security"]\nrefresh = "merge"\n')
    cfg = Config.load(repo)
    ops.write_doc(repo, cfg, load(repo, []), _spec(cfg), force=True)
    (o,) = refresh.refresh_selected(repo, "merge", cfg=cfg).outcomes
    assert (o.action, o.path) == ("unchanged", _write_at) and o.digest


def test_the_command_prints_and_writes_it(repo):
    out = api.export_documents(repo, "security")
    assert out.exit == 0 and "## Supported Versions" in out.data["results"][0]["text"]
    assert not (repo / "SECURITY.md").exists()
    assert api.export_documents(repo, "security", update=True).exit == 0
    assert (repo / "SECURITY.md").is_file()
    assert api.export_documents(repo, "security", check=True).exit == 0
