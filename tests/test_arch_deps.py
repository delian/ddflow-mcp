"""Dependency-census tests and ratchet (ADP-census).

`ddflow/services/archdeps.py` reports every external dependency family -- third-party
packages and the stdlib families http, sqlite3, tomllib, shutil, subprocess, fsio
(tempfile/fcntl), hashlib and clock-env (time/uuid/random/os.environ) -- with its
importing modules, its declared home and the violations (importers outside the home).

These tests pin two things:

* the declared homes match the import-linter home contracts that already exist
  (subprocess, fsio, hashlib, tomlkit), and
* the ratchet: a family's violations equal the committed ``BASELINE``. A NEW importer
  outside a family's declared home makes a set GROW and fails; the baseline only shrinks,
  and a lower one is written in the same change.

The fake (`FakeArchDepsCensus`) stands in for the AST scan where the scan itself is not
what is under test, so the api layer is testable without touching the tree.
"""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from pathlib import Path

import pytest

from ddflow.services import archdeps as A

ROOT = Path(__file__).resolve().parents[1]

# Committed baseline of each family's violations, keyed by family name. Entries only
# shrink: when a family moves behind a port (an ADP-* task), its importers leave this
# table as they gain a declared home. A NEW importer outside a home grows a set and fails.
BASELINE: dict[str, tuple[str, ...]] = {
    "http": (
        "ddflow.infra.hostinfo",
        "ddflow.infra.release_index",
        "ddflow.services.docscheck",
        "ddflow.services.doctypes.rule_content",
        "ddflow.services.install_info",
        "ddflow.services.review_keys",
        "ddflow.services.upstream_delivery",
    ),
    "sqlite3": (
        "ddflow.infra.docindex",
        "ddflow.infra.store._fingerprint",
        "ddflow.infra.store._projection",
        "ddflow.infra.store._schema",
        "ddflow.infra.store._search",
        "ddflow.services.completion_ids",
        "ddflow.services.enforce.trailers",
        "ddflow.services.similar",
    ),
    "tomllib": (
        "ddflow.api.harness_drv",
        "ddflow.api.plan",
        "ddflow.api.record_lifecycle",
        "ddflow.api.setup.config",
        "ddflow.config_sections._layers",
        "ddflow.core.eventkinds",
        "ddflow.core.kinddata",
        "ddflow.core.sensitivity",
        "ddflow.infra.tomlcfg",
        "ddflow.services.configwrite._edits",
        "ddflow.services.configwrite._keys",
        "ddflow.services.configwrite._problems",
        "ddflow.services.configwrite._write",
        "ddflow.services.constants_audit",
        "ddflow.services.context_families",
        "ddflow.services.docscheck",
        "ddflow.services.doctypes.spec",
        "ddflow.services.gates.drift",
        "ddflow.services.guidance.fileformat",
        "ddflow.services.harness",
        "ddflow.services.harnessreg",
        "ddflow.services.install_info",
        "ddflow.services.lanes.endpoint",
        "ddflow.services.lanes.launch_template",
        "ddflow.services.legacy",
        "ddflow.services.mcpconfig",
        "ddflow.services.modelreg",
        "ddflow.services.overlay",
        "ddflow.services.pins",
        "ddflow.services.shared_files",
        "ddflow.services.tomldir",
        "ddflow.services.upgrade_apply._appliers",
        "ddflow.services.upgrade_manifest",
    ),
    "shutil": (
        "ddflow.api.chore",
        "ddflow.api.operations",
        "ddflow.infra.forge",
        "ddflow.infra.paths",
        "ddflow.infra.signals",
        "ddflow.infra.worktree",
        "ddflow.services.adopt._launch",
        "ddflow.services.backups",
        "ddflow.services.ci.scratch",
        "ddflow.services.cleanup.actions",
        "ddflow.services.cmdrunner",
        "ddflow.services.companions",
        "ddflow.services.companions.model",
        "ddflow.services.companions.probe",
        "ddflow.services.enforce.hooks",
        "ddflow.services.gates.mutation",
        "ddflow.services.gates.regression_testfix",
        "ddflow.services.lane_headless",
        "ddflow.services.lanes.opencode",
        "ddflow.services.launchers",
        "ddflow.services.repro_facts",
        "ddflow.services.roborev",
        "ddflow.services.roborev_branch",
        "ddflow.services.swarm.lanehooks",
        "ddflow.services.upgrade_dryrun._drive",
        "ddflow.services.upgrade_dryrun._steps",
    ),
    "subprocess": (),
    "fsio": (),
    "hashlib": (),
    "clock-env": (
        "ddflow.api._base",
        "ddflow.api._dedupe",
        "ddflow.api.approvals",
        "ddflow.api.bisect",
        "ddflow.api.briefing",
        "ddflow.api.chore",
        "ddflow.api.cirun",
        "ddflow.api.firstcall",
        "ddflow.api.healthwatch",
        "ddflow.api.items",
        "ddflow.api.jobs",
        "ddflow.api.lane_views",
        "ddflow.api.lifecycle._common",
        "ddflow.api.lifecycle.brief",
        "ddflow.api.lifecycle.claim",
        "ddflow.api.lifecycle.claim_choice",
        "ddflow.api.lifecycle.claim_guard_queued",
        "ddflow.api.lifecycle.claim_guard_reserved",
        "ddflow.api.lifecycle.claim_line",
        "ddflow.api.lifecycle.heartbeat",
        "ddflow.api.lifecycle.reservations",
        "ddflow.api.lifecycle.wait",
        "ddflow.api.metrics",
        "ddflow.api.operations",
        "ddflow.api.reporting.overview",
        "ddflow.api.reporting.recovery",
        "ddflow.api.reporting.recovery_notes",
        "ddflow.api.reporting.views",
        "ddflow.api.reporting.waitcycles",
        "ddflow.api.review.gate",
        "ddflow.api.surf_mcp",
        "ddflow.api.watch",
        "ddflow.api.workflow_state",
        "ddflow.config._core",
        "ddflow.core.clock",
        "ddflow.core.ids",
        "ddflow.core.progress.models",
        "ddflow.core.schedule._plan",
        "ddflow.infra.claimref",
        "ddflow.infra.container",
        "ddflow.infra.fsio",
        "ddflow.infra.git",
        "ddflow.infra.harness_identity",
        "ddflow.infra.localstore",
        "ddflow.infra.log",
        "ddflow.infra.paths",
        "ddflow.infra.proc",
        "ddflow.infra.signals",
        "ddflow.infra.store._projection",
        "ddflow.infra.store._schema",
        "ddflow.infra.treeactivity",
        "ddflow.infra.worktree",
        "ddflow.mcpshim.probe_server",
        "ddflow.services.approvals.audit",
        "ddflow.services.approvals.harness",
        "ddflow.services.approvals.view",
        "ddflow.services.backups",
        "ddflow.services.bisect",
        "ddflow.services.branchsync",
        "ddflow.services.changes",
        "ddflow.services.ci.runner",
        "ddflow.services.ci.scratch",
        "ddflow.services.cleanup.actions",
        "ddflow.services.cleanup.dispose",
        "ddflow.services.cleanup.stranded",
        "ddflow.services.cmdrunner",
        "ddflow.services.compaction",
        "ddflow.services.companions",
        "ddflow.services.companions.probe",
        "ddflow.services.companions.registry",
        "ddflow.services.completion_ids",
        "ddflow.services.docsync",
        "ddflow.services.doctypes.recordquery",
        "ddflow.services.enforce.hooks",
        "ddflow.services.enforce.leasecheck",
        "ddflow.services.engines.probe",
        "ddflow.services.export.write",
        "ddflow.services.flakes",
        "ddflow.services.flowstate",
        "ddflow.services.forbidden",
        "ddflow.services.gateenv",
        "ddflow.services.gates.evidence",
        "ddflow.services.gates.measured",
        "ddflow.services.gates.regression_diag",
        "ddflow.services.gates.runner",
        "ddflow.services.gates.running",
        "ddflow.services.gates.testcmd",
        "ddflow.services.grants",
        "ddflow.services.healthwatch",
        "ddflow.services.identity",
        "ddflow.services.importer",
        "ddflow.services.inbox",
        "ddflow.services.install_info",
        "ddflow.services.jobs",
        "ddflow.services.lane_headless",
        "ddflow.services.lanes.capacity",
        "ddflow.services.lanes.endpoint",
        "ddflow.services.lanes.opencode",
        "ddflow.services.lanes.redraw",
        "ddflow.services.launchers",
        "ddflow.services.leasebreak",
        "ddflow.services.leasekeep",
        "ddflow.services.leases",
        "ddflow.services.logfixture",
        "ddflow.services.mergequeue",
        "ddflow.services.metrics.forecast",
        "ddflow.services.model_stamp",
        "ddflow.services.onboard_tests",
        "ddflow.services.onboard_verify",
        "ddflow.services.orphan_suites",
        "ddflow.services.pins",
        "ddflow.services.pins_merge",
        "ddflow.services.quota",
        "ddflow.services.redact_report",
        "ddflow.services.repro_facts",
        "ddflow.services.reprocheck",
        "ddflow.services.review.engine",
        "ddflow.services.review.runner",
        "ddflow.services.review_keys",
        "ddflow.services.roborev",
        "ddflow.services.schedule",
        "ddflow.services.search",
        "ddflow.services.selfupgrade",
        "ddflow.services.sessions",
        "ddflow.services.shardmarks",
        "ddflow.services.slots",
        "ddflow.services.startcost",
        "ddflow.services.suitelock",
        "ddflow.services.swarm.conformance",
        "ddflow.services.swarm.githook",
        "ddflow.services.swarm.lanehooks",
        "ddflow.services.swarm.lanehooks_check",
        "ddflow.services.ticks",
        "ddflow.services.upgrade_apply._apply",
        "ddflow.services.upgrade_check",
        "ddflow.services.upgrade_dryrun._drive",
        "ddflow.services.upgrade_dryrun._rehearsal",
        "ddflow.services.upgrade_dryrun._steps",
        "ddflow.services.upgrade_start",
        "ddflow.services.upstream_delivery",
        "ddflow.services.waits",
        "ddflow.services.workermcp",
        "ddflow.services.workers",
        "ddflow.surfaces.cli",
        "ddflow.surfaces.cli_pipe",
        "ddflow.surfaces.commands.reporting",
        "ddflow.surfaces.commands.setup",
        "ddflow.surfaces.context",
        "ddflow.surfaces.ifacesnap",
        "ddflow.surfaces.mcp",
        "ddflow.surfaces.mcp_heartbeat",
        "ddflow.surfaces.mcp_protocol",
        "ddflow.surfaces.mcp_upgrade",
        "ddflow.surfaces.worker",
    ),
    "jinja2": (
        "ddflow.services.lanes.endpoint",
        "ddflow.services.lanes.opencode",
        "ddflow.services.prompts",
    ),
    "model2vec": (),
    "radon": ("ddflow.services.complexity_budget",),
    "rapidfuzz": (),
    "tomlkit": (),
}


