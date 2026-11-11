"""P-agent-lifecycle.manifest: the one shared call every onboarding writer records through.

Project paths reach the log as ONE `agent.onboarded {agent, artifacts[]}` event per run;
user-scope paths go to a machine-local file; re-recording a path and key updates the digest;
a read returns an agent's artifacts and which digests changed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ddflow.core.model import fold
from ddflow.services import installmanifest as IM


def _onboarded(log):
    return [e for e in log.read_all() if e.kind == "agent.onboarded"]


def test_a_run_of_project_artifacts_is_one_event(repo, log):
    with IM.recording(log, repo, "claude", version="0.2.9") as m:
        m.record("CLAUDE.md", "block", "ddflow:begin", "d1", descriptor="claude")
        m.record(".mcp.json", "json_key", "mcpServers.ddflow", "d2", descriptor="claude")
        m.record(".claude/agents/x.md", "subagent", "", "d3", descriptor="claude")
    evs = _onboarded(log)
    assert len(evs) == 1
    assert evs[0].data["agent"] == "claude"
    got = {(a["path"], a["key"]): a for a in evs[0].data["artifacts"]}
    assert set(got) == {
        ("CLAUDE.md", "ddflow:begin"),
        (".mcp.json", "mcpServers.ddflow"),
        (".claude/agents/x.md", ""),
    }
    a = got[("CLAUDE.md", "ddflow:begin")]
    assert (a["kind"], a["digest"], a["descriptor"], a["version"]) == (
        "block", "d1", "claude", "0.2.9",
    )  # fmt: skip


def test_an_unknown_artifact_kind_is_refused(repo, log):
    with pytest.raises(ValueError, match="artifact kind"):
        IM.record(log, repo, "claude", "a", "nonsense", "", "d", descriptor="c", version="1")


def test_re_recording_the_same_path_and_key_updates_the_digest(repo, log):
    IM.record(log, repo, "claude", "CLAUDE.md", "block", "b", "d1", descriptor="c", version="1")
    IM.record(log, repo, "claude", "CLAUDE.md", "block", "b", "d2", descriptor="c", version="2")
    rows = IM.artifacts(log, "claude")
    assert len(rows) == 1
    assert (rows[0]["digest"], rows[0]["version"]) == ("d2", "2")


def test_an_identical_re_record_appends_nothing(repo, log):
    args = ("claude", "CLAUDE.md", "block", "b", "d1")
    IM.record(log, repo, *args, descriptor="c", version="1")
    IM.record(log, repo, *args, descriptor="c", version="1")
    assert len(_onboarded(log)) == 1


def test_agents_do_not_share_artifacts(repo, log):
    IM.record(log, repo, "claude", "X.md", "block", "b", "d1", descriptor="c", version="1")
    IM.record(log, repo, "kilo", "X.md", "block", "b", "d9", descriptor="k", version="1")
    assert IM.artifacts(log, "claude")[0]["digest"] == "d1"
    assert IM.artifacts(log, "kilo")[0]["digest"] == "d9"


def test_user_scope_paths_go_to_the_machine_local_file_not_the_log(repo, log, tmp_path):
    outside = tmp_path / "home" / ".claude" / "settings.json"
    IM.record(log, repo, "claude", str(outside), "json_key", "hooks", "d1", descriptor="c", version="1")
    assert _onboarded(log) == []
    stored = json.loads((repo / IM.LOCAL_FILE).read_text())
    assert stored["claude"][0]["path"] == str(outside)
    assert IM.artifacts(log, "claude", repo=repo)[0]["digest"] == "d1"
    IM.record(log, repo, "claude", str(outside), "json_key", "hooks", "d2", descriptor="c", version="1")
    assert [a["digest"] for a in IM.artifacts(log, "claude", repo=repo)] == ["d2"]


def test_a_mixed_run_splits_by_scope(repo, log, tmp_path):
    outside = tmp_path / "elsewhere" / "cfg.toml"
    with IM.recording(log, repo, "claude", version="1") as m:
        m.record("a.md", "block", "k", "d1", descriptor="c")
        m.record(str(outside), "toml_key", "x", "d2", descriptor="c")
    assert [a["path"] for a in _onboarded(log)[0].data["artifacts"]] == ["a.md"]
    assert {a["path"] for a in IM.artifacts(log, "claude", repo=repo)} == {"a.md", str(outside)}


def test_a_run_that_raises_records_nothing(repo, log):
    with pytest.raises(RuntimeError):
        with IM.recording(log, repo, "claude", version="1") as m:
            m.record("a.md", "block", "k", "d1", descriptor="c")
            raise RuntimeError("boom")
    assert _onboarded(log) == []


def test_read_says_which_digests_changed(repo, log):
    (repo / "cmd.md").write_text("one\n")
    (repo / "keep.md").write_text("same\n")
    IM.record(log, repo, "claude", "cmd.md", "command", "", IM.file_digest(repo / "cmd.md"), descriptor="c", version="1")
    IM.record(log, repo, "claude", "keep.md", "command", "", IM.file_digest(repo / "keep.md"), descriptor="c", version="1")
    IM.record(log, repo, "claude", "gone.md", "command", "", "x", descriptor="c", version="1")
    IM.record(log, repo, "claude", "r.md", "block", "k", "x", descriptor="c", version="1")
    (repo / "cmd.md").write_text("two\n")
    report = IM.read(log, repo, "claude")
    assert {a["path"] for a in report.artifacts} == {"cmd.md", "keep.md", "gone.md", "r.md"}
    changed = {c["path"]: c["now"] for c in report.changed}
    assert changed["cmd.md"] == IM.file_digest(repo / "cmd.md")
    assert changed["gone.md"] is None
    assert "keep.md" not in changed
    assert "r.md" not in changed  # a block's digest needs the writer's resolver: unknown, not changed


def test_read_uses_a_writers_resolver_for_regions(repo, log):
    IM.record(log, repo, "claude", "r.md", "block", "k", "old", descriptor="c", version="1")
    report = IM.read(log, repo, "claude", current=lambda a: "new")
    assert [c["path"] for c in report.changed] == ["r.md"]
    assert IM.read(log, repo, "claude", current=lambda a: "old").changed == []


def test_the_fold_is_replayable(repo, log):
    IM.record(log, repo, "claude", "a.md", "block", "k", "d1", descriptor="c", version="1")
    st = fold(log.read_all())
    assert st.onboarded["claude"]
    assert isinstance(Path(repo), Path)


def test_a_resolver_that_does_not_know_an_artifact_leaves_whole_files_to_read(repo, log):
    (repo / "p.md").write_text("one\n")
    IM.record(log, repo, "claude", "p.md", "plugin", "", IM.file_digest(repo / "p.md"), descriptor="c", version="1")
    IM.record(log, repo, "claude", "r.md", "block", "k", "old", descriptor="c", version="1")

    def resolver(a):
        return "new" if a["kind"] == "block" else IM.UNKNOWN

    assert [c["path"] for c in IM.read(log, repo, "claude", current=resolver).changed] == ["r.md"]
    (repo / "p.md").write_text("two\n")
    assert {c["path"] for c in IM.read(log, repo, "claude", current=resolver).changed} == {"p.md", "r.md"}


def test_an_unreadable_file_is_unknown_not_gone(repo, log, monkeypatch):
    (repo / "p.md").write_text("one\n")
    IM.record(log, repo, "claude", "p.md", "plugin", "", "stale", descriptor="c", version="1")
    real = Path.read_bytes

    def deny(self):
        if self.name == "p.md":
            raise PermissionError("no")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", deny)
    assert IM.read(log, repo, "claude").changed == []


def test_a_resolver_returning_none_cannot_report_a_present_file_gone(repo, log):
    (repo / "p.md").write_text("one\n")
    IM.record(log, repo, "claude", "p.md", "plugin", "", IM.file_digest(repo / "p.md"), descriptor="c", version="1")
    assert IM.read(log, repo, "claude", current=lambda a: None).changed == []
