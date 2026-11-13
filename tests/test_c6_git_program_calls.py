"""C6-git-program-calls: git and dynamic program calls go through the adapters, not the
`ddflow.infra` compat shims, so the interface ratchet sees no grown site in these files."""

from __future__ import annotations

from pathlib import Path

from ddflow.services import archifaces as I

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "arch_baselines" / "arch_interfaces.txt"

FILES = (
    "ddflow/api/knowledge/bugs.py",
    "ddflow/api/lifecycle/pull.py",
    "ddflow/api/pin_files.py",
    "ddflow/services/bug_globs.py",
    "ddflow/services/ci/ruff.py",
    "ddflow/services/premerge.py",
    "ddflow/services/docsonly.py",
    "ddflow/services/file_size.py",
    "ddflow/services/gates/stale_why.py",
    "ddflow/services/leases/recovery.py",
    "ddflow/services/resultcache.py",
    "ddflow/services/revalidate.py",
    "ddflow/adapters/fs.py",
)


def test_program_sites_in_c6_files_do_not_exceed_baseline() -> None:
    now = I.counts(I.scan(ROOT))
    base = I.parse_baseline(BASELINE.read_text())
    grown = [
        f"{kind} {fam} {path}: {n} > {base.get((kind, fam, path), 0)}"
        for (kind, fam, path), n in sorted(now.items())
        if kind == "program"
        and path in FILES
        and fam in ("git", "<dynamic>")
        and n > base.get((kind, fam, path), 0)
    ]
    assert not grown, grown


def _infra_shim_imports(src: str) -> list[str]:
    """Imports of ``ddflow.infra.git`` / ``ddflow.infra.proc`` in any spelling (relative,
    absolute, `import x.y`, `from x import y`)."""
    import ast

    bad = {"git", "proc"}
    found: list[str] = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            if mod.split(".")[-1:] == ["infra"]:
                found += [a.name for a in n.names if a.name in bad]
            if mod.split(".")[-2:] in (["infra", "git"], ["infra", "proc"]):
                found.append(mod)
        elif isinstance(n, ast.Import):
            found += [
                a.name for a in n.names if a.name in ("ddflow.infra.git", "ddflow.infra.proc")
            ]
    return found


def test_c6_files_import_the_adapters_not_the_infra_shims() -> None:
    back = {
        rel: _infra_shim_imports((ROOT / rel).read_text())
        for rel in FILES
        if _infra_shim_imports((ROOT / rel).read_text())
    }
    assert not back, back


def test_shim_import_detector_sees_every_spelling() -> None:
    for src in (
        "from ..infra import git as G",
        "from ...infra import proc",
        "from ddflow.infra import git as G",
        "from ddflow.infra.proc import run",
        "import ddflow.infra.git",
    ):
        assert _infra_shim_imports(src), src
    assert not _infra_shim_imports("from ..adapters import git as G")


def test_dynamic_program_home_is_only_the_runner_and_the_which_port() -> None:
    _task, homes = I.home_of("program", "<dynamic>")
    assert set(homes) == {"ddflow.infra.proc", "ddflow.infra.git", "ddflow.adapters.fs"}
