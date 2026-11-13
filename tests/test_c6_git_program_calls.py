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
