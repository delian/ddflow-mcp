"""The sanitized, reproducible report bundle (D-upstream-reporting (3)).

Fixtures are built from string pieces (nothing private is committed); the real-log test
reads this project's own event log at test time, which the repo-is-generic scan exempts.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.core.events import Event
from ddflow.services import bugreport as B
from ddflow.services import install_info as I
from ddflow.services import redact_report as rr

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / ".ddflow" / "events"

HOME = "/" + "home" + "/" + "someone"
PROJECT = "secretproj"
HOST = "buildbox7"
ADDR = ".".join(["10", "1", "2", "3"])
COMMIT = "ab12cd34" * 5

INSTALL = I.InstallInfo(
    version="9.9.9", kind="vcs", commit=COMMIT, is_own_dev_tree=False, location=None
)

TRACEBACK = f"""Traceback (most recent call last):
  File "{HOME}/src/{PROJECT}/.venv/lib/python3.12/site-packages/ddflow/surfaces/cli.py", line 88, in main
    return args.fn(args, ctx)
  File "{HOME}/src/ddflow/.claude/worktrees/x/ddflow/services/claim.py", line 41, in claim
    raise RuntimeError("no lease for {PROJECT} on {HOST}")
  File "{HOME}/src/{PROJECT}/tools/helper.py", line 7, in helper
    secret_call("tok")