def _families(census: A.Census) -> dict[str, A.FamilyReport]:
    return {f.family: f for f in census.families}


def test_default_scan_reports_every_family():
    census = A.scan(ROOT)
    names = set(_families(census))
    # every stdlib family in the taxonomy, plus each detected third-party package
    assert names >= set(A.STDLIB_FAMILIES)
    assert {"jinja2", "radon", "rapidfuzz", "tomlkit", "model2vec"} <= names
    assert names == set(BASELINE)
    assert census.unparsed == ()


def test_violations_are_importers_outside_the_home():
    reports = _families(A.scan(ROOT))
    assert reports["subprocess"].home == ("ddflow.infra.proc", "ddflow.infra.git")
    assert reports["subprocess"].violations == ()
    assert reports["fsio"].home == ("ddflow.infra.fsio",)
    assert reports["hashlib"].home == ("ddflow.adapters.digest",)
    assert reports["tomlkit"].home == ("ddflow.infra.tomlcfg",)
    assert reports["http"].home == ("ddflow.adapters.http",)
    assert "ddflow.adapters.http" in reports["http"].importers
    assert "ddflow.adapters.http" not in reports["http"].violations


def test_ratchet_violations_match_baseline():
    actual = _families(A.scan(ROOT))
    unknown = set(actual) - set(BASELINE)
    assert not unknown, f"new dependency family not in the committed baseline: {sorted(unknown)}"
    for name, base in BASELINE.items():
        got = set(actual[name].violations)
        want = set(base)
        if got != want:
            grew = sorted(got - want)
            removed = sorted(want - got)
            assert not grew, f"{name}: NEW importer(s) outside its declared home: {grew}"
            assert not removed, f"{name}: lower its baseline by removing {removed}"


