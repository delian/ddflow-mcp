"""Hooks are stamped regions (B-uni-compat-artifacts.4): a git hook, a harness settings
entry and the frozen-files block each say which ddflow wrote them, and an older ddflow
neither downgrades a newer one's nor loses a person's lines or edits."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import claudehooks as CH
from ddflow.services import enforce as E
from ddflow.services import legacy as L
from ddflow.services.adopt import Refused


def _hook(repo: Path, name: str = "pre-commit") -> Path:
    return E.hooks_dir(repo) / name


# -- git hooks ---------------------------------------------------------------------------


def test_an_installed_hook_is_a_stamped_region_that_still_carries_the_old_marker(repo):
    E.install(repo)
    text = _hook(repo).read_text()
    assert text.startswith("#!/bin/sh\n# ddflow:begin hooks/pre-commit ddflow=")
    assert " fmt=1 " in text and text.rstrip().endswith("# ddflow:end hooks/pre-commit")
    assert E.HOOK_MARKER in text, "an older ddflow knows only this line"
    assert E.armed(repo).via == "ddflow"


def test_a_second_install_is_an_update_of_the_same_bytes(repo):
    E.install(repo)
    before = _hook(repo).read_text()
    assert "updated the ddflow pre-commit hook" in E.install(repo)
    assert _hook(repo).read_text() == before


def test_a_hook_written_before_regions_existed_is_ours_and_is_upgraded(repo):
    hook = _hook(repo)
    hook.write_text(f"#!/bin/sh\n{E.HOOK_MARKER}\nexec true\n")
    assert E.armed(repo).via == "ddflow"
    assert "updated the ddflow pre-commit hook" in E.install(repo)
    assert "ddflow:begin hooks/pre-commit" in hook.read_text()


def test_an_older_ddflow_does_not_downgrade_a_newer_hook(repo):
    E.install(repo)
    hook = _hook(repo)
    text = hook.read_text()
    newer = text.replace(" fmt=1 ", " fmt=99 ", 1).replace("ddflow=", "ddflow=9.9.9 x=", 1)
    hook.write_text(newer)
    msg = E.install(repo)
    assert msg.startswith("REFUSED") and "upgrade ddflow to >= " in msg
    assert hook.read_text() == newer
    assert E.armed(repo).via == "ddflow", "a newer hook still runs the check"


def test_lines_around_the_region_survive_a_refresh_and_an_uninstall(repo):
    E.install(repo)
    hook = _hook(repo)
    hook.write_text(
        hook.read_text().replace("#!/bin/sh\n", "#!/bin/sh\necho mine-before\n", 1)
        + "echo mine-after\n"
    )
    E.install(repo)
    text = hook.read_text()
    assert "echo mine-before" in text and "echo mine-after" in text
    assert "removed the ddflow pre-commit hook" in E.uninstall(repo)
    rest = hook.read_text()
    assert "echo mine-before" in rest and "echo mine-after" in rest
    assert E.HOOK_MARKER not in rest and "ddflow:begin" not in rest


def test_a_hand_edited_hook_is_backed_up_before_it_is_rewritten(repo):
    E.install(repo)
    hook = _hook(repo)
    edited = hook.read_text().replace("exec ", "exec echo edited; exec ", 1)
    assert edited != hook.read_text()
    hook.write_text(edited)
    msg = E.install(repo)
    assert "edited by hand" in msg and ".ddflow/backups/" in msg
    copies = [p for p in (repo / ".ddflow" / "backups").rglob("pre-commit") if p.is_file()]
    assert copies and copies[0].read_text() == edited
    assert "echo edited" not in hook.read_text()


def test_an_unmanaged_hook_is_still_never_clobbered(repo):
    hook = _hook(repo)
    hook.write_text("#!/bin/sh\necho theirs\n")
    assert E.install(repo).startswith("REFUSED")
    assert hook.read_text() == "#!/bin/sh\necho theirs\n"
    assert "not managed by ddflow" in E.uninstall(repo)


def test_a_broken_region_is_refused_not_guessed(repo):
    E.install(repo)
    hook = _hook(repo)
    broken = hook.read_text().replace("# ddflow:end hooks/pre-commit\n", "")
    hook.write_text(broken)
    assert "broken ddflow markers" in E.install(repo)
    assert "broken ddflow markers" in E.uninstall(repo)
    assert hook.read_text() == broken


def test_force_replaces_a_hook_whose_region_is_broken(repo):
    E.install(repo)
    hook = _hook(repo)
    hook.write_text(hook.read_text().replace("# ddflow:end hooks/pre-commit\n", ""))
    assert "installed the ddflow pre-commit hook" in E.install(repo, force=True)
    assert hook.read_text().count("ddflow:end hooks/pre-commit") == 1


def test_bytes_that_are_not_utf8_around_the_region_are_never_rewritten(repo):
    E.install(repo)
    hook = _hook(repo)
    raw = hook.read_bytes() + b"# caf\xe9\n"
    hook.write_bytes(raw)
    assert "not UTF-8" in E.install(repo)
    assert "not UTF-8" in E.uninstall(repo)
    assert hook.read_bytes() == raw
    assert "installed the ddflow pre-commit hook" in E.install(repo, force=True)


def test_a_stray_byte_inside_the_region_goes_with_it_on_uninstall(repo):
    E.install(repo)
    hook = _hook(repo)
    raw = hook.read_bytes().replace(b"Refuses", b"Refus\xffes", 1)
    hook.write_bytes(b"#!/bin/sh\necho mine\n" + raw.split(b"\n", 1)[1])
    assert "removed the ddflow pre-commit hook" in E.uninstall(repo)
    assert hook.read_bytes() == b"#!/bin/sh\necho mine\n"


# -- the harness settings entry ----------------------------------------------------------


def _install_session_hook(repo: Path) -> str:
    return CH.install_spec(repo, CH.spec("claude", "session-start"))


def _entries(repo: Path) -> list[dict]:
    data = json.loads((repo / ".claude" / "settings.json").read_text())
    return [h for g in data["hooks"]["SessionStart"] for h in g["hooks"] if CH._ours(h)]


def test_a_harness_hook_command_is_a_stamped_region_that_still_runs_the_subcommand(repo):
    _install_session_hook(repo)
    (entry,) = _entries(repo)
    cmd = entry["command"]
    assert cmd.startswith("# ddflow:begin hooks/session-start ddflow=")
    assert cmd.rstrip().endswith("# ddflow:end hooks/session-start")
    assert "hooks session-start" in cmd


def test_an_unchanged_harness_hook_is_left_alone(repo):
    _install_session_hook(repo)
    assert "already in" in _install_session_hook(repo)


def test_a_harness_hook_from_before_the_stamp_is_upgraded_keeping_its_other_keys(repo):
    p = repo / ".claude" / "settings.json"
    p.parent.mkdir(parents=True)
    old = {
        "type": "command",
        "command": CH.command(CH.spec("claude", "session-start")),
        "timeout": 9,
    }
    p.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [old]}]}}))
    assert "updated the ddflow SessionStart hook" in _install_session_hook(repo)
    (entry,) = _entries(repo)
    assert entry["timeout"] == 9 and "ddflow:begin hooks/session-start" in entry["command"]


def test_a_hand_edited_harness_hook_is_backed_up_before_it_is_rewritten(repo):
    _install_session_hook(repo)
    p = repo / ".claude" / "settings.json"
    data = json.loads(p.read_text())
    hook = data["hooks"]["SessionStart"][0]["hooks"][0]
    hook["command"] = hook["command"].replace(
        "hooks session-start", "hooks session-start --mine", 1
    )
    p.write_text(json.dumps(data))
    edited = p.read_text()
    assert "edited by hand" in _install_session_hook(repo)
    copies = [c for c in (repo / ".ddflow" / "backups").rglob("settings.json") if c.is_file()]
    assert copies and copies[0].read_text() == edited
    assert "--mine" not in _entries(repo)[0]["command"]


def test_a_harness_hook_with_a_lone_marker_is_refused_not_a_crash(repo):
    p = repo / ".claude" / "settings.json"
    p.parent.mkdir(parents=True)
    cmd = CH.command(CH.spec("claude", "session-start")) + "\n# ddflow:end hooks/session-start\n"
    p.write_text(
        json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": cmd}]}]}})
    )
    before = p.read_text()
    with pytest.raises(CH.SettingsError, match="region markers"):
        _install_session_hook(repo)
    assert p.read_text() == before


def test_an_older_ddflow_does_not_downgrade_a_newer_harness_hook(repo):
    _install_session_hook(repo)
    p = repo / ".claude" / "settings.json"
    data = json.loads(p.read_text())
    hook = data["hooks"]["SessionStart"][0]["hooks"][0]
    hook["command"] = hook["command"].replace(" fmt=1 ", " fmt=99 ", 1)
    p.write_text(json.dumps(data))
    before = p.read_text()
    with pytest.raises(CH.SettingsError, match="upgrade ddflow to >= "):
        _install_session_hook(repo)
    assert p.read_text() == before


# -- the frozen-files block --------------------------------------------------------------

PRE_COMMIT = "repos:\n  - repo: local\n    hooks:\n      - id: other\n        name: other\n        entry: 'true'\n        language: system\n"


def _freeze(repo: Path, rel: str = "a.md"):
    (repo / rel).write_text("x\n")
    (repo / ".pre-commit-config.yaml").write_text(PRE_COMMIT)
    return L.freeze(repo, [rel])


def test_the_frozen_files_block_is_a_stamped_region_holding_the_old_markers(repo):
    _freeze(repo)
    text = (repo / ".pre-commit-config.yaml").read_text()
    assert "# ddflow:begin onboard/frozen-files ddflow=" in text
    assert text.count(L.HOOK_BEGIN) == 1 and text.count(L.HOOK_END) == 1
    assert text.index("ddflow:begin") < text.index(L.HOOK_BEGIN) < text.index(L.HOOK_END)


def test_a_block_from_before_the_stamp_is_wrapped_not_duplicated(repo):
    _freeze(repo)
    cfg = repo / ".pre-commit-config.yaml"
    text = cfg.read_text()
    legacy = text[text.index(L.HOOK_BEGIN) :]
    legacy = legacy[: legacy.index(L.HOOK_END) + len(L.HOOK_END) + 1]
    cfg.write_text(PRE_COMMIT + legacy)
    L.freeze(repo, ["a.md"])
    now = cfg.read_text()
    assert now.count("ddflow:begin onboard/frozen-files") == 1 and now.count(L.HOOK_BEGIN) == 1


def test_an_older_ddflow_does_not_downgrade_a_newer_frozen_block(repo):
    _freeze(repo)
    cfg = repo / ".pre-commit-config.yaml"
    newer = cfg.read_text().replace(" fmt=1 ", " fmt=99 ", 1)
    cfg.write_text(newer)
    (repo / "b.md").write_text("y\n")
    actions = L.freeze(repo, ["a.md", "b.md"])
    refused = [a for a in actions if isinstance(a, Refused)]
    assert refused and "upgrade ddflow to >= " in refused[0]
    assert cfg.read_text() == newer


def test_a_second_block_outside_the_stamped_region_is_named_as_such(repo):
    _freeze(repo)
    cfg = repo / ".pre-commit-config.yaml"
    text = cfg.read_text()
    legacy = text[text.index(L.HOOK_BEGIN) : text.index(L.HOOK_END) + len(L.HOOK_END) + 1]
    cfg.write_text(text + legacy)
    refused = [a for a in L.freeze(repo, ["a.md"]) if isinstance(a, Refused)]
    assert refused and "outside it" in refused[0] and "stamped frozen-files hook" in refused[0]


def test_a_hand_edited_frozen_block_is_backed_up_first(repo):
    _freeze(repo)
    cfg = repo / ".pre-commit-config.yaml"
    edited = cfg.read_text().replace("language: fail", "language: fail  # mine", 1)
    cfg.write_text(edited)
    actions = L.freeze(repo, ["a.md"])
    assert any("edited by hand" in str(a) for a in actions)
    copies = [
        p for p in (repo / ".ddflow" / "backups").rglob(".pre-commit-config.yaml") if p.is_file()
    ]
    assert copies and copies[0].read_text() == edited
