"""The migration registry and runner (B-uni-compat-migrations.1-registry, D-compat).

A toy migration (a legacy marker line in `notes.txt` becomes the current one) is registered in
an isolated registry and run on a fixture project written as an older ddflow left it: it must
be detected, planned, backed up first, applied, verified, idempotent (a second run does
nothing and leaves the bytes alone), and every refusal at registration must hold.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import FORMAT_LEVEL
from ddflow.config import Config
from ddflow.infra.log import EventLog
from ddflow.services import migrations as M

OLD_LINE = "<!-- ddflow:legacy -->"
NEW_LINE = "<!-- ddflow:begin notes -->"


def _detect(ctx: M.Context) -> list[M.Finding]:
    p = ctx.repo / "notes.txt"
    if not p.is_file():
        return []
    return [
        M.Finding(f"notes.txt:{n}", f"legacy marker on line {n}", "notes.txt")
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if line == OLD_LINE
    ]


def _plan(ctx: M.Context, found: list[M.Finding]) -> list[M.Change]:
    return [M.Change("notes.txt", f"rewrite {len(found)} legacy marker(s)")]


def _apply(ctx: M.Context, found: list[M.Finding]) -> list[M.Corrective]:
    p = ctx.repo / "notes.txt"
    p.write_text(p.read_text().replace(OLD_LINE, NEW_LINE))
    return [("session.note", "", {"text": f"migrated {len(found)} marker(s)", "item": ""})]


def _verify(ctx: M.Context) -> list[str]:
    p = ctx.repo / "notes.txt"
    return ["no current marker"] if p.is_file() and NEW_LINE not in p.read_text() else []


TOY = M.Migration(
    id="toy-marker",
    since_version="0.1.0",
    format_level=1,
    kinds=("docs",),
    title="legacy marker",
    action="rewrite the legacy marker",
    detect=_detect,
    plan=_plan,
    apply=_apply,
    verify=_verify,
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(M, "_REGISTRY", [])


@pytest.fixture
def old(repo: Path) -> Path:
    assert run_cli(repo, "init")[0] == 0
    (repo / "notes.txt").write_text(f"top\n{OLD_LINE}\nbody\n")
    return repo


def _ctx(repo: Path) -> M.Context:
    return M.context(repo, EventLog(repo, "migrator"), Config.load(repo))


def test_registration_refuses_bad_declarations() -> None:
    M.register(TOY)
    for bad, why in (
        (TOY, "already registered"),
        (replace(TOY, id="a", since_version="soon"), "not a version"),
        (replace(TOY, id="b", format_level=FORMAT_LEVEL + 1), "format_level"),
        (replace(TOY, id="c", format_level=0), "format_level"),
        (replace(TOY, id="d", kinds=("bogus",)), "kinds"),
        (replace(TOY, id="e", kinds=()), "kinds"),
        (replace(TOY, id="f", consent="anyone"), "consent"),
    ):
        with pytest.raises(ValueError, match=why):
            M.register(bad)
    assert [m.id for m in M.registry()] == ["toy-marker"]
    with pytest.raises(KeyError):
        M.by_id("nope")


def test_pending_reads_only_and_plans(old: Path) -> None:
    M.register(TOY)
    before = (old / "notes.txt").read_bytes()
    got = M.pending(_ctx(old))
    assert [p.migration.id for p in got] == ["toy-marker"]
    assert [f.key for f in got[0].findings] == ["notes.txt:2"]
    assert got[0].changes == [M.Change("notes.txt", "rewrite 1 legacy marker(s)")]
    assert got[0].files(old) == [old / "notes.txt"]
    assert (old / "notes.txt").read_bytes() == before, "the plan only reads"


def test_run_backs_up_first_applies_verifies_and_is_idempotent(old: Path) -> None:
    M.register(TOY)
    seen: list[tuple[list[Path], str]] = []

    def backup(files: list[Path]) -> str:
        seen.append((files, (files[0]).read_text()))  # the original, before apply
        return "bk-1"

    log = EventLog(old, "migrator")
    out = M.run(old, log, Config.load(old), backup=backup)
    assert [(o.migration, o.status, o.backup) for o in out] == [
        ("toy-marker", "applied", "bk-1")
    ], out
    assert seen == [([old / "notes.txt"], f"top\n{OLD_LINE}\nbody\n")]
    assert (old / "notes.txt").read_text() == f"top\n{NEW_LINE}\nbody\n"
    assert any(e.kind == "session.note" for e in log.read_all())
    after = (old / "notes.txt").read_bytes()
    n_events = len(log.read_all())
    assert M.run(old, log, Config.load(old), backup=backup) == [], "a second run does nothing"
    assert len(seen) == 1 and (old / "notes.txt").read_bytes() == after
    assert len(log.read_all()) == n_events and M.pending(_ctx(old)) == []


def test_failed_backup_writes_nothing(old: Path) -> None:
    M.register(TOY)
    before = (old / "notes.txt").read_bytes()

    def broken(files: list[Path]) -> str:
        raise OSError("disk full")

    out = M.run(old, EventLog(old, "migrator"), Config.load(old), backup=broken)
    assert [o.status for o in out] == ["failed"] and "nothing was changed" in out[0].detail
    assert (old / "notes.txt").read_bytes() == before


def test_a_migration_that_does_not_verify_is_failed_not_applied(old: Path) -> None:
    M.register(replace(TOY, apply=lambda ctx, found: []))  # applies nothing
    out = M.run(old, EventLog(old, "migrator"), Config.load(old))
    assert out[0].status == "failed" and out[0].problems == [
        "legacy marker on line 2",
        "no current marker",
    ]


def test_unavailable_detector_is_never_clean(old: Path) -> None:
    def cannot(ctx: M.Context) -> list[M.Finding]:
        raise M.Unavailable("no git")

    M.register(replace(TOY, detect=cannot))
    got = M.pending(_ctx(old))
    assert got[0].unavailable == "no git" and got[0].findings == []
    out = M.run(old, EventLog(old, "migrator"), Config.load(old))
    assert [(o.status, o.detail) for o in out] == [("unavailable", "could not run: no git")]


def test_consent_and_version_gate_what_runs(old: Path) -> None:
    M.register(replace(TOY, id="op", consent=M.OPERATOR))
    M.register(replace(TOY, id="future", since_version="9.9.9"))
    log, cfg = EventLog(old, "migrator"), Config.load(old)
    assert [p.migration.id for p in M.pending(_ctx(old), running="0.1.0")] == ["op"]
    assert M.run(old, log, cfg, running="0.1.0") == [], "operator consent runs only when named"
    out = M.run(old, log, cfg, ["op"], running="0.1.0")
    assert [o.status for o in out] == ["applied"]
    with pytest.raises(KeyError):
        M.run(old, log, cfg, ["nope"])


def test_default_backup_saves_the_original_first(old: Path) -> None:
    M.register(TOY)
    out = M.run(old, EventLog(old, "migrator"), Config.load(old))
    assert out[0].status == "applied" and out[0].backup
    saved = list(Path(out[0].backup).rglob("notes.txt"))
    assert [p.read_text() for p in saved] == [f"top\n{OLD_LINE}\nbody\n"]


def test_unavailable_after_apply_is_failed_not_unavailable(old: Path) -> None:
    def verify(ctx: M.Context) -> list[str]:
        raise M.Unavailable("tool gone")

    M.register(replace(TOY, verify=verify))
    out = M.run(old, EventLog(old, "migrator"), Config.load(old))
    assert out[0].status == "failed" and "could not be confirmed" in out[0].detail


def test_unavailable_before_apply_writes_is_unavailable(old: Path) -> None:
    def apply(ctx: M.Context, found: list[M.Finding]) -> list[M.Corrective]:
        raise M.Unavailable("git missing")

    M.register(replace(TOY, apply=apply))
    before = (old / "notes.txt").read_bytes()
    out = M.run(old, EventLog(old, "migrator"), Config.load(old))
    assert [o.status for o in out] == ["unavailable"] and "git missing" in out[0].detail
    assert (old / "notes.txt").read_bytes() == before


def test_apply_oserror_is_failed_with_the_error(old: Path) -> None:
    def apply(ctx: M.Context, found: list[M.Finding]) -> list[M.Corrective]:
        raise OSError("read-only file system")

    M.register(replace(TOY, apply=apply))
    out = M.run(old, EventLog(old, "migrator"), Config.load(old))
    assert out[0].status == "failed" and "OSError: read-only file system" in out[0].detail


def test_a_raising_detector_is_unavailable_and_isolated(old: Path) -> None:
    def boom(ctx: M.Context) -> list[M.Finding]:
        raise OSError("unreadable")

    M.register(replace(TOY, id="a-first"))
    M.register(replace(TOY, id="b-boom", detect=boom))
    got = M.pending(_ctx(old))
    assert [(p.migration.id, bool(p.findings), p.unavailable) for p in got] == [
        ("a-first", True, ""),
        ("b-boom", False, "OSError: unreadable"),
    ]
    out = M.run(old, EventLog(old, "migrator"), Config.load(old))
    assert [(o.migration, o.status) for o in out] == [
        ("a-first", "applied"),
        ("b-boom", "unavailable"),
    ]
    assert "OSError: unreadable" in out[1].detail
