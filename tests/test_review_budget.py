"""The review-round budget (decision D-review-budget): [review].max_rounds.

A shipped default for every project -- 2 full rounds per gate per item, then a refusal
naming the alternatives -- changeable at every layer. A delta recheck and `review triage`
are never refused: rounds 3+ still find about a quarter of the confirmed defects.
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

import ddflow.api.review as api
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup(repo: Path, tmp_path: Path, review_toml: str = "") -> Path:
    """A command reviewer that finds FINDME and logs every call to seen.txt."""
    seen = tmp_path / "seen.txt"
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        f"printf 'call\\n' >> '{seen}'\n"
        "case \"$in\" in *FINDME*) printf 'FINDING HIGH x.py:1\\nbad\\n\\nSTATUS: FINDINGS 1\\n';; "
        "*) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n' + review_toml
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    (repo / "x.py").write_text("x = 1  # FINDME\n")
    return seen


def _calls(seen: Path) -> int:
    return seen.read_text().count("call") if seen.exists() else 0


def _rounds(repo: Path, gate: str = "critic") -> int:
    return len(
        [
            e
            for e in EventLog(repo).read_all()
            if e.subject == "T1"
            and e.kind.startswith("gate.")
            and e.data.get("gate") == gate
            and (e.data.get("evidence") or {}).get("review_kind") == "full"
        ]
    )


def test_the_default_is_two_rounds_then_refuse_for_every_project():
    cfg = Config.load(env={})
    assert (cfg.review.max_rounds, cfg.review.on_exceed) == (2, "refuse")
    assert cfg.sources["review.max_rounds"] == "default"


def test_the_third_full_round_is_refused_with_the_alternatives(repo, tmp_path):
    seen = _setup(repo, tmp_path)
    for n in (1, 2):
        out = api.review(repo, gate="critic", item="T1")
        assert out.data["outcome"] == "failed", out.reason
        ev = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence
        assert ev["round"] == n and ev["review_kind"] == "full"
    calls = _calls(seen)
    out = api.review(repo, gate="critic", item="T1")
    assert out.exit == REFUSED, out.reason
    for needle in ("2 full", "--delta", "review triage", "--force --reason", "review.max_rounds"):
        assert needle in out.reason, (needle, out.reason)
    assert _calls(seen) == calls, "a reviewer was called for a refused round"
    assert _rounds(repo) == 2, "a refusal must not be recorded as a round"


def test_a_delta_recheck_and_triage_are_never_refused(repo, tmp_path):
    seen = _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    api.review(repo, gate="critic", item="T1")
    assert (
        api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="p").exit == OK
    )
    # a commit that fixes it, then a delta of it
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    (repo / "x.py").write_text("x = 2  # FINDME again\n")
    _git(repo, "commit", "-qam", "fix")
    sha = _git(repo, "rev-parse", "HEAD")
    before = _calls(seen)
    out = api.review(repo, gate="critic", item="T1", commit=sha)
    assert out.exit in (OK, FAIL), out.reason
    assert _calls(seen) > before, "the delta was reviewed"
    ev = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence
    assert ev["review_kind"] == "delta" and ev["rounds"] == 2, "the count rides along"
    # still capped for a full round after the delta
    assert api.review(repo, gate="critic", item="T1").exit == REFUSED


def test_delta_flag_reviews_only_what_changed_since_the_reviewed_head(repo, tmp_path):
    seen = _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    api.review(repo, gate="critic", item="T1")
    nothing = api.review(repo, gate="critic", item="T1", delta=True)
    assert nothing.exit == REFUSED and "nothing changed" in nothing.reason, nothing.reason
    _git(repo, "add", "x.py")
    _git(repo, "commit", "-qm", "work")
    before = _calls(seen)
    out = api.review(repo, gate="critic", item="T1", delta=True)
    assert out.exit == OK and out.data["outcome"] == "failed", out.reason  # FINDME is in it
    assert _calls(seen) > before
    assert _rounds(repo) == 2, "a delta is not a full round"


def test_delta_without_a_recorded_review_is_refused(repo, tmp_path):
    _setup(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1", delta=True)
    assert out.exit == REFUSED and "full review" in out.reason, out.reason


def test_warn_mode_runs_and_says_so(repo, tmp_path):
    _setup(repo, tmp_path, '[review]\non_exceed = "warn"\n')
    for _ in range(3):
        out = api.review(repo, gate="critic", item="T1")
        assert out.exit == OK, out.reason
    assert _rounds(repo) == 3
    assert "WARNING" in out.data["text"] and "max_rounds" in out.data["text"]


def test_zero_is_unlimited(repo, tmp_path):
    _setup(repo, tmp_path, "[review]\nmax_rounds = 0\n")
    for _ in range(4):
        assert api.review(repo, gate="critic", item="T1").exit == OK
    assert _rounds(repo) == 4


def test_force_with_a_reason_is_the_recorded_exception(repo, tmp_path):
    _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    api.review(repo, gate="critic", item="T1")
    bare = api.review(repo, gate="critic", item="T1", force=True)
    assert bare.exit == REFUSED and "--reason" in bare.reason
    out = api.review(repo, gate="critic", item="T1", force=True, reason="operator asked")
    assert out.exit == OK, out.reason
    assert _rounds(repo) == 3
    ev = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence
    assert ev["budget_forced"] == "operator asked" and ev["round"] == 3


def test_the_budget_is_per_gate(repo, tmp_path):
    _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    api.review(repo, gate="critic", item="T1")
    assert api.review(repo, gate="rubber_duck", item="T1").exit == OK


def test_the_count_survives_a_reclaim_and_a_manual_record(repo, tmp_path):
    _setup(repo, tmp_path)
    run_cli(repo, "claim", "T1")
    api.review(repo, gate="critic", item="T1")
    api.review(repo, gate="critic", item="T1")
    run_cli(repo, "release", "T1")
    run_cli(repo, "claim", "T1")
    run_cli(repo, "gate", "skip", "T1", "critic", "--reason", "trying to reset the count")
    assert api.review(repo, gate="critic", item="T1").exit == REFUSED


def test_the_cli_has_delta_force_and_reason(repo, tmp_path):
    _setup(repo, tmp_path)
    run_cli(repo, "review", "T1", "--gate", "critic")
    run_cli(repo, "review", "T1", "--gate", "critic")
    code, _out, err = run_cli(repo, "review", "T1", "--gate", "critic")
    assert code == REFUSED and "--delta" in err, err
    code, _out, err = run_cli(
        repo, "review", "T1", "--gate", "critic", "--force", "--reason", "why"
    )
    assert code == OK, err


def test_every_layer_changes_it(repo, tmp_path):
    _setup(repo, tmp_path)
    assert Config.load(repo).review.max_rounds == 2
    run_cli(repo, "config", "--set", "review.max_rounds", "3")
    cfg = Config.load(repo)
    assert cfg.review.max_rounds == 3 and cfg.sources["review.max_rounds"] == "file"
    run_cli(repo, "config", "--set", "review.max_rounds", "0", "--local")
    cfg = Config.load(repo)
    assert cfg.review.max_rounds == 0 and cfg.sources["review.max_rounds"] == "local"
    cfg = Config.load(
        repo, env={"DDFLOW_REVIEW_MAX_ROUNDS": "5", "DDFLOW_REVIEW_ON_EXCEED": "warn"}
    )
    assert (cfg.review.max_rounds, cfg.review.on_exceed) == (5, "warn")
    assert cfg.sources["review.on_exceed"] == "env"


def test_it_is_documented_in_config_explain(repo, tmp_path):
    _setup(repo, tmp_path)
    out = run_cli(repo, "config", "--explain", "--filter", "review.")[1]
    assert "review.max_rounds" in out and "review.on_exceed" in out
    assert "--delta" in out and "triage" in out


def test_an_unknown_review_knob_is_skipped_not_fatal(repo, tmp_path):
    _setup(repo, tmp_path, "[review]\nfuture_knob = 1\n")
    cfg = Config.load(repo)
    assert cfg.review.max_rounds == 2


def test_mcp_configure_sets_it_on_both_layers_and_tells_the_operator(repo, tmp_path):
    from ddflow.surfaces.mcp import Server

    _setup(repo, tmp_path)

    def call(**args):
        reply = Server(repo).handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ddflow_configure", "arguments": args},
            }
        )
        return json.dumps(reply)

    shared = call(set="review.max_rounds", value="0")
    assert "operator" in shared.lower(), shared
    assert Config.load(repo).review.max_rounds == 0
    local = call(set="review.on_exceed", value="warn", local=True)
    assert "operator" in local.lower(), local
    cfg = Config.load(repo)
    assert cfg.review.on_exceed == "warn" and cfg.sources["review.on_exceed"] == "local"
    # an unrelated knob carries no such notice
    assert "operator" not in call(set="lease.ttl_s", value="1800").lower()


# -- generality: a feature of ddflow, not of one repository ----------------------------


def test_a_fresh_init_project_has_the_budget_with_no_config_line(repo):
    run_cli(repo, "init")
    text = (repo / ".ddflow" / "config.toml").read_text()
    assert "max_rounds" not in text, "the default must live in the package, not in a file"
    cfg = Config.load(repo)
    assert (cfg.review.max_rounds, cfg.review.on_exceed) == (2, "refuse")
    assert cfg.unknown_knobs == []
    out = run_cli(repo, "config", "--explain", "--filter", "review.")[1]
    assert "review.max_rounds" in out and "[default]" in out


def test_it_holds_under_a_custom_workflow(repo, tmp_path):
    _setup(repo, tmp_path)
    code, _out, err = run_cli(repo, "workflow", "pipeline", "task", "implement,critic,merge")
    assert code == OK, err
    api.review(repo, gate="critic", item="T1")
    api.review(repo, gate="critic", item="T1")
    assert api.review(repo, gate="critic", item="T1").exit == REFUSED


def test_the_shipped_docs_describe_it_and_name_no_repository():
    root = Path(__file__).resolve().parents[1]
    shipped = {
        "ddflow/templates/drivers/implement-phase.md": "max_rounds",
        "ddflow/templates/prompts/help/gates.md": "max_rounds",
        "ddflow/templates/prompts/mcp_instructions.md": "max_rounds",
        "README.md": "review.max_rounds",
    }
    for rel, needle in shipped.items():
        assert needle in (root / rel).read_text(), rel
    from ddflow.config import KNOB_DOCS

    for key in ("review.max_rounds", "review.on_exceed"):
        doc = KNOB_DOCS[key]
        for private in ("ddflow-worktrees", "/home/", "rb-rounds", "bridge-cse"):
            assert private not in doc.lower(), (key, private)
    src = (root / "ddflow" / "api" / "review.py").read_text()
    assert "/home/delian" not in src


# -- round 1 findings of the review of this very change ----------------------------------


def test_a_base_reaching_before_the_reviewed_head_is_a_full_round(repo, tmp_path):
    _setup(repo, tmp_path)
    old = _git(repo, "rev-parse", "HEAD")
    (repo / "z.txt").write_text("later\n")
    _git(repo, "add", "z.txt")
    _git(repo, "commit", "-qm", "later")
    api.review(repo, gate="critic", item="T1")
    api.review(repo, gate="critic", item="T1")
    out = api.review(repo, gate="critic", item="T1", base=old)
    assert out.exit == REFUSED and "--delta" in out.reason, "a wide --base must not dodge the cap"
    out = api.review(repo, gate="critic", item="T1", commit=old)
    assert out.exit == REFUSED, "nor a --commit that is not after the reviewed head"


def test_a_base_after_the_reviewed_head_is_a_delta(repo, tmp_path):
    _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    api.review(repo, gate="critic", item="T1")
    head = _git(repo, "rev-parse", "HEAD")
    (repo / "y.py").write_text("y = 1  # FINDME\n")
    _git(repo, "add", "y.py")
    _git(repo, "commit", "-qm", "fix")
    out = api.review(repo, gate="critic", item="T1", base=head)
    assert out.exit == OK, out.reason
    ev = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence
    assert ev["review_kind"] == "delta"


def test_delta_survives_a_gate_skip_that_replaced_the_record(repo, tmp_path):
    _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    api.review(repo, gate="critic", item="T1")
    run_cli(repo, "gate", "skip", "T1", "critic", "--reason", "x")
    _git(repo, "add", "x.py")
    _git(repo, "commit", "-qm", "work")
    out = api.review(repo, gate="critic", item="T1", delta=True)
    assert out.exit == OK, out.reason  # not "no review on record": the log still knows
    assert api.review(repo, gate="critic", item="T1").exit == REFUSED


def test_a_refusal_carries_how(repo, tmp_path):
    _setup(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1", delta=True)
    assert out.exit == REFUSED and "how" in out.data


def test_the_refusal_does_not_assume_a_cap_of_two(repo, tmp_path):
    _setup(repo, tmp_path, "[review]\nmax_rounds = 1\n")
    api.review(repo, gate="critic", item="T1")
    out = api.review(repo, gate="critic", item="T1")
    assert out.exit == REFUSED and "after the second" not in out.reason


def _configure(repo, **args):
    from ddflow.surfaces.mcp import Server

    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_configure", "arguments": args},
        }
    )
    return json.dumps(reply)


def test_a_quoted_table_header_is_still_reported_and_the_revert_names_the_knob(repo, tmp_path):
    _setup(repo, tmp_path)
    reply = _configure(repo, toml='["review"]\nmax_rounds = 0\non_exceed = "warn"')
    assert "operator" in reply.lower(), reply
    assert "review.max_rounds 2" in reply and "review.on_exceed" in reply and "refuse" in reply
    only = _configure(repo, set='"review".on_exceed', value="refuse", local=True)
    assert "review.on_exceed refuse" in only and "review.max_rounds 2" not in only
    commented = _configure(repo, set="lease.ttl_s", value="1800")
    assert "operator" not in commented.lower()


def test_config_takes_key_and_value_without_set(repo, tmp_path):
    _setup(repo, tmp_path)
    code, out, err = run_cli(repo, "config", "review.max_rounds", "4", "--local")
    assert code == OK, err
    assert Config.load(repo, env={}).review.max_rounds == 4
    assert "review.max_rounds = 4" in out
    code, _out, err = run_cli(repo, "config", "--set", "review.max_rounds", "1")
    assert code == OK, err
    assert Config.load(repo, env={}).review.max_rounds == 4, "local still wins"


def test_set_beside_append_applies_only_the_set_so_only_it_is_reported(repo, tmp_path):
    """Probe for a review finding: `configure` returns after `set`, the append is never
    written, so reporting it would be a false alarm and ignoring it is correct."""
    _setup(repo, tmp_path)
    reply = _configure(repo, set="lease.ttl_s", value="1800", toml="[review]\nmax_rounds = 0")
    assert Config.load(repo, env={}).review.max_rounds == 2, "the append was written"
    assert "operator" not in reply.lower()


def test_a_non_table_review_value_does_not_crash_the_report(repo, tmp_path):
    from ddflow.api.setup import ConfigEdit, report_budget_change
    from ddflow.core import outcome as O

    out = O.ok("config", text="")
    assert report_budget_change(repo, ConfigEdit(append_toml="review = 1"), out) is out


def test_a_ref_inside_the_items_range_is_a_delta_one_before_it_is_full(repo, tmp_path):
    _setup(repo, tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feat")
    mid = ""
    for n in (1, 2):
        (repo / f"f{n}.txt").write_text(f"{n}\n")
        _git(repo, "add", f"f{n}.txt")
        _git(repo, "commit", "-qm", f"f{n}")
        mid = mid or _git(repo, "rev-parse", "HEAD")
    api.review(repo, gate="critic", item="T1")
    api.review(repo, gate="critic", item="T1")
    inside = api.review(repo, gate="critic", item="T1", base=mid)
    assert inside.exit == OK, inside.reason  # strictly inside the item's range: narrower
    wide = api.review(repo, gate="critic", item="T1", base=base)
    assert wide.exit == REFUSED, "the branch point reaches the whole diff"
    # the base branch moved on after the item branched: still the whole diff (roborev)
    _git(repo, "checkout", "-q", "main")
    (repo / "g.txt").write_text("g\n")
    _git(repo, "add", "g.txt")
    _git(repo, "commit", "-qm", "main moved")
    _git(repo, "checkout", "-q", "feat")
    moved = api.review(repo, gate="critic", item="T1", base="main")
    assert moved.exit == REFUSED, "an advanced base branch is not inside the item's line"


def test_config_with_a_lone_value_is_refused_not_ignored(repo, tmp_path):
    _setup(repo, tmp_path)
    code, _out, err = run_cli(repo, "config", "review.max_rounds")
    assert code == FAIL and "KEY VALUE" in err
