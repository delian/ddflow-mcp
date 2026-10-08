"""B-upgrade.4-apply.1-core: `services.upgrade_apply`, the apply step of `ddflow upgrade`.

The old-version project is the one a real ddflow 0.1.3 built (tests/fixtures/releases/0.1.3).
Each test applies through the service the way `api.upgrade` will, over the real plan.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import run_cli

from ddflow.api._base import _load
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import upgrade_apply as UA
from ddflow.services import upgrade_plan as UP

OLD = Path(__file__).parent / "fixtures" / "releases" / "0.1.3" / "project"
DRIVER = "docs/ddflow/drivers/implement-phase.md"


@pytest.fixture
def old(tmp_path: Path) -> Path:
    r = tmp_path / "old"
    shutil.copytree(OLD, r)
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    for k, v in (("user.email", "t@e.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(r), "config", k, v], check=True)
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "old"], check=True)
    return r


def go(repo: Path, categories: Any = None, **kw: Any) -> dict[str, Any]:
    log, cfg, st = _load(repo, "upgrader")
    return UA.apply(repo, log, cfg, st, categories=categories, agent="upgrader", **kw)


def plan(repo: Path) -> dict[str, Any]:
    log, cfg, st = _load(repo, "upgrader")
    return UP.build(repo, log, cfg, st)


def upgrades(repo: Path) -> list[dict[str, Any]]:
    return fold(EventLog(repo, "upgrader").read_all(), strict=False).upgrades


def tree(repo: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(repo)): p.read_bytes()
        for p in repo.rglob("*")
        if p.is_file() and ".git/" not in str(p) and "/.ddflow/local" not in str(p)
    }


def ids(body: dict[str, Any], status: str) -> list[str]:
    return [r["id"] for r in body["results"] if r["status"] == status]


def test_applying_hooks_installs_them_backs_up_and_records(old: Path) -> None:
    before = plan(old)
    assert any(
        i["id"] == "hooks:missing:claude:session-start" for i in before["categories"]["hooks"]
    )

    out = go(old, "hooks")

    assert out["exit"] == 0 and out["failed"] == 0, out["text"]
    assert "hooks:missing:claude:session-start" in ids(out, "applied")
    assert not [i for i in plan(old)["categories"]["hooks"] if i["id"].startswith("hooks:missing")]
    # the settings file did not exist: the backup says so; the manifest lists it
    manifest = json.loads((Path(out["backup"]) / UA.MANIFEST).read_text())
    assert any(f["path"] == ".claude/settings.json" and not f["existed"] for f in manifest["files"])
    assert ".ddflow/backups" in out["backup"] and "git diff" in out["text"]
    recorded = upgrades(old)
    assert len(recorded) == 1 and recorded[0]["categories"] == ["hooks"]
    assert recorded[0]["backup"] == out["backup"]


def test_the_backup_directory_is_git_ignored_and_holds_the_original_bytes(old: Path) -> None:
    driver = old / DRIVER
    original = driver.read_bytes()

    out = go(old, "instructions")

    assert DRIVER in " ".join(ids(out, "applied")), out["text"]
    assert driver.read_bytes() != original
    assert (Path(out["backup"]) / "files" / "in" / DRIVER).read_bytes() == original
    assert (old / ".ddflow" / "backups" / ".gitignore").read_text().strip().endswith("*")
    status = subprocess.run(
        ["git", "-C", str(old), "status", "--porcelain", ".ddflow/backups"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert status == ""


def test_a_second_apply_is_a_no_op_that_writes_nothing(old: Path) -> None:
    go(old, "hooks,instructions,mcp,repairs")
    snapshot = tree(old)
    backups = sorted(p.name for p in (old / ".ddflow" / "backups").iterdir() if p.is_dir())

    again = go(old, "hooks,instructions,mcp,repairs")

    assert again["noop"] is True and again["exit"] == 0 and again["backup"] == ""
    assert tree(old) == snapshot
    assert sorted(p.name for p in (old / ".ddflow" / "backups").iterdir() if p.is_dir()) == backups
    assert len(upgrades(old)) == 1


def test_an_operator_set_knob_is_refused_without_confirm_and_applied_with_it(old: Path) -> None:
    key = "worktree.max_parallel"
    item = next(i for i in plan(old)["categories"]["config"] if i["key"] == key)
    assert item["action"] == UP.OPERATOR

    refused = go(old, "config")

    assert key in " ".join(ids(refused, "refused")) or any(
        r["key"] == key and r["status"] == "refused" for r in refused["results"]
    )
    assert refused["exit"] == 3
    assert f"--confirm {key}" in refused["text"]
    assert Config.load(old).worktree.max_parallel == item["old"]
    # the unset knob was still acknowledged, so the run is not all-or-nothing
    assert any(
        r["key"] == "worktree.root" and r["status"] == "acknowledged" for r in refused["results"]
    )

    ok = go(old, "config", confirm={key: "the new default fits"})

    assert any(r["key"] == key and r["status"] == "applied" for r in ok["results"]), ok["text"]
    assert Config.load(old).worktree.max_parallel == item["new"]
    assert upgrades(old)[-1]["categories"] == ["config"]
    assert not any(i["key"] == key for i in plan(old)["categories"]["config"])


def test_an_unset_knob_is_acknowledged_by_the_agent_and_lists_the_pin_command(old: Path) -> None:
    out = go(old, "config", confirm={"worktree.max_parallel": "ok"})

    row = next(r for r in out["results"] if r["key"] == "worktree.root")
    assert row["status"] == "acknowledged"
    assert "ddflow config --set worktree.root" in row["detail"]
    event = [e for e in EventLog(old, "upgrader").read_all() if e.kind == "upgrade.applied"][-1]
    listed = {c["key"] for c in event.data["config_changes"]}
    assert "worktree.root" in listed and event.data["confirmed"]["worktree.max_parallel"] == "ok"


def test_ask_and_operator_policies_want_confirmation_for_every_config_change(old: Path) -> None:
    for policy in ("ask", "operator"):
        out = go(old, "config", config_changes=policy)
        assert any(r["key"] == "worktree.root" and r["status"] == "refused" for r in out["results"])
        assert out["exit"] == 3
        assert not any(
            r["key"] == "worktree.root" and r["status"] != "refused" for r in out["results"]
        )


def test_a_partial_apply_leaves_the_rest_of_the_plan(old: Path) -> None:
    before = plan(old)
    go(old, "hooks")
    after = plan(old)

    assert after["project_version"] != after["running"]
    assert [i["id"] for i in after["categories"]["config"]] == [
        i["id"] for i in before["categories"]["config"]
    ]
    # applying every category moves the project to the running version
    go(old, "all", confirm={i["key"]: "ok" for i in before["categories"]["config"]})
    final = plan(old)
    assert final["project_version"] == final["running"]
    assert final["categories"]["config"] == []


def test_a_refused_config_item_keeps_the_version_where_it_was(old: Path) -> None:
    out = go(old, "all")

    assert out["refused"] >= 1
    # not moved to the running version: the refused item is still news
    assert plan(old)["project_version"] != plan(old)["running"]
    assert any(i["action"] == UP.OPERATOR for i in plan(old)["categories"]["config"])


def test_an_applier_that_fails_leaves_the_backup_and_a_plan_that_can_run_again(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddflow.services import claudehooks as CH

    real = CH.install_spec

    def boom(repo: Path, h: Any) -> str:
        raise OSError("disk went away")

    monkeypatch.setattr(CH, "install_spec", boom)
    failed = go(old, "hooks")

    assert failed["exit"] == 1 and failed["failed"] >= 1
    assert Path(failed["backup"]).is_dir() and (Path(failed["backup"]) / UA.MANIFEST).is_file()
    assert upgrades(old) == []
    assert any(i["id"].startswith("hooks:missing") for i in plan(old)["categories"]["hooks"])

    monkeypatch.setattr(CH, "install_spec", real)
    redo = go(old, "hooks")

    assert redo["exit"] == 0 and len(upgrades(old)) == 1


def test_a_removed_knob_the_config_carries_is_removed_only_when_confirmed(old: Path) -> None:
    from ddflow.services import upgrade_manifest as UM

    cfgfile = old / ".ddflow" / "config.toml"
    cfgfile.write_text(cfgfile.read_text() + "\n[log]\nzzz_old = 1  # keep this comment\n")
    cfg = Config.load(old)
    change = UM.Change(version="0.2.0", kind="knob_removed", key="log.zzz_old", old=1, has_old=True)
    item = UP.config_items([change], cfg)[0]
    synthetic = {
        "running": "0.2.0",
        "project_version": "0.1.0",
        "total": 1,
        "up_to_date": False,
        "categories": {c: [] for c in UP.CATEGORIES} | {"config": [item]},
    }

    log, cfg, st = _load(old, "upgrader")
    refused = UA.apply(old, log, cfg, st, categories="config", plan=synthetic)
    assert refused["exit"] == 3 and "zzz_old" in cfgfile.read_text()

    done = UA.apply(
        old, log, cfg, st, categories="config", plan=synthetic, confirm={"log.zzz_old": "retired"}
    )

    assert done["exit"] == 0, done["text"]
    assert "zzz_old" not in cfgfile.read_text()
    assert "[log]" in cfgfile.read_text() or "log" in cfgfile.read_text()
    saved = Path(done["backup"]) / "files" / "in" / ".ddflow" / "config.toml"
    assert "zzz_old = 1  # keep this comment" in saved.read_text()


def test_a_knob_set_by_the_environment_is_not_rewritten(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DDFLOW_WORKTREE_MAX_PARALLEL", "9")
    log, cfg, st = _load(old, "upgrader")
    if not cfg.sources.get("worktree.max_parallel", "").startswith("env"):
        pytest.skip("this ddflow reads no environment override for that knob")
    out = UA.apply(old, log, cfg, st, categories="config", confirm={"worktree.max_parallel": "x"})
    assert any(r["status"] == "refused" and "environment" in r["detail"] for r in out["results"])


def test_a_dangling_mcp_entry_is_rewritten_and_saved_first(old: Path) -> None:
    mcp = old / ".mcp.json"
    mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "ddflow": {"command": "/nonexistent/venv/bin/python", "args": ["-m", "x"]}
                }
            }
        )
    )
    item = next((i for i in plan(old)["categories"]["mcp"]), None)
    if item is None:
        pytest.skip("the fixture's agent config is not one this ddflow checks")
    stale = mcp.read_bytes()

    out = go(old, "mcp")

    assert out["exit"] == 0, out["text"]
    assert (Path(out["backup"]) / "files" / "in" / ".mcp.json").read_bytes() == stale
    assert plan(old)["categories"]["mcp"] == []


def test_a_pending_repair_is_applied_and_recorded(old: Path) -> None:
    from ddflow.services import repairs as RP

    log, cfg, _st = _load(old, "upgrader")
    ctx = RP.context(old, log, cfg)
    if not RP.pending(ctx):
        # a fresh fixture log is undamaged: give it an orphaned prompt, as an old ddflow did
        shard = old / ".ddflow" / "events" / "olduser.jsonl"
        shard.parent.mkdir(parents=True, exist_ok=True)
        from ddflow.core.events import Event

        ev = Event(
            "session.prompt",
            "",
            {"text": "lost", "item": ""},
            agent="olduser",
            lamport=1,
            ts="2026-09-20T10:00:00Z",
        )
        shard.write_text(ev.to_json() + "\n")
    pend = [i for i in plan(old)["categories"]["repairs"] if i["action"] == UP.AGENT]
    if not pend:
        pytest.skip("no agent-consent repair is pending in this fixture")

    out = go(old, "repairs")

    # an operator-consent repair (an unknown author's shard) waits for --confirm
    assert out["exit"] in (0, 2, 3), out["text"]
    assert set(ids(out, "applied")) == {i["id"] for i in pend}
    assert not [i for i in plan(old)["categories"]["repairs"] if i["action"] == UP.AGENT]


def test_categories_are_validated_and_all_means_every_one() -> None:
    assert UA.parse_categories(None) == list(UP.CATEGORIES)
    assert UA.parse_categories("all") == list(UP.CATEGORIES)
    assert UA.parse_categories("mcp, hooks") == ["hooks", "mcp"]
    with pytest.raises(ValueError, match="unknown upgrade category"):
        UA.parse_categories("hooks,nope")


def test_bad_policy_and_backup_values_are_errors(old: Path) -> None:
    with pytest.raises(ValueError, match="backup mode"):
        go(old, backup="sideways")
    with pytest.raises(ValueError, match="config_changes"):
        go(old, config_changes="whenever")


def test_backup_none_writes_no_copy_but_still_records(old: Path) -> None:
    out = go(old, "hooks", backup="none")

    assert out["exit"] == 0 and out["backup"] == ""
    assert not (old / ".ddflow" / "backups").exists()
    assert upgrades(old)[-1]["backup"] == ""


def test_the_apply_remedies_no_longer_say_not_available(old: Path) -> None:
    text = UP.render(plan(old))

    assert "not available yet" not in text


def test_a_repair_that_could_not_run_exits_2_not_0(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddflow.services import repairs as RP

    synthetic = {
        "id": "repair:torn-lines",
        "category": "repairs",
        "repair": "torn-lines",
        "summary": "x",
        "action": UP.AGENT,
        "fix": "ddflow upgrade --apply",
    }
    full = plan(old)
    full["categories"] = {c: [] for c in UP.CATEGORIES} | {"repairs": [synthetic]}
    monkeypatch.setattr(
        RP,
        "apply",
        lambda repo, log, cfg, ids=None: [
            {"repair": "torn-lines", "unavailable": "shard unreadable", "findings": [], "events": 0}
        ],
    )

    log, cfg, st = _load(old, "upgrader")
    out = UA.apply(old, log, cfg, st, categories="repairs", plan=full)

    assert out["exit"] == 2 and out["unavailable"] == 1 and out["applied"] == 0
    assert "unavailable" in out["text"] and upgrades(old) == []


def test_a_failed_backup_reports_the_version_it_did_not_leave(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_disk(*a: Any, **k: Any) -> Path:
        raise OSError("read-only file system")

    monkeypatch.setattr(UA, "make_backup", no_disk)

    out = go(old, "hooks")

    assert out["exit"] == 1 and out["to"] != plan(old)["running"]
    assert "nothing was changed" in out["text"] and upgrades(old) == []
    assert not (old / ".claude" / "settings.json").exists()


def test_the_upgrade_is_in_the_fold_replay_and_history(old: Path) -> None:
    go(old, "config", confirm={"worktree.max_parallel": "the new default fits"})

    recorded = upgrades(old)[-1]
    assert recorded["confirmed"] == {"worktree.max_parallel": "the new default fits"}
    assert recorded["summary"].startswith("upgrade ") and "config" in recorded["summary"]
    assert any(i.startswith("knob_changed:worktree.max_parallel") for i in recorded["items"])
    assert {c["key"] for c in recorded["config_changes"]} >= {"worktree.root"}
    from conftest import run_cli

    _code, text, _err = run_cli(old, "replay")
    assert "Upgraded the project from ddflow" in text and "worktree.max_parallel" in text, text
    _code, hist, _err = run_cli(old, "history")
    assert "upgrade applied" in hist


def test_toml_remove_keeps_comments_and_reports_whether_the_key_was_there() -> None:
    from ddflow.infra import tomlcfg as TC

    text = "# top\n[log]\nzzz = 1  # why\nkeep = 2\n"

    out, was = TC.remove(text, "log.zzz")

    assert was is True and out == "# top\n[log]\nkeep = 2\n"
    assert TC.remove(out, "log.zzz") == (out, False)
    assert TC.remove(out, "nope.zzz") == (out, False)


def test_two_backups_in_one_clock_tick_get_their_own_directories(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddflow.services import backups as BK

    monkeypatch.setattr(BK, "backup_name", lambda frm, to: "same-tick")
    f = old / DRIVER

    first = UA.make_backup(old, [f], "", "9.9.9")
    second = UA.make_backup(old, [f], "", "9.9.9")

    assert first != second and first.is_dir() and second.is_dir()


def test_a_partial_apply_summary_names_the_version_it_reached(old: Path) -> None:
    go(old, "hooks")

    event = [e for e in EventLog(old, "upgrader").read_all() if e.kind == "upgrade.applied"][-1]
    assert event.data["to"] == "0.0.0" and "-> 0.0.0" in event.data["summary"]


# -- B-upgrade.4-apply.2-docs: hand edits survive ----------------------------------------------


def _adopted_old(old: Path) -> None:
    """The 0.1.3 fixture with this ddflow's driver docs and rules (stamped), as an upgrade
    leaves them -- so the only drift left is what a test adds."""
    from ddflow.services import adopt as AD

    AD.refresh_docs(old, backup=False)


def test_a_local_edit_in_a_driver_doc_survives_to_local_edits_and_is_reported(old: Path) -> None:
    _adopted_old(old)
    driver = old / DRIVER
    edited = driver.read_text().replace("Claim before you edit", "Claim when you like")
    assert edited != driver.read_text()
    driver.write_text(edited)
    item = next(i for i in plan(old)["categories"]["instructions"] if i["path"] == DRIVER)
    assert item["provenance"] == "hand-edited" and item["action"] == UP.AGENT

    out = go(old, "instructions")

    assert out["exit"] == 0, out["text"]
    aside = old / (DRIVER + ".local-edits")
    assert aside.read_text() == edited, "the edit is kept whole"
    assert any(".local-edits" in r["detail"] for r in out["results"]), out["text"]
    assert "Claim when you like" not in driver.read_text()
    assert not [i for i in plan(old)["categories"]["instructions"] if i["path"] == DRIVER]


def test_applying_twice_leaves_an_identical_tree_after_a_hand_edit(old: Path) -> None:
    _adopted_old(old)
    driver = old / DRIVER
    driver.write_text(driver.read_text().replace("Claim before you edit", "Claim when you like"))
    go(old, "instructions")
    snapshot = tree(old)

    again = go(old, "instructions")

    assert again["noop"] is True and tree(old) == snapshot


def test_a_stamped_older_copy_is_stale_not_hand_edited(old: Path) -> None:
    from ddflow.services import adopt as AD

    _adopted_old(old)
    driver = old / DRIVER
    region = AD._driver_region(DRIVER)
    driver.write_text(region.render("# an older driver, as an older ddflow wrote it\n"))
    item = next(i for i in plan(old)["categories"]["instructions"] if i["path"] == DRIVER)
    assert item["provenance"] == "stale" and item["action"] == UP.AGENT

    out = go(old, "instructions")

    assert out["exit"] == 0 and not (old / (DRIVER + ".local-edits")).exists()
    assert "an older driver" not in driver.read_text()


def test_a_driver_doc_a_newer_ddflow_wrote_is_a_note_and_stays(old: Path) -> None:
    import re

    from ddflow import FORMAT_LEVEL

    _adopted_old(old)
    driver = old / DRIVER
    newer = re.sub(r"fmt=\d+", f"fmt={FORMAT_LEVEL + 1}", driver.read_text(), count=1)
    driver.write_text(newer.replace("Claim before you edit", "Claim v2"))
    item = next(i for i in plan(old)["categories"]["instructions"] if i["path"] == DRIVER)
    assert item["provenance"] == "newer" and item["action"] == UP.NOTE

    out = go(old, "instructions")

    assert "Claim v2" in driver.read_text() and DRIVER not in " ".join(ids(out, "applied"))


def test_a_hand_edited_rules_block_is_kept_in_local_edits(old: Path) -> None:
    _adopted_old(old)
    agents = old / "AGENTS.md"
    edited = agents.read_text().replace("Claim before you edit", "Claim whenever")
    agents.write_text(edited)
    item = next(i for i in plan(old)["categories"]["instructions"] if i["path"] == "AGENTS.md")
    assert item["provenance"] == "hand-edited" and item["action"] == UP.AGENT

    out = go(old, "instructions")

    assert out["exit"] == 0, out["text"]
    assert (old / "AGENTS.md.local-edits").read_text() == edited
    assert "Claim before you edit" in agents.read_text()


def test_the_legacy_unstamped_driver_is_refreshed_and_stamped(old: Path) -> None:
    from ddflow.services import adopt as AD

    item = next(i for i in plan(old)["categories"]["instructions"] if i["path"] == DRIVER)
    assert item["provenance"] == "stale"  # a release has shipped since: no edit is claimed

    go(old, "instructions")

    assert AD._driver_region(DRIVER).owns((old / DRIVER).read_text())
    assert not (old / (DRIVER + ".local-edits")).exists()


def test_a_missing_rules_file_is_summarised_as_missing_not_as_an_edit(old: Path) -> None:
    _adopted_old(old)
    (old / "AGENTS.md").unlink()

    # no release since the project last worked: the branch that used to say "hand-edited"
    items = UP.instruction_items(old, drifted_by_release=False)
    item = next(i for i in items if i["path"] == "AGENTS.md")

    assert item["provenance"] == "missing" and item["action"] == UP.AGENT
    assert "hand-edited" not in item["summary"] and ".local-edits" not in item["summary"]


def test_a_rules_block_a_newer_ddflow_wrote_is_a_note_and_never_counted_as_applied(
    old: Path,
) -> None:
    import re

    from ddflow import FORMAT_LEVEL

    _adopted_old(old)
    agents = old / "AGENTS.md"
    newer = re.sub(r"fmt=\d+", f"fmt={FORMAT_LEVEL + 1}", agents.read_text(), count=1)
    agents.write_text(newer.replace("Claim before you edit", "Claim v2"))
    item = next(i for i in plan(old)["categories"]["instructions"] if i["path"] == "AGENTS.md")
    assert item["provenance"] == "newer" and item["action"] == UP.NOTE

    out = go(old, "instructions")

    assert "Claim v2" in agents.read_text()
    assert "instructions:AGENTS.md" not in ids(out, "applied")


def test_a_refusal_from_the_refresh_is_reported_refused_not_applied(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddflow.services import adopt as AD

    _adopted_old(old)
    (old / "AGENTS.md").write_text("# mine\n")  # no block: the plan lists it
    monkeypatch.setattr(
        AD, "refresh_docs", lambda *a, **k: [AD.Refused("REFUSED AGENTS.md: a newer format")]
    )

    out = go(old, "instructions")

    assert "instructions:AGENTS.md" in ids(out, "refused") and out["exit"] == 3
    assert out["applied"] == 0 or "instructions:AGENTS.md" not in ids(out, "applied")


def test_the_plan_promises_local_edits_only_where_the_writer_keeps_them(old: Path) -> None:
    from ddflow.services import adopt as AD

    assert run_cli(old, "adopt", "--agents", "cursor")[0] == 0
    mdc = old / AD.NATIVE_RULES["cursor"].path
    mdc.write_text(mdc.read_text().replace("alwaysApply: true", "alwaysApply: false"))

    item = next(
        i for i in plan(old)["categories"]["instructions"] if i["path"] == str(mdc.relative_to(old))
    )

    assert ".local-edits" not in item["summary"]


def test_a_hand_edit_that_is_not_utf8_is_kept_byte_for_byte(old: Path) -> None:
    _adopted_old(old)
    driver = old / DRIVER
    raw = driver.read_bytes().replace(b"Claim before you edit", b"Claim caf\xe9 you edit")
    driver.write_bytes(raw)

    out = go(old, "instructions")

    assert out["exit"] == 0, out["text"]
    assert (old / (DRIVER + ".local-edits")).read_bytes() == raw
    assert b"caf\xe9" not in driver.read_bytes() and "\ufffd" not in driver.read_text()


def test_a_native_block_surface_written_by_a_newer_ddflow_is_a_note(old: Path) -> None:
    import re

    from ddflow import FORMAT_LEVEL
    from ddflow.services import adopt as AD

    key = next(k for k, r in AD.NATIVE_RULES.items() if r.form == AD.FORM_BLOCK)
    assert run_cli(old, "adopt", "--agents", key)[0] == 0
    path = old / AD.NATIVE_RULES[key].path
    path.write_text(
        re.sub(r"fmt=\d+", f"fmt={FORMAT_LEVEL + 1}", path.read_text(), count=1).replace(
            "Claim before you edit", "Claim v2"
        )
    )

    state = next(r for r in AD.rules_status(old) if r.path == AD.NATIVE_RULES[key].path)

    assert state.newer and state.stamped
