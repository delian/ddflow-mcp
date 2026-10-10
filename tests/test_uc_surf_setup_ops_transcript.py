"""The CLI transcript (exit code, stdout, stderr) of the commands the setup/operations
family of the executor slice moves (B-uc-surf-setup-ops): export, ci, bisect, verify, review,
reviewers, workflow, cleanup, cadence, import, external, pins, tests, precommit, onboard,
upgrade, config, prompts, help, adopt, hooks status and init, in one scripted session over a
fresh adopted repository.

The expected transcript (`test_uc_surf_setup_ops_transcript.json`) was recorded from the code
BEFORE the slice and is never regenerated to make the slice pass. Only what differs between
runs is normalised: the repository and package paths, the home directory, timestamps, short
hashes and durations."""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import ddflow
from ddflow.surfaces.cli import main

SCRIPT: tuple[tuple[str, ...], ...] = (
# setup of the sandbox
    ("init",), ("phase","add","P1","--title","Phase one"),
    ("task","add","T1","--phase","P1","--title","First task"),
    ("task","add","T2","--phase","P1","--title","Second task","--needs","T1"),
    # export family
    ("export",), ("--json","export"), ("export","changelog"), ("--json","export","changelog"),
    ("export","nosuch"), ("--json","export","nosuch"), ("export","--all"), ("export","validate"),
    ("--json","export","validate"), ("export","validate","changelog"), ("export","enable","changelog"),
    ("export","enable","changelog","--path","CL.md","--mode","whole"),
    ("export","changelog","--diff"), ("export","changelog","--check"), ("export","changelog","--update","--yes"),
    ("export","changelog","--check"), ("export","disable","changelog"), ("export","ack"), ("--json","export","ack"),
    ("export","eject","changelog"),
    ("export","changelog","--diff","--check"), ("export","--out","X.md"),
    # ci
    ("ci",), ("--json","ci"), ("ci","status"), ("ci","record"), ("ci","record","--result","passed","--stage","gate"),
    ("--json","ci","record","--result","failed","--stage","merge"), ("ci","record","--result","passed","--stage","nosuch"),
    ("ci","run"), ("--json","ci","run"), ("ci","run","--command","true","--ref","HEAD"),
    # bisect
    ("bisect","tests/test_a.py::t","--cmd","true"), ("--json","bisect","tests/test_a.py::t","--cmd","true"),
    ("bisect","x","--cmd","true","--candidates","nosuch.py"),
    # verify
    ("verify",), ("verify","T1"), ("--json","verify","T1"), ("verify","nosuch"), ("verify","--all"), ("--json","verify","--all"),
    ("verify","--phase","P1"), ("verify","--phase","nosuch"), ("verify","T1","--all"), ("verify","--reopen"),
    ("verify","T1","--pack"), ("verify","T1","--pack","--judge"), ("verify","--pack"),
    # review
    ("review",), ("review","T1"), ("--json","review","T1"), ("review","nosuch"), ("review","triage","T1"),
    ("review","triage","T1","--gate","critic","--finding","1","--refuted"),
    ("review","triage","T1","--gate","critic","--finding","1","--refuted","--probe","p","--reason","r"),
    ("review","T1","--gate","nosuch"),
    # reviewers
    ("reviewers",), ("reviewers","list"), ("--json","reviewers","list"), ("reviewers","presets"), ("--json","reviewers","presets"),
    ("reviewers","add"), ("reviewers","add","nosuchpreset"), ("reviewers","approve"),
    ("--json","reviewers","approve"), ("reviewers","approve","nosuch"),
    # workflow
    ("workflow",), ("--json","workflow"), ("workflow","state"), ("--json","workflow","state"),
    ("workflow","pipeline","task","implement,unit_tests,merge"), ("workflow","pipeline","task","implement,unit_tests,merge","--dry-run"),
    ("--json","workflow","pipeline","task","implement,merge"), ("workflow","pipeline","nosuch","a"),
    ("workflow","gate","g1","--command","true","--into","task"), ("workflow","gate","g1","--command","true","--dry-run"),
    ("workflow","gate","g2"), ("--json","workflow","gate","g2","--prompt","p"), ("workflow","gate","bad id","--command","x"),
    ("workflow","gate","g3","--into","task"), ("workflow","gate","g1","--after","nosuch","--into","task"),
    ("workflow","drop","g1"), ("workflow","drop","g1","--dry-run"), ("--json","workflow","drop","g1"), ("workflow","drop","nosuch"),
    ("workflow","drop","g1"), ("workflow",),
    # operations
    ("cleanup",), ("--json","cleanup"), ("cleanup","--apply"), ("cadence",), ("--json","cadence"), ("cadence","--ran","nosuch"),
    ("cadence","--ran","architecture","--note","n"), ("import",), ("--json","import"), ("import","--verify"), ("--json","import","--verify"),
    ("import","--apply"), ("import","--max-tasks","1"), ("external","sync"), ("--json","external","sync"),
    ("pins","AGENTS.md"), ("--json","pins","AGENTS.md"), ("pins","nosuch.md"), ("tests",), ("--json","tests"), ("tests","--item","T1"),
    ("tests","--item","nosuch"), ("precommit",), ("--json","precommit"), ("precommit","--write"), ("precommit","--write"),
    # onboard
    ("onboard",), ("--json","onboard"), ("onboard","status"), ("onboard","preflight"), ("--json","onboard","preflight"),
    ("onboard","legacy"), ("onboard","memory"), ("onboard","test-gate"), ("onboard","verify"), ("onboard","preflight","--apply"),
    ("onboard","preflight","--apply","--accept","nosuch"), ("onboard","nosuch"),
    # setup family
    ("upgrade",), ("--json","upgrade"), ("upgrade","--plan"), ("upgrade","--apply"), ("upgrade","--apply","hooks"),
    ("upgrade","--apply","nosuch"), ("upgrade","--confirm","k"), ("upgrade","--confirm","k","--reason","r"), ("upgrade","--restore"),
    ("upgrade","--restore","nosuch"), ("upgrade","--restore","--plan"), ("upgrade","--backup","bogus"), ("upgrade","--snapshot"),
    ("config",), ("--json","config"), ("config","--explain"), ("--json","config","--explain"), ("config","--filter","review"),
    ("config","review.max_rounds"), ("config","review.max_rounds","3","--local"), ("config","--set","review.max_rounds","4"),
    ("config","--set","nosuch.key","1"), ("config","--append-toml","[review]\nmax_rounds = 5\n"), ("config","--append-toml","[nosuch]\nx=1\n"),

    ("prompts",), ("prompts","list"), ("--json","prompts","list"), ("prompts","show","nosuch"), ("prompts","get","nosuch"),
    ("help",), ("help","workflow"), ("help","nosuch"), ("--json","help"), ("adopt","--agents","claude","--refresh-docs"),
    ("adopt","--agents","nosuch"), ("hooks","status"), ("--json","hooks","status"), ("init",),
)  # fmt: skip

