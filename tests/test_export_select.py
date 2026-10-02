"""Selecting documents and owning their templates (B-export-select; decisions
D-export-selection, D-export-agent-enable, D-export-templates): `ddflow export enable | disable
| ack | eject | validate`, the MCP actions, the events, the brief and doctor lines."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core import model
from ddflow.core.events import PROVENANCE_KINDS, Event
from ddflow.services.export import registry as R
from ddflow.services.export import templates as T
from ddflow.surfaces.mcp import Server

ROOT = Path(__file__).resolve().parents[1]


def run_human(repo: Path, *argv: str) -> tuple[int, str, str]:
    """The CLI as a PERSON at their own terminal: no agent identity, no harness marker."""
    env = {k: v for k, v in os.environ.items() if k not in ("DDFLOW_AGENT", "CLAUDECODE")}
    env["PYTHONPATH"] = str(ROOT)
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), *argv],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    return p.returncode, p.stdout, p.stderr


@pytest.fixture
def proj(repo: Path) -> Path:
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "phase", "add", "P1", "--title", "Billing")[0] == 0
    return repo


def _cfg(proj: Path, local: bool = False) -> str:
    p = proj / ".ddflow" / ("local" if local else "") / "config.toml"
    return p.read_text() if p.exists() else ""


def _events(proj: Path, kind: str) -> list[dict]:
    out = []
    for f in (proj / ".ddflow" / "events").glob("*.jsonl"):
        for line in f.read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                if e["kind"] == kind:
                    out.append(e)
    return out


def _call(repo: Path, args: dict, agent: str = "") -> dict:
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_export", "arguments": args},
        }
    )["result"]
    return {"exit": reply["_meta"]["exit"], "body": reply["content"][0]["text"]}


# -- agent enable ---------------------------------------------------------------------


def test_an_agent_enable_succeeds_names_the_agent_and_the_stop_command_and_writes_no_file(proj):
    code, out, _ = run_cli(proj, "export", "enable", "roadmap", agent="bot")
    assert code == 0
    assert "(by " in out and "ddflow export disable roadmap" in out
    assert 'documents = ["roadmap"]' in _cfg(proj)
    assert not (proj / "ROADMAP.md").exists()  # an enable creates no file
    (ev,) = _events(proj, "export.enabled")
    assert ev["subject"] == "roadmap" and ev["data"]["by"] == ev["agent"] != ""
    assert ev["data"]["path"] == "ROADMAP.md" and ev["data"]["mode"] == "whole"
    assert ev["data"]["human"] is False and ev["data"]["locked"] is False


def test_enable_then_all_writes_exactly_the_selected_documents(proj):
    run_cli(proj, "export", "enable", "status", agent="bot")
    assert run_cli(proj, "export", "--all", "--update", "--yes")[0] == 0
    assert (proj / "STATUS.md").is_file() and not (proj / "ROADMAP.md").exists()


def test_enable_is_idempotent_and_records_one_event(proj):
    run_cli(proj, "export", "enable", "roadmap", agent="bot")
    code, out, _ = run_cli(proj, "export", "enable", "roadmap", agent="bot")
    assert code == 0 and "already enabled" in out
    assert len(_events(proj, "export.enabled")) == 1
    assert _cfg(proj).count('documents = ["roadmap"]') == 1


def test_enable_with_path_and_mode_edits_the_document_table(proj):
    code, out, _ = run_cli(
        proj, "export", "enable", "status", "--path", "docs/S.md", "--mode", "region", agent="bot"
    )
    assert code == 0 and "docs/S.md" in out
    cfg = _cfg(proj)
    assert 'path = "docs/S.md"' in cfg and 'mode = "region"' in cfg
    row = next(
        r
        for r in json.loads(run_cli(proj, "--json", "export")[1])["documents"]
        if r["doc"] == "status"
    )
    assert row["target"] == "docs/S.md" and row["mode"] == "region" and row["selected"]


def test_enable_refuses_an_unknown_kind_a_bad_mode_and_a_path_outside_the_repo(proj):
    code, _o, err = run_cli(proj, "export", "enable", "nosuch", agent="bot")
    assert code == 3 and "roadmap" in err  # the kinds are listed
    assert run_cli(proj, "export", "enable", "roadmap", "--mode", "bogus", agent="bot")[0] == 3
    assert run_cli(proj, "export", "enable", "roadmap", "--path", "../x.md", agent="bot")[0] == 3
    assert (
        run_cli(proj, "export", "enable", "roadmap", "--path", ".ddflow/x.md", agent="bot")[0] == 3
    )
    assert "[export]" not in _cfg(proj) and not _events(proj, "export.enabled")


def test_enable_local_writes_the_local_layer_only(proj):
    assert run_cli(proj, "export", "enable", "bugs", "--local", agent="bot")[0] == 0
    assert 'documents = ["bugs"]' in _cfg(proj, local=True)
    assert "[export]" not in _cfg(proj)
    assert json.loads(run_cli(proj, "--json", "export")[1])["selected"] == ["bugs"]


def test_enable_append_registers_the_target_as_append_only(proj):
    assert run_cli(proj, "export", "enable", "changelog", "--mode", "append", agent="bot")[0] == 0
    assert "CHANGELOG.md" in _cfg(proj) and "append_only_globs" in _cfg(proj)


def test_the_listing_names_who_enabled_each_document_and_when(proj):
    run_cli(proj, "export", "enable", "roadmap", agent="bot")
    out = run_cli(proj, "export")[1]
    assert "enabled by" in out and "not acknowledged" in out
    row = next(
        r
        for r in json.loads(run_cli(proj, "--json", "export")[1])["documents"]
        if r["doc"] == "roadmap"
    )
    assert row["enabled_by"] and row["enabled_at"] and row["by_agent"] and not row["acknowledged"]


# -- brief, doctor, ack ---------------------------------------------------------------


def test_brief_shows_one_line_until_the_operator_acknowledges(proj):
    assert "Export:" not in run_cli(proj, "brief")[1]
    run_cli(proj, "export", "enable", "roadmap", agent="bot")
    brief = run_cli(proj, "brief")[1]
    assert brief.count("Export:") == 1 and "ddflow export disable roadmap" in brief
    assert (
        "roadmap" in run_cli(proj, "doctor")[1] and "not acknowledged" in run_cli(proj, "doctor")[1]
    )
    assert run_human(proj, "export", "ack")[0] == 0
    assert "Export:" not in run_cli(proj, "brief")[1]
    assert "not acknowledged" not in run_cli(proj, "doctor")[1]
    assert "not acknowledged" not in run_human(proj, "export")[1]


def test_an_agent_cannot_acknowledge_its_own_enable(proj):
    run_cli(proj, "export", "enable", "roadmap", agent="bot")
    code, _o, err = run_cli(proj, "export", "ack", agent="bot")
    assert code == 3 and "operator" in err
    assert "Export:" in run_cli(proj, "brief")[1]


def test_an_operator_enable_is_not_a_notice(proj):
    assert run_human(proj, "export", "enable", "roadmap")[0] == 0
    assert "Export:" not in run_cli(proj, "brief")[1]
    (ev,) = _events(proj, "export.enabled")
    assert ev["data"]["human"] is True


# -- disable and lock -----------------------------------------------------------------


def test_disable_leaves_all_but_print_still_works(proj):
    run_cli(proj, "export", "enable", "roadmap", agent="bot")
    code, out, _ = run_cli(proj, "export", "disable", "roadmap", agent="bot")
    assert code == 0 and "disabled roadmap" in out
    assert run_cli(proj, "export", "--all")[0] == 2  # nothing selected any more
    assert run_cli(proj, "export", "roadmap")[0] == 0  # on-demand print is always possible
    assert _events(proj, "export.disabled")
    assert "Export:" not in run_cli(proj, "brief")[1]
    assert run_cli(proj, "export", "disable", "roadmap", agent="bot")[0] == 0  # idempotent


def test_a_locked_document_refuses_an_agent_enable_and_the_operator_can_unlock(proj):
    run_cli(proj, "export", "enable", "roadmap", agent="bot")
    assert run_human(proj, "export", "disable", "roadmap", "--lock")[0] == 0
    code, _o, err = run_cli(proj, "export", "enable", "roadmap", agent="other")
    assert code == 3 and "locked by the operator" in err
    assert code == run_cli(proj, "--json", "export", "enable", "roadmap", agent="other")[0]
    assert _call(proj, {"action": "enable", "doc": "roadmap"})["exit"] == 3
    assert "locked" in run_cli(proj, "export")[1]
    assert "documents = []" in _cfg(proj)
    assert run_human(proj, "export", "enable", "roadmap")[0] == 0  # the operator unlocks
    assert run_cli(proj, "export", "disable", "roadmap", agent="bot")[0] == 0
    assert run_cli(proj, "export", "enable", "roadmap", agent="bot")[0] == 0  # no lock now


def test_an_agent_cannot_lock_and_a_lock_survives_a_plain_disable(proj):
    code, _o, err = run_cli(proj, "export", "disable", "roadmap", "--lock", agent="bot")
    assert code == 3 and "operator" in err
    assert run_human(proj, "export", "disable", "roadmap", "--lock")[0] == 0
    assert run_cli(proj, "export", "disable", "roadmap", agent="bot")[0] == 0
    assert run_cli(proj, "export", "enable", "roadmap", agent="bot")[0] == 3


def test_the_harness_marker_alone_is_an_agent(proj):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "CLAUDECODE": "1"}
    env.pop("DDFLOW_AGENT", None)
    p = subprocess.run(
        [
            sys.executable,
            "-m",
            "ddflow",
            "--repo",
            str(proj),
            "export",
            "disable",
            "roadmap",
            "--lock",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 3 and "operator" in p.stderr


# -- events and the fold --------------------------------------------------------------


def _ev(kind: str, subject: str, n: int, **data) -> Event:
    return Event(
        kind=kind, subject=subject, data=data, agent="a", lamport=n, ts=f"2026-10-0{n}T00:00:00Z"
    )


def test_the_fold_tracks_who_enabled_what_and_the_lock():
    st = model.fold(
        [
            _ev("export.enabled", "roadmap", 1, by="bot", path="R.md", mode="whole"),
            _ev("export.disabled", "roadmap", 2, by="op", human=True, locked=True),
            _ev("export.enabled", "status", 3, by="op", human=True),
            _ev("export.acknowledged", "export", 4, human=True, documents=["roadmap", "nosuch", 7]),
        ]
    )
    assert st.exports["roadmap"]["locked"] is True and not st.exports["roadmap"]["enabled"]
    assert st.exports["status"]["acked"] is True
    st = model.fold([_ev("export.enabled", "roadmap", 1, by="bot")])
    assert st.exports["roadmap"]["acked"] is False
    st = model.fold(
        [
            *[_ev("export.enabled", "roadmap", 1, by="bot")],
            _ev("export.acknowledged", "export", 2, human=True, documents=["roadmap"]),
        ]
    )
    assert st.exports["roadmap"]["acked"] is True


def test_an_older_ddflow_reports_the_new_kinds_instead_of_failing(monkeypatch):
    for k in ("export.enabled", "export.disabled", "export.acknowledged"):
        monkeypatch.delitem(model.HANDLERS, k)
    st = model.fold([_ev("export.enabled", "roadmap", 1)], strict=False)
    assert st.skipped_kinds == {"export.enabled": 1}


def test_the_kinds_are_declared_and_survive_compaction():
    kinds = {"export.enabled", "export.disabled", "export.acknowledged"}
    assert kinds <= model.known_kinds() and kinds <= PROVENANCE_KINDS


# -- MCP ------------------------------------------------------------------------------


def test_mcp_enable_disable_list_validate_match_the_cli(proj):
    r = _call(proj, {"action": "enable", "doc": "roadmap", "path": "docs/R.md", "mode": "region"})
    assert r["exit"] == 0
    body = json.loads(r["body"])
    assert body["doc"] == "roadmap" and body["by_agent"] is True
    assert "ddflow export disable roadmap" in body["text"] and body["by"]
    assert not (proj / "docs").exists()  # no file
    (ev,) = _events(proj, "export.enabled")
    assert ev["data"]["human"] is False
    cli_list = json.loads(run_cli(proj, "--json", "export")[1])
    assert json.loads(_call(proj, {"action": "list"})["body"]) == cli_list
    assert json.loads(_call(proj, {})["body"]) == cli_list
    cli_val = json.loads(run_cli(proj, "--json", "export", "validate")[1])
    assert json.loads(_call(proj, {"action": "validate"})["body"]) == cli_val
    assert cli_val["results"][0]["ok"] is True
    assert _call(proj, {"action": "disable", "doc": "roadmap"})["exit"] == 0
    assert json.loads(run_cli(proj, "--json", "export")[1])["selected"] == []


def test_mcp_action_refusals(proj):
    assert _call(proj, {"action": "enable"})["exit"] == 3  # needs doc
    assert _call(proj, {"action": "enable", "doc": "nosuch"})["exit"] == 3
    assert _call(proj, {"action": "bogus"})["exit"] == 3
    assert _call(proj, {"action": "list", "write": True})["exit"] == 3
    assert _call(proj, {"action": "enable", "doc": "roadmap", "path": "../x"})["exit"] == 3
    assert _call(proj, {"action": "validate"})["exit"] == 2  # nothing selected


def test_mcp_cannot_lock_acknowledge_or_eject(proj):
    props = Server(proj).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"][
        "tools"
    ]
    (tool,) = [t for t in props if t["name"] == "ddflow_export"]
    names = set(tool["inputSchema"]["properties"])
    assert {"action", "mode"} <= names and not names & {"lock", "force", "template", "eject"}
    assert "eject" not in json.dumps(tool["inputSchema"]["properties"]["action"])


# -- eject ----------------------------------------------------------------------------


def test_eject_copies_the_shipped_template_and_renders_the_same(proj):
    run_cli(proj, "export", "enable", "roadmap", agent="bot")
    before = run_cli(proj, "export", "roadmap")[1]
    code, out, _ = run_cli(proj, "export", "eject", "roadmap")
    dst = proj / ".ddflow" / "templates" / "export" / "roadmap.md.j2"
    assert code == 0 and dst.is_file() and "ejected" in out
    text = dst.read_text()
    assert text.startswith("{# ddflow-shipped: ") and R.shipped_template("roadmap").text in text
    assert run_cli(proj, "export", "roadmap")[1] == before  # the marker renders to nothing


def test_eject_is_idempotent_and_never_overwrites_an_edit_without_force(proj):
    run_cli(proj, "export", "eject", "roadmap")
    dst = proj / ".ddflow" / "templates" / "export" / "roadmap.md.j2"
    assert "already" in run_cli(proj, "export", "eject", "roadmap")[1]
    dst.write_text(dst.read_text() + "\nmine\n")
    code, _o, err = run_cli(proj, "export", "eject", "roadmap")
    assert code == 3 and "--force" in err and dst.read_text().endswith("mine\n")
    assert run_cli(proj, "export", "eject", "roadmap", "--force")[0] == 0
    assert "mine" not in dst.read_text()
    assert run_cli(proj, "export", "eject", "nosuch")[0] == 3
    assert run_cli(proj, "export", "eject")[0] == 3


def test_an_unedited_older_copy_is_refreshed_and_an_edited_one_is_reported_as_drift(proj):
    dst = proj / ".ddflow" / "templates" / "export" / "roadmap.md.j2"
    dst.parent.mkdir(parents=True)
    old = "# Old shipped roadmap\n"
    dst.write_text(f"{{# ddflow-shipped: {R.template_digest(old)} -#}}\n{old}")
    assert any("older shipped roadmap" in n and "no edits" in n for n in T.drift_notes(proj))
    assert "older shipped" in run_cli(proj, "doctor")[1]
    run_cli(proj, "export", "enable", "roadmap", agent="bot")
    assert "older shipped" in run_cli(proj, "export", "validate")[1]
    assert run_cli(proj, "export", "eject", "roadmap")[0] == 0  # unedited: refreshed, no --force
    assert T.drift_notes(proj) == []
    dst.write_text(dst.read_text() + "\nmine\n")
    assert T.drift_notes(proj) == []  # current digest: an edit is not drift
    dst.write_text(dst.read_text().replace(R.shipped_digest("roadmap"), "0" * 12))
    assert any("has your edits" in n for n in T.drift_notes(proj))


def test_eject_notes_a_configured_template_that_takes_precedence(proj):
    p = proj / ".ddflow" / "config.toml"
    p.write_text(_cfg(proj) + '\n[export.roadmap]\ntemplate = "x.j2"\n')
    out = run_cli(proj, "export", "eject", "roadmap")
    assert out[0] == 0 and "takes precedence" in out[2]


# -- validate -------------------------------------------------------------------------


def test_validate_reports_a_broken_template_with_file_and_line(proj):
    run_cli(proj, "export", "enable", "roadmap", agent="bot")
    run_cli(proj, "export", "enable", "status", agent="bot")
    code, out, _ = run_cli(proj, "export", "validate")
    assert code == 0 and "roadmap: ok" in out and "status: ok" in out
    run_cli(proj, "export", "eject", "roadmap")
    dst = proj / ".ddflow" / "templates" / "export" / "roadmap.md.j2"
    lines = dst.read_text().splitlines()
    lines[2] = "{% if %}"
    dst.write_text("\n".join(lines) + "\n")
    code, out, err = run_cli(proj, "export", "validate")
    assert code == 2 and "roadmap.md.j2:3" in err and "status: ok" in out
    data = json.loads(run_cli(proj, "--json", "export", "validate", "roadmap")[1])
    assert data["results"][0]["ok"] is False and ":3" in data["results"][0]["message"]
    # undefined variables are errors too, never an empty clean document
    lines[2] = "{{ nope.x }}"
    dst.write_text("\n".join(lines) + "\n")
    assert run_cli(proj, "export", "validate", "roadmap")[0] == 2
    assert not (proj / "ROADMAP.md").exists()  # validate writes nothing


def test_validate_with_nothing_selected_says_so_and_a_named_kind_works_anyway(proj):
    code, _o, err = run_cli(proj, "export", "validate")
    assert code == 2 and "no documents are selected" in err
    assert run_cli(proj, "export", "validate", "bugs")[0] == 0
    assert run_cli(proj, "export", "validate", "nosuch")[0] == 3


def test_template_flag_renders_without_writing(proj):
    t = proj / "my.j2"
    t.write_text("Schema: {{ schema_version }}\n")
    code, out, _ = run_cli(proj, "export", "roadmap", "--template", str(t))
    assert code == 0 and "Schema:" in out and not (proj / "ROADMAP.md").exists()


def test_the_help_topic_and_readme_document_the_new_verbs():
    help_text = (ROOT / "ddflow/templates/prompts/help/export.md").read_text()
    readme = (ROOT / "README.md").read_text()
    for needle in (
        "export enable",
        "export disable",
        "--lock",
        "export ack",
        "export eject",
        "export validate",
    ):
        assert needle in help_text and needle in readme, needle


def test_a_forged_agent_enable_does_not_lift_the_operators_lock():
    st = model.fold(
        [
            _ev("export.disabled", "roadmap", 1, by="op", human=True, locked=True),
            _ev("export.enabled", "roadmap", 2, by="bot"),  # not human: the lock stays
            _ev("export.disabled", "roadmap", 3, by="bot", locked=False),
        ]
    )
    assert st.exports["roadmap"]["locked"] is True
    st = model.fold(
        [
            _ev("export.disabled", "roadmap", 1, by="op", human=True, locked=True),
            _ev("export.enabled", "roadmap", 2, by="op", human=True),
        ]
    )
    assert st.exports["roadmap"]["locked"] is False


def test_no_document_kind_is_named_like_a_verb():
    from ddflow.surfaces.commands.export import VERBS

    assert not set(R.names()) & set(VERBS)


def test_a_non_human_acknowledgement_event_clears_nothing():
    st = model.fold(
        [
            _ev("export.enabled", "roadmap", 1, by="bot"),
            _ev("export.acknowledged", "export", 2, documents=["roadmap"]),
        ]
    )
    assert st.exports["roadmap"]["acked"] is False


def test_mcp_refuses_mode_and_path_outside_enable(proj):
    assert _call(proj, {"action": "disable", "doc": "roadmap", "mode": "append"})["exit"] == 3
    assert _call(proj, {"action": "validate", "doc": "roadmap", "path": "x.md"})["exit"] == 3


def _on_a_terminal(repo: Path, *argv: str) -> int:
    """The CLI as a person at a real terminal: stdin and stdout are a pty."""
    import pty

    env = {k: v for k, v in os.environ.items() if k not in ("DDFLOW_AGENT", "CLAUDECODE")}
    env["PYTHONPATH"] = str(ROOT)
    master, slave = pty.openpty()
    try:
        p = subprocess.run(
            [sys.executable, "-m", "ddflow", "--repo", str(repo), *argv],
            stdin=slave,
            stdout=slave,
            stderr=subprocess.PIPE,
            env=env,
            timeout=300,
        )
    finally:
        os.close(master)
        os.close(slave)
    return p.returncode


def test_the_plain_listing_at_a_terminal_acknowledges_and_no_other_mode_does(proj):
    run_cli(proj, "export", "enable", "roadmap", agent="bot")
    _on_a_terminal(proj, "export", "--check")  # a comparison with no document is not a listing
    assert not _events(proj, "export.acknowledged")
    assert "Export:" in run_cli(proj, "brief")[1]
    assert _on_a_terminal(proj, "export") == 0
    (ev,) = _events(proj, "export.acknowledged")
    assert ev["data"]["documents"] == ["roadmap"] and ev["data"]["human"] is True
    assert "Export:" not in run_cli(proj, "brief")[1]
