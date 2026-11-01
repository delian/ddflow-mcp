"""B-upgrade.4-apply: hand edits to driver docs and rules blocks survive an apply.

Shared fixture and helpers: tests/_upgrade_apply_support.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _upgrade_apply_support import (
    DRIVER,
    go,
    ids,
    tree,
)
from conftest import run_cli
from helpers import upgrade_plan as plan

import ddflow.surfaces.cli  # noqa: F401 -- registers the command table the reference check needs
from ddflow.services import upgrade_apply as UA
from ddflow.services import upgrade_plan as UP

#: Read by `ddflow tests --item` (B81cf6e7dfd): the plan is built from the unreleased manifest
#: fragments, data no import reaches, so a new knob fragment must select these tests.
GOVERNS = ("ddflow/templates/upgrade/**", "ddflow/services/upgrade_manifest.py")


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

    rel = AD.NATIVE_RULES[key].path
    state = next(r for r in AD.rules_status(old) if r.path == rel)
    item = next(i for i in plan(old)["categories"]["instructions"] if i["path"] == rel)

    assert state.newer and state.stamped
    assert item["provenance"] == "newer" and item["action"] == UP.NOTE
    out = go(old, "instructions")
    assert "Claim v2" in path.read_text() and f"instructions:{rel}" not in ids(out, "applied")


def test_a_rules_file_that_is_not_utf8_is_skipped_and_left_whole(old: Path) -> None:
    from ddflow.services import adopt as AD

    _adopted_old(old)
    agents = old / "AGENTS.md"
    raw = b"# caf\xe9 notes\n"
    agents.write_bytes(raw)

    actions = AD.refresh_docs(old, only=["AGENTS.md"], backup=False)

    assert any(isinstance(a, AD.Refused) and "not UTF-8" in a for a in actions)
    assert agents.read_bytes() == raw


def test_a_refusal_from_a_nested_rules_file_is_that_items_and_not_its_namesakes(old: Path) -> None:
    from ddflow.services import adopt as AD

    _adopted_old(old)
    keys = [k for k, r in AD.NATIVE_RULES.items() if r.form == AD.FORM_BLOCK]
    paths = {AD.NATIVE_RULES[k].path for k in keys}
    names = [Path(p).name for p in paths]
    twins = sorted(p for p in paths if names.count(Path(p).name) > 1)
    assert len(twins) >= 2, "the native rules table no longer has two block surfaces sharing a name"
    assert run_cli(old, "adopt", "--agents", ",".join(keys))[0] == 0
    bad, twin = old / twins[0], old / twins[1]
    bad.write_bytes(b"# caf\xe9 not utf-8\n")
    twin.write_text(twin.read_text().replace("Claim before you edit", "Claim sometime"))

    out = go(old, "instructions")

    assert f"instructions:{twins[0]}" in ids(out, "refused")
    assert f"instructions:{twins[1]}" in ids(out, "applied"), "the namesake must be refreshed"
    assert "Claim before you edit" in twin.read_text()
    assert bad.read_bytes() == b"# caf\xe9 not utf-8\n"


def test_an_action_naming_two_items_belongs_to_the_one_written() -> None:
    items = [{"path": "AGENTS.md"}, {"path": ".aider.conf.yml"}]

    assert UA._owner("added AGENTS.md to read: in .aider.conf.yml", items) == ".aider.conf.yml"
    assert UA._owner("updated the managed block in AGENTS.md", items) == "AGENTS.md"
    assert UA._owner("nothing about either", items) == ""
    twins = [{"path": "docs/ddflow/drivers/deltas/replit.md"}, {"path": "replit.md"}]
    assert UA._owner("wrote docs/ddflow/drivers/deltas/replit.md", twins) == twins[0]["path"]
    assert UA._owner("updated the managed block in replit.md", twins) == "replit.md"
    kept = "wrote docs/x/a.md (your edits are kept in a.md.local-edits)"
    assert UA._owner(kept, [{"path": "docs/x/a.md"}, {"path": "a.md"}]) == "docs/x/a.md"
    assert (
        UA._owner(
            "updated the managed block in a.md (your edits are kept in a.md.local-edits)",
            [{"path": "a.md"}],
        )
        == "a.md"
    )
