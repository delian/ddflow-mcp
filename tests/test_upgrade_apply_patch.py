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

    monkeypatch.setattr(
        UA, "_apply_config", lambda *a, **k: seen.append("_apply_config") or ("applied", "patched")
    )
    monkeypatch.setattr(UA, "_abs", lambda repo, p: seen.append("_abs") or Path(repo) / p)
    monkeypatch.setattr(UA, "_writes_config", lambda item: seen.append("_writes_config") or False)
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
    instructions_item: dict[str, Any] = {
        "id": "instructions:AGENTS.md",
        "path": "AGENTS.md",
        "category": "instructions",
    }

    assert UA._run(tmp_path, cfg, config_item, "upgrader") == ("applied", "patched")
    UA.touched(tmp_path, cfg, config_item)
    UA.touched(tmp_path, cfg, instructions_item)
    assert [i["id"] for i in UA._in_order([config_item, instructions_item])] == [
        "instructions:AGENTS.md",
        "knob_changed:zzz.notreal",
    ]

    assert seen.count("_apply_config") == 1
    assert seen.count("_abs") == 1
    assert seen.count("_writes_config") == 1
