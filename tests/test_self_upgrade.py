"""B-upgrade.9-self-upgrade: `ddflow upgrade --self` (decision D-self-upgrade 3 and 4).

No network and no real installer: the installer is a fake that records its argv. Install
methods are told to `install_method` as facts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow import api as A
from ddflow.config import Config
from ddflow.infra import proc as P
from ddflow.infra.log import EventLog
from ddflow.services import install_info as II
from ddflow.services import selfupgrade as SU
from ddflow.surfaces.mcp import Server

RUNNING = "0.1.9"


class Fake:
    """A runner that records argv and answers from a script."""

    def __init__(self, code: int = 0, error: BaseException | None = None) -> None:
        self.calls: list[list[str]] = []
        self.code, self.error = code, error

    def __call__(self, argv: list[str], timeout_s: float) -> P.Captured:
        self.calls.append(argv)
        if self.error is not None and len(self.calls) == 1:
            return P.Captured(error=self.error)
        if len(self.calls) == 1:  # the installer
            return P.Captured(self.code, "installed\n", "boom" if self.code else "")
        return P.Captured(0, "plan under new code\n", "")


@pytest.fixture
def project(repo: Path) -> Path:
    (repo / ".ddflow").mkdir()
    return repo


def req(**kw) -> SU.Request:
    base = {
        "method": "pip-venv",
        "running": RUNNING,
        "confirm": ["self"],
        "reason": "operator said yes",
    }
    return SU.Request(**{**base, **kw})


def go(project: Path, runner: Fake, cfg: Config | None = None, **kw) -> dict:
    cfg = cfg or Config()
    return SU.run(project, cfg, EventLog(project, "u"), req(**kw), runner=runner)


# --- which command ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "target", "argv"),
    [
        ("uv-tool", "", ["uv", "tool", "upgrade", "ddflow-mcp"]),
        ("uv-tool", "0.2.0", ["uv", "tool", "install", "--force", "ddflow-mcp==0.2.0"]),
        ("pipx", "", ["pipx", "upgrade", "ddflow-mcp"]),
        ("pipx", "0.2.0", ["pipx", "install", "--force", "ddflow-mcp==0.2.0"]),
        ("pip-venv", "", [sys.executable, "-m", "pip", "install", "-U", "ddflow-mcp"]),
        ("pip-venv", "0.2.0", [sys.executable, "-m", "pip", "install", "-U", "ddflow-mcp==0.2.0"]),
    ],
)
def test_each_install_method_chooses_its_command(project, method, target, argv) -> None:
    fake = Fake()
    out = go(project, fake, method=method, target=target)
    assert fake.calls[0] == argv and out["status"] == "upgraded" and out["exit"] == 0


@pytest.mark.parametrize("method", ["uvx", "container", "source", "readonly", "other"])
def test_an_unsupported_method_prints_the_command_and_exits_2(project, method) -> None:
    fake = Fake()
    out = go(project, fake, method=method)
    assert fake.calls == [] and out["exit"] == 2 and out["status"] == "manual"
    assert method in out["text"] and (
        "uv" in out["text"] or "docker" in out["text"] or "git" in out["text"]
    )


def test_dry_run_names_the_command_and_runs_nothing_even_without_consent(project) -> None:
    fake = Fake()
    out = go(project, fake, dry_run=True, confirm=[], reason="")
    assert fake.calls == [] and out["exit"] == 0 and "pip install -U ddflow-mcp" in out["text"]


# --- consent -----------------------------------------------------------------------------


def test_no_consent_is_refused_with_the_proposal(project) -> None:
    fake = Fake()
    for kw in ({"confirm": []}, {"reason": " "}):
        out = go(project, fake, **kw)
        assert out["exit"] == 3 and out["status"] == "refused" and "pip install" in out["text"]
    assert fake.calls == []


def test_the_operator_policy_refuses_an_agent_over_mcp_even_with_consent(project) -> None:
    cfg = Config()
    cfg.upgrade.self = "operator"
    fake = Fake()
    assert go(project, fake, cfg, surface="mcp")["exit"] == 3
    assert go(project, fake, cfg, surface="cli")["exit"] == 0, (
        "a terminal with --yes --reason is the operator"
    )


def test_the_auto_policy_asks_no_consent(project) -> None:
    cfg = Config()
    cfg.upgrade.self = "auto"
    out = go(project, Fake(), cfg, confirm=[], reason="")
    assert out["exit"] == 0


# --- success, failure, rollback --------------------------------------------------------


def test_success_records_the_event_remembers_the_version_and_runs_the_plan(project) -> None:
    fake = Fake()
    out = go(project, fake, target="0.2.0")
    assert "RESTART" in out["text"] and "plan under new code" in out["text"]
    assert fake.calls[1][-2:] == ["upgrade", "--plan"], "the NEW code shows the plan"
    assert fake.calls[1][:3] == [sys.executable, "-m", "ddflow"]
    from ddflow.core.model import fold

    ups = fold(EventLog(project, "u").read_all()).upgrades
    assert [(u["from"], u["to"], u["categories"]) for u in ups] == [(RUNNING, "0.2.0", ["self"])]
    assert ups[0]["confirmed"] == {"self": "operator said yes"}
    assert SU.previous_version(project) == RUNNING


def test_safe_auto_applies_the_non_destructive_categories_with_the_new_code(project) -> None:
    cfg = Config()
    cfg.upgrade.auto = "safe"
    fake = Fake()
    go(project, fake, cfg, target="0.2.0")
    assert fake.calls[2][-3:] == ["upgrade", "--apply", "hooks,instructions"]


@pytest.mark.parametrize(
    "fake", [Fake(code=1), Fake(error=TimeoutError()), Fake(error=OSError("no uv"))]
)
def test_a_failed_installer_leaves_the_old_version_and_says_so(project, fake) -> None:
    out = go(project, fake, target="0.2.0")
    assert (
        out["exit"] == 1
        and out["status"] == "failed"
        and f"ddflow {RUNNING} is still installed" in out["text"]
    )
    assert len(fake.calls) == 1, "no plan, nothing recorded"
    assert SU.previous_version(project) == ""
    from ddflow.core.model import fold

    assert fold(EventLog(project, "u").read_all()).upgrades == []


def test_rollback_reinstalls_the_remembered_version(project) -> None:
    go(project, Fake(), target="0.2.0")
    fake = Fake()
    out = go(project, fake, rollback=True, running="0.2.0")
    assert fake.calls[0][-1] == f"ddflow-mcp=={RUNNING}" and out["exit"] == 0
    assert "rolled back" in out["text"]


def test_rollback_with_nothing_remembered_is_exit_2(project) -> None:
    out = go(project, Fake(), rollback=True)
    assert out["exit"] == 2 and out["status"] == "nothing"


# --- the surfaces ----------------------------------------------------------------------


def test_cli_self_flags_need_self_and_dry_run_works(project) -> None:
    code, _out, err = run_cli(project, "upgrade", "--dry-run")
    assert code == 3 and "only go with --self" in err
    code, out, _err = run_cli(project, "upgrade", "--self", "--dry-run")
    assert code in (0, 2), out  # a source checkout answers 2 with the command


def test_cli_and_mcp_refuse_without_consent_the_same_way(project, monkeypatch) -> None:
    monkeypatch.setattr(II, "install_method", lambda *a, **k: "pip-venv")
    out = A.upgrade(project, selfup=SU.Request())
    assert out.exit == 3 and out.data["status"] == "refused"
    reply = Server(project).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_upgrade", "arguments": {"self": "run"}},
        }
    )
    body = json.loads(reply["result"]["content"][0]["text"])
    assert body["status"] == "refused" and body["method"] == "pip-venv"
    assert "consent" in body["text"] and "confirm" in body["text"]


def test_self_stands_alone(project) -> None:
    out = A.upgrade(project, apply="all", selfup=SU.Request())
    assert out.exit == 3 and "stands alone" in out.reason


def test_the_knob_is_declared() -> None:
    from ddflow.config import KNOB_CHOICES, KNOB_STRICTEST

    assert KNOB_CHOICES["upgrade.self"] == ("ask", "operator", "auto")
    assert KNOB_STRICTEST["upgrade.self"][0] == "operator"
    assert Config().upgrade.self == "ask"


# --- detecting the method --------------------------------------------------------------


def _info(kind: str = "index") -> II.InstallInfo:
    return II.InstallInfo(version=RUNNING, kind=kind, commit=None, is_own_dev_tree=False)


@pytest.mark.parametrize(
    ("prefix", "kind", "kw", "want"),
    [
        ("/home/a/.local/share/uv/tools/ddflow-mcp", "index", {}, "uv-tool"),
        ("/home/a/.local/share/pipx/venvs/ddflow-mcp", "index", {}, "pipx"),
        ("/home/a/.cache/uv/archive-v0/Xyz", "index", {}, "uvx"),
        ("/home/a/.cache/uv/environments-v2/abc", "index", {}, "uvx"),
        ("/home/a/proj/.venv", "index", {}, "pip-venv"),
        ("/usr", "index", {}, "other"),
        ("/home/a/proj/.venv", "index", {"writable": False}, "readonly"),
        ("/home/a/proj/.venv", "index", {"container": True}, "container"),
        ("/home/a/proj/.venv", "source-tree", {}, "source"),
        ("/home/a/proj/.venv", "editable", {}, "source"),
        ("/home/a/proj/.venv", "vcs", {}, "other"),
    ],
)
def test_install_method_is_read_from_the_facts(prefix, kind, kw, want) -> None:
    base = {"container": False, "writable": True, "base_prefix": "/usr"}
    got = II.install_method(_info(kind), prefix=prefix, **{**base, **kw})
    assert got == want