EXPECTED = Path(__file__).with_suffix(".json")
PKG = str(Path(ddflow.__file__).resolve().parent)


def _normalise(text: str, repo: Path) -> str:
    text = text.replace(PKG, "<pkg>")
    text = text.replace(str(repo.resolve()), "<repo>").replace(str(repo), "<repo>")
    text = text.replace(str(Path.home()), "<home>")
    text = text.replace(re.sub(r"[/._]", "-", str(repo.resolve())), "<mangled>")
    text = re.sub(r"(?<=-proj-)[0-9a-f]{6}\b", "<h6>", text)
    text = re.sub(r"\d{4}-\d\d-\d\d[T ][\d:.+Z-]+", "<time>", text)
    text = re.sub(r"\b[0-9a-f]{10,12}\b", "<hex>", text)
    text = re.sub(r"\b\d+\.\d+s\b", "<secs>", text)
    return re.sub(rf"(?<![\d.]){re.escape(ddflow.__version__)}(?!\.?\d)", "<VERSION>", text)


def record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[list]:
    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for k, v in (("user.email", "t@e.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(repo), "config", k, v], check=True)
    (repo / "README.md").write_text("# p\n")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    monkeypatch.chdir(repo)
    for key in [k for k in __import__("os").environ if k.startswith("DDFLOW_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "off")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("NO_COLOR", "1")
    got = []
    for argv in SCRIPT:
        out, err = io.StringIO(), io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(["--repo", str(repo), "--agent", "tx", *argv])
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        got.append(
            [list(argv), code, _normalise(out.getvalue(), repo), _normalise(err.getvalue(), repo)]
        )
    return got


def test_the_setup_and_operations_cli_transcript_is_unchanged(tmp_path, monkeypatch):
    got = record(tmp_path, monkeypatch)
    want = json.loads(EXPECTED.read_text("utf-8"))
    assert len(got) == len(want)
    bad = [
        f"{' '.join(g[0])}:\n got {g[1:]!r}\nwant {w[1:]!r}"
        for g, w in zip(got, want, strict=True)
        if g != w
    ]
    assert not bad, "\n\n".join(bad[:5]) + f"\n({len(bad)} of {len(want)} differ)"
