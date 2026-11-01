"""B-upgrade-apply-patch-not-honoured: monkeypatching ``UA._apply_config``/``_abs``/
``_writes_config``/``_LAST`` is honored, not silently ignored by the split package.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ddflow.config import Config
from ddflow.services import upgrade_apply as UA
from ddflow.services import upgrade_plan as UP


def test_patching_a_name_on_the_package_reaches_the_applier_that_uses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The split re-exports these names lazily, so ``monkeypatch.setattr(UA, name, ...)``
    re-bound the ALIAS while ``_run``/``touched``/``_in_order`` kept calling their own
    module globals. Each patched name must reach the caller that does the work, exactly
    as it did in the one-file module."""
    seen: list[str] = []

    # The appliers `_run` dispatches are replaced outright: a silent ignore leaves them
    # uncalled. `_abs`/`_writes_config` keep running (record + delegate) so the real paths
    # still execute while the test sees they were consulted.
    real_apply_config = UA._apply_config
    real_abs = UA._abs
    real_writes = UA._writes_config
    monkeypatch.setattr(
        UA, "_apply_config", lambda *a, **k: seen.append("_apply_config") or ("applied", "patched")
    )
    monkeypatch.setattr(
        UA, "_apply_hook", lambda *a, **k: seen.append("_apply_hook") or ("applied", "patched")
    )
    monkeypatch.setattr(
        UA, "_apply_mcp", lambda *a, **k: seen.append("_apply_mcp") or ("applied", "patched")
    )
    monkeypatch.setattr(UA, "_abs", lambda repo, p: seen.append("_abs") or real_abs(repo, p))
    monkeypatch.setattr(
        UA, "_writes_config", lambda item: seen.append("_writes_config") or real_writes(item)
    )
    monkeypatch.setattr(UA, "_LAST", ("config",))

    cfg = Config()
    config_item: dict[str, Any] = {
        "id": "knob_changed:zzz.notreal",
        "key": "zzz.notreal",
        "category": "config",
        "action": UP.AGENT,
        "change": "knob_changed",
        "old": "0",
        "new": "1",
    }
    removed_item: dict[str, Any] = {
        "id": "knob_removed:zzz.gone",
        "key": "zzz.gone",
        "category": "config",
        "action": UP.OPERATOR,
        "change": "knob_removed",
        "set_by": True,
    }
    hook_item: dict[str, Any] = {"id": "hooks:badfix", "category": "hooks"}
    mcp_item: dict[str, Any] = {"id": "mcp:x", "category": "mcp"}
    instructions_item: dict[str, Any] = {
        "id": "instructions:AGENTS.md",
        "path": "AGENTS.md",
        "category": "instructions",
    }

    # `_run` dispatches each applier through the package, not its own global.
    assert UA._run(tmp_path, cfg, config_item, "upgrader") == ("applied", "patched")
    assert UA._run(tmp_path, cfg, hook_item, "upgrader") == ("applied", "patched")
    assert UA._run(tmp_path, cfg, mcp_item, "upgrader") == ("applied", "patched")
    # `_apply_config`'s own `_writes_config` (the knob_removed route) reaches the real applier.
    assert real_apply_config(tmp_path, cfg, removed_item, "upgrader")[0] == "applied"
    # `touched` reads `_writes_config` and `_abs` through the package.
    UA.touched(tmp_path, cfg, config_item)
    UA.touched(tmp_path, cfg, instructions_item)
    # `_in_order` reads `_LAST` through the package (config moves last).
    assert [i["id"] for i in UA._in_order([config_item, instructions_item])] == [
        "instructions:AGENTS.md",
        "knob_changed:zzz.notreal",
    ]

    assert seen.count("_apply_config") == 1
    assert seen.count("_apply_hook") == 1
    assert seen.count("_apply_mcp") == 1
    assert seen.count("_abs") == 1
    assert seen.count("_writes_config") == 2