def test_fake_returns_the_reports_it_is_given():
    rows = (
        A.FamilyReport(
            "sqlite3", ("sqlite3",), ("ddflow.infra.docindex",), ("ddflow.infra.docindex",)
        ),
    )
    fake = FakeArchDepsCensus(A.Census(rows))
    assert fake.scan(ROOT).families == rows


class FakeArchDepsCensus:
    """The fake: returns a prepared census, so callers of the port are testable without
    scanning the real tree."""

    def __init__(self, census: A.Census) -> None:
        self._census = census

    def scan(self, repo: Path) -> A.Census:
        return self._census


def test_unparsable_file_is_reported_not_skipped(tmp_path: Path):
    # a valid module and one that cannot be parsed: the broken one must be reported,
    # never silently treated as importing nothing, and the valid one must still count.
    pkg = tmp_path / "ddflow"
    pkg.mkdir()
    (pkg / "ok.py").write_text("import subprocess\n")
    (pkg / "broken.py").write_text("import subprocess\ndef f(:\n")
    census = A.scan(tmp_path)
    assert census.unparsed == ("ddflow.broken",)
    subprocess_importers = [f.importers for f in census.families if f.family == "subprocess"]
    assert subprocess_importers and subprocess_importers[0] == ("ddflow.ok",)


def test_api_view_carries_families_and_unparsed():
    from ddflow.api import arch_deps as API

    census = A.FamilyReport(
        "hashlib", ("hashlib",), ("ddflow.core.digest",), ("ddflow.core.digest",)
    )
    text = API.render(A.Census((census,), ("ddflow.broken",)))
    assert "hashlib" in text and "ddflow.core.digest" in text and "ddflow.broken" in text

    out = API.archdeps(ROOT)
    assert out.exit == 0
    assert {f["family"] for f in out.data["families"]} == set(BASELINE)
    assert {"home", "importers", "violations", "third_party"} <= set(out.data["families"][0])
    assert out.data["unparsed"] == []


def test_family_report_is_immutable():
    row = A.FamilyReport(
        "fsio", ("tempfile", "fcntl"), ("ddflow.infra.fsio",), ("ddflow.infra.fsio",)
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.importers = ()
    assert replace(row, home=()) != row
