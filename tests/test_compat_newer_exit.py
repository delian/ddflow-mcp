"""A newer ddflow's artifact is a refusal (exit 3, "upgrade ddflow to >= X"), not a failure,
on every path that meets one: onboard freeze and `upgrade --apply`'s hook applier too
(B-compat-newer-exit3, found by roborev in B-uni-compat-artifacts.4-hooks)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.api import onboard as A
from ddflow.services import claudehooks as CH
from ddflow.services import enforce as E
from ddflow.services import legacy as L
from ddflow.services import upgrade_apply as UA

PRE_COMMIT = (
    "repos:\n  - repo: local\n    hooks:\n      - id: other\n        name: other\n"
    "        entry: 'true'\n        language: system\n"
)


def _newer_frozen_block(repo: Path) -> None:
    (repo / "a.md").write_text("x\n")
    cfg = repo / ".pre-commit-config.yaml"
    cfg.write_text(PRE_COMMIT)
    L.freeze(repo, ["a.md"])
    cfg.write_text(cfg.read_text().replace(" fmt=1 ", " fmt=99 ", 1))


def test_onboard_freeze_over_a_newer_frozen_block_is_exit_3(repo, monkeypatch):
    _newer_frozen_block(repo)
    monkeypatch.setattr(A.LG, "imported_files", lambda state: ["a.md"])
    out = A.onboard(repo, stage="legacy", apply=True)
    assert out.exit == 3, out.reason
    assert E.NEWER_HINT in out.reason


def test_onboard_freeze_refusal_for_another_reason_is_still_a_failure(repo, monkeypatch):
    from ddflow.services.adopt import Refused

    monkeypatch.setattr(A.LG, "imported_files", lambda state: ["a.md"])
    monkeypatch.setattr(A.LG, "freeze", lambda repo_, chosen: [Refused("cannot arm the hook")])
    out = A.onboard(repo, stage="legacy", apply=True)
    assert out.exit == 1


def test_upgrade_apply_over_a_newer_git_hook_is_refused_not_failed(repo):
    E.install(repo)
    hook = E.hooks_dir(repo) / "pre-commit"
    hook.write_text(hook.read_text().replace(" fmt=1 ", " fmt=99 ", 1))
    item = {"id": "hooks:dangling:x", "fix": "ddflow hooks install", "paths": []}
    status, detail = UA._apply_hook(repo, item)
    assert status == UA.REFUSED, detail
    assert E.NEWER_HINT in detail


def test_upgrade_apply_over_a_newer_harness_entry_is_refused_not_failed(repo):
    CH.install_spec(repo, CH.spec("claude", "session-start"))
    p = repo / ".claude" / "settings.json"
    p.write_text(p.read_text().replace(" fmt=1 ", " fmt=99 ", 1))
    for item in (
        {
            "id": "hooks:missing:claude:session-start",
            "fix": "ddflow hooks install --claude",
            "paths": [CH.CLAUDE_SETTINGS],
        },
        {
            "id": "hooks:stale",
            "fix": "ddflow hooks install --claude",
            "paths": [CH.CLAUDE_SETTINGS],
        },
    ):
        status, detail = UA._apply_hook(repo, item)
        assert status == UA.REFUSED, (item["id"], status, detail)
        assert E.NEWER_HINT in detail
