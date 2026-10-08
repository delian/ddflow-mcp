"""`ddflow upgrade` plans and applies registered migrations (B-uni-compat-migrations.2-wire).

A toy migration is registered in an isolated registry and driven through the real plan and
apply over a project a real ddflow 0.1.3 built: it is listed in the plan with the files it
would rewrite, applied per category with a backup of those files first, recorded in
`upgrade.applied`, idempotent, and an operator-consent one waits for `--confirm`.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ddflow.api._base import _load
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import migrations as MG
from ddflow.services import upgrade_apply as UA
from ddflow.services import upgrade_plan as UP

OLD = Path(__file__).parent / "fixtures" / "releases" / "0.1.3" / "project"
LEGACY = "<!-- ddflow:legacy -->"
CURRENT = "<!-- ddflow:begin notes -->"
FILE = "notes.txt"


def _detect(ctx: MG.Context) -> list[MG.Finding]:
    p = ctx.repo / FILE
    if not p.is_file() or LEGACY not in p.read_text():
        return []
    return [MG.Finding(f"{FILE}:legacy", "legacy marker", FILE)]


def _plan(ctx: MG.Context, found: list[MG.Finding]) -> list[MG.Change]:
    return [MG.Change(FILE, "replace the legacy marker")]


def _apply(ctx: MG.Context, found: list[MG.Finding]) -> list[MG.Corrective]:
    p = ctx.repo / FILE
    p.write_text(p.read_text().replace(LEGACY, CURRENT))
    return []


def _verify(ctx: MG.Context) -> list[str]:
    return []


TOY = MG.Migration(
    id="toy-marker",
    since_version="0.1.0",
    format_level=1,
    kinds=("docs",),
    title="legacy marker",
    action="replace the legacy marker",
    detect=_detect,
    plan=_plan,
    apply=_apply,
    verify=_verify,
)


@pytest.fixture
def old(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "old"
    shutil.copytree(OLD, r)
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    for k, v in (("user.email", "t@e.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(r), "config", k, v], check=True)
    (r / FILE).write_text(f"top\n{LEGACY}\nbody\n")
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "old"], check=True)
    monkeypatch.setattr(MG, "_REGISTRY", [])
    return r


def go(repo: Path, categories: Any = None, **kw: Any) -> dict[str, Any]:
    log, cfg, st = _load(repo, "upgrader")
    return UA.apply(repo, log, cfg, st, categories=categories, agent="upgrader", **kw)


def plan(repo: Path) -> dict[str, Any]:
    log, cfg, st = _load(repo, "upgrader")
    return UP.build(repo, log, cfg, st)


def upgrades(repo: Path) -> list[dict[str, Any]]:
    return fold(EventLog(repo, "upgrader").read_all(), strict=False).upgrades


def test_the_plan_lists_a_pending_migration_with_the_files_it_would_rewrite(old: Path) -> None:
    assert plan(old)["categories"]["migrations"] == [], "an empty registry has nothing to say"
    MG.register(TOY)
    before = (old / FILE).read_bytes()
    body = plan(old)
    (item,) = body["categories"]["migrations"]
    assert item["id"] == "migration:toy-marker" and item["action"] == UP.AGENT
    assert item["changes"] == [{"path": FILE, "action": "replace the legacy marker"}]
    assert item["kinds"] == ["docs"] and item["since_version"] == "0.1.0"
    text = UP.render(body)
    assert "migrations (1)" in text and f"would: {FILE}: replace the legacy marker" in text
    assert (old / FILE).read_bytes() == before, "the plan only reads"


def test_apply_backs_up_first_records_and_is_idempotent(old: Path) -> None:
    MG.register(TOY)
    out = go(old, "migrations")
    assert out["exit"] == 0 and [r["status"] for r in out["results"]] == ["applied"]
    assert (old / FILE).read_text() == f"top\n{CURRENT}\nbody\n"
    saved = list(Path(out["backup"]).rglob(FILE))
    assert [p.read_text() for p in saved] == [f"top\n{LEGACY}\nbody\n"]
    assert "migration:toy-marker" in upgrades(old)[-1]["items"]
    assert plan(old)["categories"]["migrations"] == []
    again = go(old, "migrations")
    assert again["noop"] is True and again["backup"] == ""
    assert len([u for u in upgrades(old) if "migration:toy-marker" in u["items"]]) == 1


def test_backup_none_writes_no_copy(old: Path) -> None:
    MG.register(TOY)
    out = go(old, "migrations", backup="none")
    assert out["exit"] == 0 and out["backup"] == ""
    assert [r["status"] for r in out["results"]] == ["applied"]
    assert (old / FILE).read_text() == f"top\n{CURRENT}\nbody\n"
    assert not (old / ".ddflow" / "backups").exists() or not any(
        (old / ".ddflow" / "backups").rglob(FILE)
    )


def test_an_operator_consent_migration_waits_for_confirm(old: Path) -> None:
    MG.register(replace(TOY, consent=MG.OPERATOR))
    (item,) = plan(old)["categories"]["migrations"]
    assert item["action"] == UP.OPERATOR
    refused = go(old, "migrations")
    assert refused["exit"] == 3 and refused["refused"] == 1
    assert "--confirm toy-marker" in refused["text"]
    assert LEGACY in (old / FILE).read_text()
    done = go(old, "migrations", confirm={"toy-marker": "reviewed"})
    assert done["exit"] == 0 and CURRENT in (old / FILE).read_text()
    assert upgrades(old)[-1]["confirmed"] == {"toy-marker": "reviewed"}


def test_a_migration_that_fails_verification_is_failed_and_stays_in_the_plan(old: Path) -> None:
    MG.register(replace(TOY, apply=lambda ctx, found: []))
    out = go(old, "migrations")
    assert out["exit"] == 1 and out["results"][0]["status"] == "failed"
    assert [i["id"] for i in plan(old)["categories"]["migrations"]] == ["migration:toy-marker"]


def test_a_file_the_runner_plans_beyond_the_plan_is_saved_too(old: Path) -> None:
    """Earlier steps of a run can change what a migration finds: its extra file is backed up."""
    other = old / "other.txt"
    other.write_text(f"{LEGACY}\n")
    state = {"calls": 0}

    def plan_(ctx: MG.Context, found: list[MG.Finding]) -> list[MG.Change]:
        state["calls"] += 1  # the plan lists FILE; the run (a later detect) also finds other.txt
        return [MG.Change(FILE, "x")] + (
            [MG.Change("other.txt", "y")] if state["calls"] > 1 else []
        )

    def apply_(ctx: MG.Context, found: list[MG.Finding]) -> list[MG.Corrective]:
        for name in (FILE, "other.txt"):
            p = ctx.repo / name
            p.write_text(p.read_text().replace(LEGACY, CURRENT))
        return []

    def detect_(ctx: MG.Context) -> list[MG.Finding]:
        return [
            MG.Finding(n, n, n) for n in (FILE, "other.txt") if LEGACY in (ctx.repo / n).read_text()
        ]

    MG.register(replace(TOY, plan=plan_, apply=apply_, detect=detect_))
    out = go(old, "migrations")
    assert out["exit"] == 0, out
    first = Path(out["backup"])
    extra = [d for d in first.parent.iterdir() if d != first]
    saved = [p.read_text() for d in [first, *extra] for p in d.rglob("other.txt")]
    assert saved == [f"{LEGACY}\n"]


def test_a_raising_detector_does_not_break_the_plan_or_the_other_migrations(old: Path) -> None:
    def boom(ctx: MG.Context) -> list[MG.Finding]:
        raise OSError("unreadable")

    MG.register(TOY)
    MG.register(replace(TOY, id="z-boom", detect=boom))
    items = plan(old)["categories"]["migrations"]
    assert [(i["id"], i["unavailable"]) for i in items] == [
        ("migration:toy-marker", ""),
        ("migration:z-boom", "OSError: unreadable"),
    ]
    out = go(old, "migrations")
    assert out["exit"] == 2 and CURRENT in (old / FILE).read_text()
    assert sorted(r["status"] for r in out["results"]) == ["applied", "unavailable"]
