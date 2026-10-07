"""Bug B98650136a8: `reviewers detect --write` and `reviewers add --preset` wrote the
SHIPPED family guess as `family = ...`, and a declared family wins over the project's
`[agent].families` -- so the project's map was overridden for that reviewer for good.

The project here serves a Qwen build it has classified itself ("qwen" -> "house-qwen");
the shipped map would call it "alibaba"."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import run_cli

from ddflow.api import review as AR
from ddflow.services import review as R

MODEL = "Qwen/Qwen3-Coder-30B"
FAMILIES = '\n[agent.families]\nqwen = "house-qwen"\n'


def _project(repo: Path) -> None:
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + FAMILIES)


def _families(repo: Path) -> dict[str, str]:
    return {r.name: r.resolved_family() for r in R.load_reviewers(repo)}


def test_the_shipped_map_would_say_otherwise() -> None:
    assert R.family_of(MODEL) not in ("", "house-qwen")


def test_detect_write_leaves_the_family_to_the_projects_map(repo, monkeypatch) -> None:
    _project(repo)
    monkeypatch.setattr(R, "detect", lambda *a, **k: [("http://127.0.0.1:9/v1", "x", [MODEL])])
    out = AR.reviewers_detect(repo, write=True)
    assert out.ok, out.reason
    assert [row["family"] for row in out.data["found"]] == ["house-qwen"]
    assert "family =" not in out.data["blocks"]
    assert _families(repo) == {"qwen3-coder-30b": "house-qwen"}


def test_a_preset_reviewer_leaves_the_family_to_the_projects_map(repo) -> None:
    _project(repo)
    code, out, err = run_cli(
        repo, "reviewers", "add", "--preset", "vllm", "--name", "house", "--model", MODEL
    )
    assert code == 0, out + err
    assert _families(repo)["house"] == "house-qwen"
