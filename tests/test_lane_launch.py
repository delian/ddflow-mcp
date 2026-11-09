"""SW-launch: lanes start only from person-approved templates, within caps, budget and hook level."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra import paths
from ddflow.services import approval, modelreg
from ddflow.services.lanes import launch as LN
from ddflow.services.lanes import launch_template as T
from ddflow.services.lanes import opencode as OC
from ddflow.services.swarm import lanehooks as LH
from ddflow.services.swarm import lanehooks_plugin as PL
from ddflow.services.swarm import score
from ddflow.services.swarm import seedpack as SP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lanes_fixture import T0, Log

EXAMPLE = paths.package_dir() / "models" / "routes" / "local.example.toml"
SECRET = "sk-test-0123456789abcdef"
FAKE = """#!{py}
import json, os, sys
if "--version" in sys.argv:
    print("9.9.9"); sys.exit(0)
json.dump({{"argv": sys.argv[1:], "agent": os.environ.get("DDFLOW_AGENT")}}, open({rec!r}, "w"))
print(json.dumps({{"type": "step_start", "sessionID": "s1"}}), flush=True)
"""


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    for v in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LAN_ENDPOINT_KEY", SECRET)
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)


def sh(cwd, *a):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def tree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sh(repo, "git", "init", "-q")
    sh(repo, "git", "config", "user.email", "a@b.c")
    sh(repo, "git", "config", "user.name", "a")
    (repo / ".git" / "info" / "exclude").write_text(".ddflow/\n")
    (repo / "a.txt").write_text("a")
    sh(repo, "git", "add", ".")
    sh(repo, "git", "commit", "-qm", "init")
    wt = tmp_path / "lane-wt"
    sh(repo, "git", "worktree", "add", "-q", "-b", "lane", str(wt))
    return repo, wt, tmp_path / "lane-home"


def tpl(**kw) -> T.Template:
    base = {"name": "ds", "harness": "kilo", "route": "lan-vllm", "family": "deepseek",
            "budget_usd": 1.0, "trust": 1, "price_in": 1.0, "price_out": 2.0}  # fmt: skip
    return T.Template(**{**base, **kw})


def grant(log, subject, digest, human=True):
    log.add("approval.granted", subject, "op", T0, digest=digest, human=human,
            user="op", host="h", token_hash="", note="")  # fmt: skip


def state(*approved: T.Template, plugin=True, extra_tasks=(), live=()):
    log = Log()
    log.task("X-1", "src/a/*.py")
    log.task("X-2", "src/b/*.py", needs="X-1")
    for tid, g in extra_tasks:
        log.task(tid, g)
    for a, tid, g in live:
        log.claim(a, tid, T0, g)
    for t in approved:
        grant(log, t.subject, t.digest())
    if plugin:
        grant(log, LH.consent_subject("kilo"), PL.digest())
    return fold(list(log.events), strict=False)


def cat(tmp_path):
    t = EXAMPLE.read_text().replace('s = "unverified"', 's = "verified"')
    (tmp_path / "models.toml").write_text(t)
    return modelreg.load(local_dir=tmp_path)


def cat_route(tmp_path, model):
    """A catalog whose lane route serves ``model``. The shipped `families.toml` already maps
    a family id to its vendor (claude -> anthropic, deepseek -> deepseek); this puts the
    route the template names behind the model, so the lane scores under that vendor."""
    (tmp_path / "models.toml").write_text(
        '[route.lan-vllm]\n'
        'endpoint = {{ v = {{ kind = "openai", base_url = "http://h:8000/v1", model = "{0}", api_key_env = "LAN_ENDPOINT_KEY" }}, s = "verified", d = "2026-10-30" }}\n'.format(
            model
        )
    )
    return modelreg.load(local_dir=tmp_path)


class Rec:
    def __init__(self):
        self.rows = []

    def append(self, kind, subject, data=None):
        self.rows.append((kind, subject, data))


def request(tmp_path, tree, t=None, st=None, enabled=True, **kw):
    repo, wt, home = tree
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / "kilo"
    p.write_text(FAKE.format(py=sys.executable, rec=str(tmp_path / "record.json")))
    p.chmod(0o755)
    b = OC.select(which=lambda n, path=None: str(d / n) if (d / n).exists() else None)
    t = t or tpl()
    cfg = Config()
    cfg.swarm.launch_enabled = enabled
    st = st if st is not None else state(t)
    args = {"template": t, "item": "X-1", "agent": "lane-7", "repo": repo, "worktree": wt,
            "home": home, "state": st, "cat": cat(tmp_path), "cfg": cfg, "binary": b}  # fmt: skip
    args.update(kw)
    args.setdefault("world", score.World())
    return LN.Request(**args)


# --- the knobs ------------------------------------------------------------------------------


def test_launch_knobs_exist_with_safe_defaults():
    sw = Config().swarm
    assert sw.launch_enabled is False
    assert sw.launch_templates_file.endswith("lane-templates.toml")
    assert sw.launch_max_budget_usd == 5.0


# --- templates: validity, digest, file loading, approval is a person's act -------------------


def test_digest_changes_with_any_field_and_is_stable():
    assert tpl().digest() == tpl().digest()
    for k, v in (("route", "other"), ("budget_usd", 2.0), ("trust", 2), ("min_hooks", "prevent")):
        assert tpl(**{k: v}).digest() != tpl().digest(), k


def test_problems_name_each_defect():
    assert T.problems(tpl()) == []
    bad = T.problems(tpl(harness="claude", budget_usd=0.0, min_hooks="x"))
    assert any("harness" in b for b in bad) and any("budget_usd" in b for b in bad)
    assert any("min_hooks" in b for b in bad)
    assert T.problems(tpl(budget_usd=0.0, local=True)) == []


def test_load_reads_templates_and_ignores_a_file_that_approves_itself(tmp_path):
    f = tmp_path / "t.toml"
    f.write_text(
        '[template.a]\nharness="kilo"\nroute="r"\nfamily="deepseek"\nbudget_usd=1.0\n'
        'approved=true\n[template.b]\nharness="kilo"\nroute="r"\nfamily="x"\nbogus=1\n'
    )
    got, notes = T.load(f)
    assert list(got) == ["a"] and any("approved" in n and "ignored" in n for n in notes)
    assert any("unknown key" in n for n in notes)
    assert not T.approved(state(), got["a"]).ok  # the file's flag approved nothing
    assert T.load(tmp_path / "missing.toml") == ({}, [])


def test_approve_is_refused_under_an_agent_identity_and_records_for_a_person(monkeypatch):
    log = Rec()
    with pytest.raises(approval.ApprovalRefused):
        T.approve(log, tpl(), requested_agent="impl-x")
    assert log.rows == []
    monkeypatch.setattr(approval, "agent_marker", lambda who: "")
    T.approve(log, tpl(), requested_agent="")
    assert log.rows[0][0] == "approval.granted" and log.rows[0][1] == tpl().subject


def test_an_agent_made_grant_or_an_edited_template_is_not_approved():
    t = tpl()
    log = Log()
    log.task("X-1")
    grant(log, t.subject, t.digest(), human=False)
    assert not T.approved(fold(list(log.events), strict=False), t).ok
    assert not T.approved(state(t), tpl(budget_usd=3.0)).ok  # edited after approval


# --- launch: refusals start nothing -----------------------------------------------------------


def test_off_by_default(tmp_path, tree):
    r = LN.launch(request(tmp_path, tree, enabled=False))
    assert isinstance(r, LN.Refused) and "launch_enabled" in r.reasons[0]
    assert not tree[2].exists()


def test_unapproved_template_shows_the_diff_and_starts_nothing(tmp_path, tree):
    r = LN.launch(request(tmp_path, tree, st=state()))
    assert isinstance(r, LN.Refused) and isinstance(r.consent, T.Consent)
    assert "budget_usd" in r.consent.diff and r.consent.digest == tpl().digest()
    assert "never approves" in r.consent.how and not tree[2].exists()
    assert not (tmp_path / "record.json").exists()


def test_budget_ceiling_and_estimate_are_enforced(tmp_path, tree):
    big = tpl(budget_usd=50.0)
    r = LN.launch(request(tmp_path, tree, t=big, st=state(big)))
    assert isinstance(r, LN.Refused) and "launch_max_budget_usd" in r.reasons[0]
    r = LN.launch(request(tmp_path, tree, est_in=2_000_000, est_out=1_000_000))  # 4 USD > 1
    assert isinstance(r, LN.Refused) and any("above the template budget" in x for x in r.reasons)
    noprice = tpl(price_in=None, price_out=None)
    r = LN.launch(
        request(
            tmp_path,
            tree,
            t=noprice,
            st=state(noprice),
        )
    )
    assert isinstance(r, LN.Refused) and any("no verified price" in x for x in r.reasons)
    assert not (tmp_path / "record.json").exists()


def test_scheduler_constraints_apply(tmp_path, tree):
    r = LN.launch(request(tmp_path, tree, item="X-2"))
    assert isinstance(r, LN.Refused) and any("open needs" in x for x in r.reasons)
    r = LN.launch(request(tmp_path, tree, t=tpl(trust=0), st=state(tpl(trust=0))))
    assert isinstance(r, LN.Launched)  # default_min_trust is 0
    r.driver.stop(r.handle)
    LN.OC.cleanup(tree[2])
    req = request(tmp_path, tree)
    req.cfg.swarm.default_min_trust = 2
    assert any("trust" in x for x in LN.launch(req).reasons)
    req = request(tmp_path, tree)
    req.cfg.swarm.max_lanes = 1
    live = score.World(live=(score.Live("Y-1", ("src/y/*",), "claude"),))
    assert any("max_lanes" in x for x in LN.launch(replace(req, world=live)).reasons)
    req = request(
        tmp_path,
        tree,
        cat=cat_route(tmp_path, "deepseek-chat"),
    )
    req.cfg.swarm.family_cap = 1
    same = score.World(live=(score.Live("Y-1", ("src/y/*",), "deepseek"),))
    assert any("family_cap" in x for x in LN.launch(replace(req, world=same)).reasons)
    clash = score.World(live=(score.Live("Y-1", ("src/a/*.py",), "claude"),))
    assert any("overlap" in x for x in LN.launch(replace(req, world=clash)).reasons)


def test_family_cap_counts_the_catalog_vendor_not_the_template_family_id(tmp_path, tree):
    """B60ff757d13: score the lane under the catalog VENDOR, not the template's family id.

    A template for family id ``claude`` (vendor ``anthropic``) must count an existing
    ``anthropic`` live lane against family_cap; scoring it under ``claude`` would see the two
    as different families and undercount (independence could also compare claude with
    anthropic as different)."""
    req = request(
        tmp_path,
        tree,
        t=tpl(family="claude"),
        st=state(tpl(family="claude")),
        cat=cat_route(tmp_path, "claude-sonnet"),
    )
    req.cfg.swarm.family_cap = 1
    same = score.World(live=(score.Live("Y-1", ("src/y/*",), "anthropic"),))
    assert any("family_cap" in x for x in LN.launch(replace(req, world=same)).reasons)


def test_a_lane_whose_estimate_exceeds_the_catalog_window_is_refused(tmp_path, tree):
    """Launch scores the same lane as dispatch, so the catalog window is enforced here too
    (the lane's served window is a hard constraint for the item it starts)."""
    r = LN.launch(request(tmp_path, tree, est_in=100_000))
    assert isinstance(r, LN.Refused) and any("exceeds window" in x for x in r.reasons)


def test_hook_plugin_consent_is_asked_and_leaves_no_files(tmp_path, tree):
    t = tpl()
    r = LN.launch(request(tmp_path, tree, st=state(t, plugin=False)))
    assert isinstance(r, LN.Refused) and isinstance(r.consent, LH.Consent)
    assert not (tree[2] / ".config/kilo/plugin").exists()


def test_min_hooks_above_what_the_lane_gives_refuses(tmp_path, tree, monkeypatch):
    t = tpl(min_hooks="prevent")
    real = OC.prepare

    def weak(*a, **k):
        lp = real(*a, **k)
        hooks = replace(lp.hooks, strength=replace(lp.hooks.strength, level=LH.DETECT))
        return replace(lp, hooks=hooks)

    monkeypatch.setattr(OC, "prepare", weak)
    r = LN.launch(request(tmp_path, tree, t=t, st=state(t)))
    assert isinstance(r, LN.Refused) and "min_hooks" in r.reasons[0]
    assert not (tmp_path / "record.json").exists()


# --- launch: the happy path -------------------------------------------------------------------


def test_approved_template_starts_a_lane_with_the_seed_pack_and_audits(tmp_path, tree):
    log = Rec()
    r = LN.launch(request(tmp_path, tree, log=log))
    assert isinstance(r, LN.Launched), r
    evs = list(r.driver.events(r.handle, idle_s=3))
    assert [e.event for e in evs] == ["started"]
    r.driver.stop(r.handle)
    seen = json.loads((tmp_path / "record.json").read_text())
    assert seen["agent"] == "lane-7"
    assert "ddflow-record" in r.prep.spec.prompt and "X-1" in r.prep.spec.prompt
    assert SECRET not in r.prep.spec.prompt and SECRET not in repr(r)
    ((kind, subject, data),) = log.rows
    assert (kind, subject, data["source"]) == ("session.note", "X-1", "lane-launch")
    assert tpl().digest() in data["text"] and SECRET not in data["text"]
    assert str(tmp_path) not in data["text"]  # no machine path in the shared log


def test_the_seed_pack_uses_the_swarm_knobs(tmp_path, tree, monkeypatch):
    seen = []
    real = SP.Knobs.from_config.__func__
    monkeypatch.setattr(
        SP.Knobs, "from_config", classmethod(lambda c, cfg: seen.append(1) or real(c, cfg))
    )
    req = request(tmp_path, tree)
    req.cfg.swarm.seed_max_decisions = 0
    assert isinstance(LN.launch(req), LN.Launched) and seen


def test_a_lane_that_cannot_start_cleans_up_and_is_refused(tmp_path, tree):
    req = request(tmp_path, tree, binary=OC.Binary("kilo", "/nonexistent/kilo", "1", True))
    r = LN.launch(req)
    assert isinstance(r, LN.Refused)
    assert (
        not list((tree[2] / ".config/kilo/plugin").glob("*"))
        if (tree[2] / ".config/kilo/plugin").exists()
        else True
    )


def test_a_binary_of_another_harness_than_the_approved_one_is_refused(tmp_path, tree):
    other = OC.Binary("opencode", "/x/opencode", "1", True)
    r = LN.launch(request(tmp_path, tree, binary=other))
    assert isinstance(r, LN.Refused) and "approved template's harness" in r.reasons[0]
    assert not (tmp_path / "record.json").exists()


def test_hostile_template_values_are_notes_not_crashes_or_loopholes(tmp_path):
    head = 'harness="kilo"\nroute="r"\nfamily="f"\n'
    f = tmp_path / "t.toml"
    f.write_text(
        f"[template.a]\n{head}budget_usd=nan\n[template.b]\n{head}budget_usd=1.0\ntrust=inf\n"
        f'[template.c]\n{head}budget_usd=0\nlocal="false"\n'
        f"[template.d]\n{head}budget_usd=1.0\nkinds=1\n"
        f'[template.e]\n{head}budget_usd=1.0\nkinds="fix"\n'
        f"[template.f]\n{head}budget_usd=1.0\nmin_hooks=3\n"
    )
    got, notes = T.load(f)
    assert got == {} and len(notes) == 6


def test_a_failing_audit_still_returns_the_running_lane(tmp_path, tree):
    class Broken:
        def append(self, *a, **k):
            raise PermissionError("read-only")

    r = LN.launch(request(tmp_path, tree, log=Broken()))
    assert (
        isinstance(r, LN.Launched)
        and json.loads(r.decision_note)["audit_error"] == "PermissionError"
    )
    r.driver.stop(r.handle)


def test_malformed_template_files_are_notes(tmp_path):
    for body in ('template = "x"\n', "[[template]]\nname = 1\n"):
        (tmp_path / "t.toml").write_text(body)
        got, notes = T.load(tmp_path / "t.toml")
        assert got == {} and notes
    (tmp_path / "t.toml").write_bytes(b"\xff")
    got, notes = T.load(tmp_path / "t.toml")
    assert got == {} and notes


def test_unpriced_template_and_negative_prices_and_bare_tables(tmp_path):
    (tmp_path / "t.toml").write_text(
        '[template.x]\n[template.y]\nharness="kilo"\nroute="r"\nfamily="f"\nbudget_usd=1.0\n'
        "price_in=-1\nprice_out=-1\n"
    )
    got, notes = T.load(tmp_path / "t.toml")
    assert got == {} and any("x: harness is empty" in n for n in notes)
    assert any("y: " in n and "negative" in n for n in notes)


def test_launch_reads_the_conformance_store_and_endpoint_pool(tmp_path, tree):
    from ddflow.services.swarm.conformance import Report
    from ddflow.services.swarm.conformance_io import save

    root = tree[0]
    assert LN.conformance_level(root, "lane-7") == 0
    save(root, Report("lane-7", 3, ()))
    assert LN.conformance_level(root, "lane-7") == 3
    pools = LN.endpoint_pools(cat(tmp_path), "lan-vllm", "", 1, tmp_path / "slots")
    assert pools and pools[0].pool == "ep-lan-vllm" and pools[0].limit == 2


def test_conformance_level_expires_after_max_age(tmp_path, tree):
    from ddflow.services.swarm.conformance import Report
    from ddflow.services.swarm.conformance_io import save

    root = tree[0]
    save(root, Report("lane-7", 3, (), at=1000.0))
    assert LN.conformance_level(root, "lane-7", max_age_s=30, now=1000.0) == 3
    assert LN.conformance_level(root, "lane-7", max_age_s=0, now=999999.0) == 3
    assert LN.conformance_level(root, "lane-7", max_age_s=30, now=2000.0) == 0