RuntimeError: no lease for {PROJECT} on {HOST}
"""


def _agent(n: int) -> str:
    return f"{HOST}-agent{n}-4f2a"


def _events(n: int = 30) -> list[Event]:
    kinds = ["item.completed", "bug.found", "lease.released", "not.a.real.kind"]
    return [
        Event(kind=kinds[i % 4], subject=f"{PROJECT}-item-{i}", agent=_agent(i % 3), lamport=i)
        for i in range(n)
    ]


def _build(**over):
    kw = {
        "title": f"claim crashes in {PROJECT}",
        "expected": "claim returns a lease",
        "install": INSTALL,
        "provenance": B.Provenance(
            reported_by_agent=_agent(0),
            on_behalf_of_operator="someone",
            session="sess-123",
            detector="agent-judgement",
        ),
        "failure": B.Failure(
            argv=["ddflow", "claim", "B-private-item", "--agent", _agent(0)],
            exit_code=1,
            error_class="RuntimeError",
            traceback=TRACEBACK,
        ),
        "events": _events(),
        "knobs": {"gates.enforce_order": "warn"},
        "candidates": lambda title: [],
        "scrub": B.Scrub(
            hostname=HOST, names=[PROJECT], home=HOME, repo_root=f"{HOME}/src/{PROJECT}"
        ),
        "env": {"python": "3.12.1", "os": "Linux 6.8", "machine": "x86_64"},
    }
    kw.update(over)
    return B.build_bundle(**kw)


def test_traceback_has_no_home_path_and_normalised_frames():
    out = _build()
    text = out.markdown + out.json
    assert HOME not in text and "/home/" not in text
    assert PROJECT not in text and HOST not in text
    assert 'File "ddflow/surfaces/cli.py", line 88, in main' in out.markdown
    assert 'File "ddflow/services/claim.py", line 41, in claim' in out.markdown
    assert "return args.fn(args, ctx)" in out.markdown  # ddflow source lines are public
    assert "secret_call" not in out.markdown  # a foreign frame's source is never sent
    assert 'File "<path>/helper.py", line 7, in helper' in out.markdown


def test_normalise_traceback_directly():
    got = B.normalise_traceback(TRACEBACK)
    assert HOME not in got
    assert got.count('File "ddflow/') == 2


def test_argv_values_are_redacted_flags_kept():
    out = _build()
    assert out.data["command"]["argv"] == ["ddflow", "<arg>", "<arg>", "--agent", "<value>"]
    out = _build(
        failure=B.Failure(
            argv=["ddflow", "task", "add", "my-private-slug"], subcommands={"task", "add"}
        )
    )
    assert out.data["command"]["argv"] == ["ddflow", "task", "add", "<arg>"]
    out = _build(failure=B.Failure(argv=["/x/myproject-run", "-m", "ddflow", "go"]))
    assert out.data["command"]["argv"] == ["<program>", "-m", "ddflow", "<arg>"]
    out = _build(
        failure=B.Failure(
            argv=["/x/bin/ddflow", "bug", "found", "--title=oops", "-q"],
            exit_code=2,
            subcommands={"bug", "found", "claim"},
        ),
    )
    assert out.data["command"]["argv"] == ["ddflow", "bug", "found", "--title=<value>", "-q"]


def test_exit_code_and_class_recorded():
    out = _build()
    assert out.data["command"]["exit_code"] == 1
    assert out.data["command"]["error_class"] == "RuntimeError"
    odd = _build(failure=B.Failure(argv=["ddflow"], error_class="x y\nz" + PROJECT))
    assert PROJECT not in odd.markdown


def test_allowlist_enforced_for_knobs():
    cfg = Config()
    cfg.prompts.mcp_instructions = "PRIVATE-PROMPT"
    cfg.session.redact_extra = ["PRIVATE-EXTRA"]
    cfg.flow.pr_reviewers = ["PRIVATE-REVIEWER"]
    cfg.worktree.root = "/srv/PRIVATE-ROOT"
    knobs = B.knobs_from_config(cfg)
    assert set(knobs) <= set(B.KNOB_ALLOWLIST)
    out = _build(knobs={**knobs, "prompts.mcp_instructions": "PRIVATE-PROMPT", "x.y": "PRIVATE-X"})
    text = out.markdown + out.json
    for secret in (
        "PRIVATE-PROMPT",
        "PRIVATE-EXTRA",
        "PRIVATE-REVIEWER",
        "PRIVATE-ROOT",
        "PRIVATE-X",
    ):
        assert secret not in text
    assert out.data["knobs"]["gates.enforce_order"] == "warn"
    assert set(out.data["knobs"]) <= set(B.KNOB_ALLOWLIST)


def test_every_allowlisted_knob_is_a_scalar_on_the_real_config():
    cfg = Config()
    for dotted in B.KNOB_ALLOWLIST:
        section, _, name = dotted.partition(".")
        value = getattr(getattr(cfg, section), name)
        assert isinstance(value, (bool, int, float, str)), dotted


def test_event_excerpt_is_bounded_known_kinds_and_aliased_agents():
    out = _build()
    rows = out.data["recent_events"]
    assert len(rows) == 20
    assert {r["kind"] for r in rows} <= B.known_kinds() | {"(unknown)"}
    assert "not.a.real.kind" not in out.markdown + out.json
    assert all(re.fullmatch(r"a-\d+", r["agent"]) for r in rows)
    assert HOST not in out.markdown + out.json and "agent1" not in out.markdown + out.json
    # stable: the same input gives the same aliases, and the reporter is one of them
    assert _build().data["recent_events"] == rows
    assert out.data["provenance"]["reported_by_agent"] == "a-1"
    assert rows[-1]["agent"] in {"a-1", "a-2", "a-3"}


def test_provenance_aliases_and_detector():
    out = _build()
    prov = out.data["provenance"]
    assert prov["on_behalf_of_operator"] == "op-1"
    assert prov["detector"] == "agent-judgement"
    assert "someone" not in out.markdown.replace("someone-", "")
    assert re.fullmatch(r"s-[0-9a-f]{8}", prov["session"])
    named = _build(
        provenance=replace(
            B.Provenance(_agent(0), "someone", "sess-123", "agent-judgement"),
            detector="traceback-at-failure",
        )
    )
    assert named.data["provenance"]["detector"] == "traceback-at-failure"
    with pytest.raises(ValueError):
        _build(provenance=B.Provenance(_agent(0), "someone", "s", "Some Name With Spaces"))


def test_install_and_environment_have_no_hostname():
    out = _build()
    assert out.data["install"]["version"] == "9.9.9"
    assert out.data["install"]["kind"] == "vcs"
    assert out.data["install"]["commit"] == COMMIT
    assert out.data["environment"] == {"python": "3.12.1", "os": "Linux 6.8", "machine": "x86_64"}
    assert HOST not in out.markdown


def test_own_dev_tree_true_for_this_repo_false_for_a_fixture_project(tmp_path):
    assert B.own_dev_tree(ROOT) is True
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "someone-elses"\n')
    assert B.own_dev_tree(tmp_path) is False
    assert _build(install=replace(INSTALL, is_own_dev_tree=True)).data["install"]["own_dev_tree"]


def test_title_and_expected_are_redacted_and_bounded():
    out = _build(
        title=f"crash at {HOME}/src/{PROJECT} on {ADDR} mail a@b.example " + "x" * 400,
        expected="expected a lease " + "y" * 5000,
    )
    text = out.markdown + out.json
    for leak in (HOME, PROJECT, ADDR, "a@b.example"):
        assert leak not in text
    assert len(out.data["title"]) <= B.TITLE_MAX and "\n" not in out.data["title"]
    assert len(out.data["expected"]) <= B.EXPECTED_MAX
    assert out.redactions.get("ipv4") and out.redactions.get("email") and out.redactions.get("path")
    assert out.data["redactions"] == dict(out.redactions)


def test_candidates_use_the_title_and_unavailable_is_said():
    seen = []

    def provider(title):
        seen.append(title)
        return [{"id": "B-a", "kind": "bug", "score": 0.61234}]

    out = _build(candidates=provider)
    assert seen == [out.data["title"]] or seen == [f"claim crashes in {PROJECT}"]
    assert out.data["dedupe_candidates"]["status"] == "ok"
    assert out.data["dedupe_candidates"]["items"][0]["score"] == 0.61

    def broken(title):
        raise LookupError("index missing")

    gone = _build(candidates=broken)
    assert gone.data["dedupe_candidates"]["status"] == "unavailable"
    assert "unavailable" in gone.markdown.lower()
    none = _build(candidates=None)
    assert none.data["dedupe_candidates"]["status"] == "not_checked"
    empty = _build(candidates=lambda t: [])
    assert empty.data["dedupe_candidates"]["status"] == "none"


def test_digest_changes_with_any_byte_and_verifies():
    base = _build()
    assert base.digest == _build().digest  # reproducible
    assert base.verify()
    assert f"sha256:{base.digest}" in base.rendered
    variants = [
        _build(title="a different title"),
        _build(expected="something else"),
        _build(install=replace(INSTALL, version="9.9.10")),
        _build(knobs={"gates.enforce_order": "block"}),
        _build(events=_events()[:-1]),
        _build(failure=B.Failure(argv=["ddflow", "claim"], exit_code=3)),
    ]
    assert len({v.digest for v in variants} | {base.digest}) == len(variants) + 1
    # tampering with the rendered text or the JSON is detected
    assert not B.verify_digest(base.rendered.replace("claim returns", "claim RETURNS"), base.json)
    assert not B.verify_digest(base.rendered, base.json.replace("9.9.9", "9.9.8"))
    assert B.verify_digest(base.rendered, base.json)


def test_json_is_canonical_and_matches_the_markdown_digest():
    out = _build()
    data = json.loads(out.json)
    assert data["digest"] == out.digest
    assert out.json == B.render_json({**out.data, "digest": out.digest})


def _leaks(text: str, *, host: str, projects: set[str]) -> list[str]:
    """The same detectors the redaction test uses."""
    left = [f"address {a}" for a in rr.private_addresses(text)]
    if re.search(r"/(?:home|Users)/[\w.-]+", text):
        left.append("home path")
    if host and re.search(rf"(?<![A-Za-z0-9]){re.escape(host)}(?![A-Za-z0-9])", text, re.I):
        left.append("hostname")
    if re.search(r"\b[\w-]+\.(?:lan|local|internal|home\.arpa)(?![\w-]|\.\w)", text, re.I):
        left.append(".lan host")
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text):
        left.append("email")
    for p in projects:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(p)}(?![A-Za-z0-9])", text, re.I):
            left.append(f"project {p}")
    return left


def _events_log() -> list[dict]:
    out = []
    for f in sorted(EVENTS.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def test_bundles_from_real_bug_texts_in_the_logs_leak_nothing():
    log = _events_log()
    bugs = [e for e in log if e.get("kind") == "bug.found"]
    if not bugs:
        pytest.skip("no bug.found events in this checkout's log")
    counts: dict[str, int] = {}
    for e in log:
        head = str(e.get("agent", "")).partition("-")[0]
        if head:
            counts[head] = counts.get(head, 0) + 1
    host = max(counts, key=counts.__getitem__)
    projects = set()
    for e in log:
        for m in re.finditer(
            r"/(?:home|Users)/[\w.-]+/(?:src|git|code|work|projects)/([\w.-]+)", json.dumps(e)
        ):
            projects.add(m.group(1))
    projects = {p for p in projects if len(p) >= 4 and p not in ("ddflow", "ddflow-mcp")} or {
        PROJECT
    }
    dirty = 0
    for e in bugs:
        summary = str(e["data"].get("summary", ""))
        out = _build(
            title=summary[:100],
            expected=summary,
            failure=B.Failure(argv=["ddflow", "x"], exit_code=1, traceback=summary, stderr=summary),
            provenance=B.Provenance(str(e.get("agent", "")), "someone", "s", "agent-judgement"),
            scrub=B.Scrub(hostname=host, names=sorted(projects)),
            events=[
                Event(
                    kind="bug.found",
                    subject=str(e.get("subject", "")),
                    agent=str(e.get("agent", "")),
                )
            ],
        )
        left = _leaks(out.markdown + out.json, host=host, projects=projects)
        assert not left, f"{e.get('subject')}: {left}"
        dirty += sum(out.redactions.values()) > 0
    assert dirty, "real bug texts carry private text; something must have been redacted"


def test_local_candidates_find_a_similar_record_and_an_index_failure_is_unavailable(repo):
    from conftest import run_cli

    from ddflow.infra.log import EventLog

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T-adopt", "--title", "adopt the existing worktree on claim")
    cfg = Config.load(repo)
    log = EventLog(repo, "tester", log_cfg=cfg.log)
    provider = B.local_candidates(repo, cfg, log)
    out = _build(title="claim refuses to adopt an existing worktree", candidates=provider)
    items = out.data["dedupe_candidates"]["items"]
    assert out.data["dedupe_candidates"]["status"] == "ok"
    assert any(c["id"] == "T-adopt" for c in items)

    def broken(_repo=repo):
        raise LookupError("no index")

    unavailable = _build(candidates=lambda t: broken())
    assert unavailable.data["dedupe_candidates"]["status"] == "unavailable"


def test_malformed_candidate_rows_are_unavailable_not_a_crash():
    for rows in ([{"id": "B-a", "score": None}], ["not a mapping"]):
        got = _build(candidates=lambda t, r=rows: r)
        assert got.data["dedupe_candidates"]["status"] == "unavailable"


def test_lone_surrogates_do_not_break_the_bundle():
    out = _build(title="bad \ud800 title", expected="x \udfff y")
    assert out.verify() and "\ud800" not in out.rendered


def test_relative_ddflow_frame_keeps_its_public_source_line():
    got = B.normalise_traceback('  File "ddflow/cli.py", line 10, in main\n    raise SystemExit(1)')
    assert got == '  File "ddflow/cli.py", line 10, in main\n    raise SystemExit(1)'


def test_a_dash_led_value_after_a_flag_is_a_value():
    assert B.redact_argv(["ddflow", "--token", "-hunter2", "-q"]) == [
        "ddflow",
        "--token",
        "<value>",
        "-q",
    ]
