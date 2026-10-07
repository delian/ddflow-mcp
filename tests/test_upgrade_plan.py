"""B-upgrade.3-plan: `ddflow upgrade --plan` (and `doctor --upgrade`, `ddflow_upgrade`).

The plan is read-only and reports by category what upgrading the project to the running
ddflow would change. The old-version case is a project a real ddflow 0.1.3 built
(tests/fixtures/releases/0.1.3); a fresh `init` is the up-to-date one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog, running_version
from ddflow.services import enforce as E
from ddflow.services import launchers as LA
from ddflow.services import upgrade_manifest as UM
from ddflow.services import upgrade_plan as UP

OLD = Path(__file__).parent / "fixtures" / "releases" / "0.1.3" / "project"


@pytest.fixture
def old(tmp_path: Path) -> Path:
    """The 0.1.3-built project, as a git repository with one commit."""
    r = tmp_path / "old"
    shutil.copytree(OLD, r)
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    for k, v in (("user.email", "t@e.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(r), "config", k, v], check=True)
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "old"], check=True)
    return r


def plan(repo: Path, *argv: str) -> tuple[int, dict]:
    code, out, err = run_cli(repo, "--json", *argv or ("upgrade",))
    assert out.strip().startswith("{"), f"no JSON body: {out!r} {err!r}"
    return code, json.loads(out)


def items(body: dict, category: str) -> list[dict]:
    return body["categories"][category]


def test_a_fresh_init_has_an_empty_plan(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == 0

    code, body = plan(repo)

    assert code == 0, body
    assert body["up_to_date"] is True and body["total"] == 0
    assert all(v == [] for v in body["categories"].values())
    code, out, _ = run_cli(repo, "upgrade")
    assert code == 0 and "Up to date" in out


def test_the_plan_against_the_0_1_3_project_lists_what_it_lacks(old: Path) -> None:
    code, body = plan(old)

    assert code == 1, "a plan with items exits 1, the way a diff does"
    assert body["up_to_date"] is False and body["project_version"] == ""
    keys = {i["key"] for i in items(body, "config")}
    # the dedupe and README knobs the brief names
    assert {"dedupe.min_words", "enforce.readme_with_code"} <= keys
    drift = {i["path"]: i for i in items(body, "instructions")}
    assert "docs/ddflow/drivers/implement-phase.md" in drift
    # older than this ddflow: a release's drift, not an edit
    assert drift["docs/ddflow/drivers/implement-phase.md"]["provenance"] == "stale"
    missing = {i["id"] for i in items(body, "hooks")}
    assert "hooks:missing:claude:prompt" in missing
    assert "hooks:missing:claude:session-start" in missing
    assert body["total"] == sum(len(v) for v in body["categories"].values())


def test_a_dangling_pre_commit_binary_is_a_hooks_item(old: Path) -> None:
    E.install(old)
    hook = E.hooks_dir(old) / "pre-commit"
    text = hook.read_text()
    for p in LA.recorded_paths(text):
        text = text.replace(p, "/nonexistent-ddflow-venv" + p)
    hook.write_text(text)

    _code, body = plan(old)

    dangling = [i for i in items(body, "hooks") if i["id"].startswith("hooks:dangling:")]
    assert dangling and "pre-commit" in dangling[0]["summary"]
    assert dangling[0]["fix"] == "ddflow hooks install"
    assert items(body, "mcp") == [], "a git hook is not an MCP entry"


def test_a_dangling_mcp_entry_is_an_mcp_item(old: Path) -> None:
    cfg = json.loads((old / ".mcp.json").read_text())
    cfg["mcpServers"]["ddflow"] = {"command": "/nonexistent/ddflow-bin", "args": []}
    (old / ".mcp.json").write_text(json.dumps(cfg))

    _code, body = plan(old)

    assert [i["id"] for i in items(body, "mcp")] and "/nonexistent/ddflow-bin" in json.dumps(
        items(body, "mcp")
    )


def test_an_operator_set_knob_needs_confirmation_and_an_unset_one_does_not(old: Path) -> None:
    # worktree.max_parallel is set by this project's config; worktree.root is not.
    _code, body = plan(old)
    by_key = {i["key"]: i for i in items(body, "config")}

    set_one = by_key["worktree.max_parallel"]
    assert set_one["action"] == UP.OPERATOR and set_one["set_by"] != ""
    assert "--confirm worktree.max_parallel" in set_one["fix"]
    unset = by_key["worktree.root"]
    assert unset["action"] == UP.AGENT and unset["set_by"] == ""
    assert unset["old"] == "../.ddflow-worktrees" and unset["new"] == ".ddflow/worktrees"
    assert by_key["dedupe.min_words"]["change"] == "knob_added"


def test_a_knob_the_project_already_sets_is_not_announced_as_new(old: Path) -> None:
    run_cli(old, "config", "--set", "dedupe.min_words", "5")

    _code, body = plan(old)

    assert "dedupe.min_words" not in {i["key"] for i in items(body, "config")}


def test_the_plan_writes_nothing(old: Path) -> None:
    before = {
        str(p.relative_to(old)): p.read_bytes()
        for p in old.rglob("*")
        if p.is_file() and ".git/" not in str(p) and ".ddflow/local" not in str(p)
    }

    run_cli(old, "upgrade")
    run_cli(old, "--json", "upgrade")

    after = {
        str(p.relative_to(old)): p.read_bytes()
        for p in old.rglob("*")
        if p.is_file() and ".git/" not in str(p) and ".ddflow/local" not in str(p)
    }
    assert after == before


def test_doctor_upgrade_is_an_alias(old: Path) -> None:
    a = run_cli(old, "--json", "upgrade")
    b = run_cli(old, "--json", "doctor", "--upgrade")
    c = run_cli(old, "upgrade", "--plan")

    assert a[0] == b[0] == 1 and a[1] == b[1]
    assert c[0] == 1 and c[1] == run_cli(old, "upgrade")[1]


def test_the_text_names_each_category_and_the_command_for_each_item(old: Path) -> None:
    code, out, _ = run_cli(old, "upgrade")

    assert code == 1
    for head in ("config (", "instructions (", "hooks ("):
        assert head in out
    assert "plan only; nothing was written" in out
    assert "ddflow hooks install --claude" in out


def test_the_unreleased_changes_are_news_only_to_an_older_project() -> None:
    m = UM.parse(
        'schema_version = 1\n[base]\nversion = "0.1.0"\n[[release]]\nversion = "0.2.0"\n'
        '[[release.change]]\nkind = "knob_added"\nkey = "a.b"\nnew = "1"\n'
        '[[release]]\nversion = "unreleased"\n[[release.change]]\nkind = "feature"\n'
        'key = "f"\nwhy = "w"\nenable = "ddflow f"\n'
    )
    # at the running version: the unreleased change is this very tree's, not news
    assert UP._since("0.2.0", "0.2.0", m) == []
    older = UP._since("0.1.0", "0.2.0", m)
    assert [c.key for c in older] == ["a.b", "f"]
    assert UP.feature_items(older)[0]["fix"] == "ddflow f"


def test_net_changes_collapse_across_releases() -> None:
    def ch(kind: str, v: str, **kw) -> UM.Change:
        return UM.Change(version=v, kind=kind, key="s.k", **kw)

    added_then_changed = [
        ch("knob_added", "0.1.1", new=1, has_new=True),
        ch("knob_changed", "0.1.2", old=1, new=2, has_old=True, has_new=True),
    ]
    assert UP._net(added_then_changed)["s.k"][0] == "knob_added"
    assert UP._net(added_then_changed)["s.k"][3] == 2
    flipped = [
        ch("knob_changed", "0.1.1", old=1, new=2, has_old=True, has_new=True),
        ch("knob_changed", "0.1.2", old=2, new=1, has_old=True, has_new=True),
    ]
    assert UP._net(flipped) == {}


def test_the_project_version_is_the_last_upgrade_else_the_oldest_stamp(repo: Path) -> None:
    run_cli(repo, "init")
    assert run_cli(repo, "session", "start", "--model", "m", "--tool", "t")[0] == 0
    st = fold(EventLog(repo, "a").read_all(), strict=False)
    assert UP.project_version(st, True, "9.9.9") == running_version()  # the stamp of the init
    st.ddflow_versions["0.1.5"] = {"agents": [], "at": "", "install": ""}
    assert UP.project_version(st, True, "9.9.9") == "0.1.5"
    st.upgrades.append({"to": "0.1.9"})
    assert UP.project_version(st, True, "9.9.9") == "0.1.9"
    assert UP.project_version(fold([], strict=False), False, "9.9.9") == "9.9.9"
    assert UP.project_version(fold([], strict=False), True, "9.9.9") == ""


def _change(kind: str, v: str, key: str = "s.k", **kw) -> UM.Change:
    return UM.Change(version=v, kind=kind, key=key, **kw)


def test_a_removed_knob_the_config_still_carries_is_the_operators(repo: Path) -> None:
    """A removed knob is unknown to the schema and so has no source; the config carrying it
    is what says someone wrote it, and removing it needs their confirmation."""
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text("[log]\nzzz_old = 1\n\n[gone_section]\nk = 1\n")
    cfg = Config.load(repo)
    assert "log.zzz_old" in cfg.unknown_knobs and "[gone_section]" in cfg.unknown_knobs

    got = UP.config_items(
        [_change("knob_removed", "0.2.0", "log.zzz_old", old=1, has_old=True)], cfg
    )

    assert [(i["change"], i["action"]) for i in got] == [("knob_removed", UP.OPERATOR)]
    assert got[0]["set_by"] == "config"
    # a knob of a section the schema dropped entirely is carried by `[section]`
    sec = UP.config_items(
        [_change("knob_removed", "0.2.0", "gone_section.k", old=1, has_old=True)], cfg
    )
    assert [i["action"] for i in sec] == [UP.OPERATOR]
    # a project that never carried it is told nothing
    assert (
        UP.config_items([_change("knob_removed", "0.2.0", "zzz.gone", old=1, has_old=True)], cfg)
        == []
    )


def test_a_knob_removed_then_added_back_is_a_changed_default_not_a_new_knob() -> None:
    seq = [
        _change("knob_removed", "0.2.0", old=1, has_old=True),
        _change("knob_added", "0.3.0", new=2, has_new=True),
    ]

    kind, _c, old, new = UP._net(seq)["s.k"]

    assert (kind, old, new) == ("knob_changed", 1, 2)
    assert UP._net([*seq, _change("knob_added", "0.4.0", new=1, has_new=True)]) == {}


def test_a_hook_that_could_not_be_checked_is_said_not_dropped(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddflow.services import claudehooks as CH

    monkeypatch.setattr(CH, "state_spec", lambda repo, h: (None, "settings.json is not JSON"))

    got = UP.hook_items(old)

    unknown = [i for i in got if i["id"].startswith("hooks:unknown:")]
    assert unknown and "not JSON" in unknown[0]["summary"] and unknown[0]["action"] == UP.NOTE
    assert not any(i["id"].startswith("hooks:missing:") for i in got)


def test_apply_is_refused_rather_than_ignored(repo: Path) -> None:
    from ddflow.api import setup as A

    out = A.upgrade(repo, plan=False)

    assert out.exit == 3 and "plan" in out.reason
    assert A.upgrade(repo).exit == 0


def test_a_knob_added_then_removed_inside_the_window_never_reached_the_project() -> None:
    seq = [
        _change("knob_added", "0.2.0", new=1, has_new=True),
        _change("knob_removed", "0.3.0", old=1, has_old=True),
    ]
    assert UP._net(seq) == {}
    back = [*seq, _change("knob_added", "0.4.0", new=1, has_new=True)]
    assert UP._net(back)["s.k"][0] == "knob_added"


def test_over_mcp_plan_defaults_to_the_plan_and_false_is_refused(old: Path) -> None:
    from ddflow.surfaces.mcp import Server

    def call(args: dict) -> dict:
        reply = Server(old).handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ddflow_upgrade", "arguments": args},
            }
        )
        return reply["result"]

    planned = call({})
    refused = call({"plan": False})

    assert json.loads(planned["content"][0]["text"])["total"] > 0
    assert refused["_meta"]["exit"] == 3 and "plan" in json.dumps(refused)
    assert call({"plan": True})["content"] == planned["content"]


def test_the_cli_plan_flag_is_the_default_spelled_out(old: Path) -> None:
    assert run_cli(old, "upgrade", "--plan")[1] == run_cli(old, "upgrade")[1]


def test_a_remedy_that_is_the_apply_step_says_apply_is_not_there_yet(old: Path) -> None:
    _code, body = plan(old)

    fixes = [i["fix"] for i in items(body, "config") if "--apply" in i["fix"]]

    assert fixes and all(f.endswith(UP.NOT_YET) for f in fixes)


def test_a_changed_then_removed_then_added_knob_keeps_the_baseline_default() -> None:
    """The project holds 5 at the baseline; 5 -> 7, removed, re-added at 7 is a change
    FROM 5 -- not from the 7 the removal recorded, which would net to nothing."""
    seq = [
        _change("knob_changed", "0.1.5", old=5, new=7, has_old=True, has_new=True),
        _change("knob_removed", "0.2.0", old=7, has_old=True),
        _change("knob_added", "0.3.0", new=7, has_new=True),
    ]

    kind, _c, old, new = UP._net(seq)["s.k"]

    assert (kind, old, new) == ("knob_changed", 5, 7)
    # a removal after a change reports the baseline value the project held
    assert UP._net(seq[:2])["s.k"][2] == 5
