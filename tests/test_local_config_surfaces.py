"""Every writer of an operator-specific value targets the machine-local layer.

Operator, 2026-09-29: the critic, roborev and companion configurations stay local to
whoever runs the project; ddflow recommends services and never ships one person's
setup. Decision D-no-own-services-local-dir: `.ddflow/local/{config,gates,reviewers}.toml`
are git-ignored and read last. `reviewers add` / `reviewers detect --write` default to
`.ddflow/local/reviewers.toml` (`--shared` commits deliberately); `config --set` /
`--append-toml` and `ddflow_configure` take `--local` / `local=true`. The human-gate
guards hold on the local layer too: it is read AFTER the committed files, so a local
pipeline that omits a human gate would otherwise delete the operator's checkpoint on
this machine with nothing in any diff.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import review as AR
from ddflow.api import setup as AS
from ddflow.services import review as R

OK = 0

HUMAN_GATE = (
    "[gate.plan_approved]\n"
    'title = "Operator approves the plan"\n'
    "human = true\n"
    'prompt = "Show the operator the plan."\n'
)


def _ignored(repo: Path, rel: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", rel]).returncode == 0


def _committed(repo: Path) -> str:
    p = repo / ".ddflow" / "config.toml"
    return p.read_text() if p.exists() else ""


# -- reviewers: local by default (bug B-reviewers-write-committed) ----------------------


def test_reviewers_add_writes_the_local_layer_not_the_committed_config(repo):
    run_cli(repo, "init")
    before = _committed(repo)
    code, out, err = run_cli(
        repo, "reviewers", "add", "--preset", "ollama", "--base-url", "http://127.0.0.1:9/v1"
    )
    assert code == OK, err
    assert _committed(repo) == before, "a reviewer endpoint was written to the committed config"
    local = repo / ".ddflow" / "local" / "reviewers.toml"
    assert "http://127.0.0.1:9/v1" in local.read_text()
    assert _ignored(repo, ".ddflow/local/reviewers.toml")
    assert "ollama" in {r.name for r in R.load_reviewers(repo)}
    assert "not committed" in out


def test_reviewers_add_shared_commits_deliberately(repo):
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "reviewers", "add", "--preset", "ollama", "--shared")
    assert code == OK, err
    assert "[[reviewer]]" in _committed(repo)
    assert not (repo / ".ddflow" / "local" / "reviewers.toml").exists()


def test_reviewers_detect_write_goes_local_and_shared_goes_committed(repo, monkeypatch):
    monkeypatch.setattr(R, "detect", lambda: [("http://127.0.0.1:9/v1", "x", ["qwen3:8b"])])
    run_cli(repo, "init")
    before = _committed(repo)
    out = AR.reviewers_detect(repo, write=True)
    assert out.data["written"].endswith(".ddflow/local/reviewers.toml")
    assert _committed(repo) == before
    assert _ignored(repo, ".ddflow/local/reviewers.toml")
    tomllib.loads((repo / ".ddflow" / "local" / "reviewers.toml").read_text())

    out = AR.reviewers_detect(repo, write=True, shared=True)
    assert out.data["written"].endswith(".ddflow/config.toml")
    assert "http://127.0.0.1:9/v1" in _committed(repo)


def test_the_local_dir_ignores_itself_without_init(repo):
    """A project whose `.ddflow/.gitignore` predates `local/`, or never ran init, must
    still not commit what a local writer is about to put there."""
    (repo / ".ddflow").mkdir()
    from ddflow.services.configwrite import append_block

    path = append_block(repo, '[[reviewer]]\nname = "m"\nmodel = "x"\n', own="reviewers.toml")
    assert path == repo / ".ddflow" / "local" / "reviewers.toml"
    assert _ignored(repo, ".ddflow/local/reviewers.toml")


# -- config --set / --append-toml --local ------------------------------------------------


def test_config_set_local_writes_the_local_file_and_reports_local(repo):
    run_cli(repo, "init")
    before = _committed(repo)
    code, out, err = run_cli(
        repo, "config", "--local", "--set", "gate.unit_tests.command", "pytest -q -n 48"
    )
    assert code == OK, err
    assert _committed(repo) == before
    local = repo / ".ddflow" / "local" / "config.toml"
    assert "pytest -q -n 48" in local.read_text()
    assert _ignored(repo, ".ddflow/local/config.toml")
    code, out, _ = run_cli(repo, "config", "--filter", "lease.ttl_s")
    assert code == OK
    run_cli(repo, "config", "--local", "--set", "lease.ttl_s", "999")
    _code, out, _ = run_cli(repo, "config", "--filter", "lease.ttl_s")
    assert "999" in out and "[local]" in out


def test_config_set_without_local_still_writes_the_committed_file(repo):
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "config", "--set", "gate.unit_tests.command", "pytest -q")
    assert code == OK, err
    assert 'command = "pytest -q"' in _committed(repo)
    assert not (repo / ".ddflow" / "local" / "config.toml").exists()


def test_config_append_toml_local(repo):
    run_cli(repo, "init")
    before = _committed(repo)
    block = '[[reviewer]]\nname = "lan"\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "m"\n'
    code, _out, err = run_cli(repo, "config", "--local", "--append-toml", block)
    assert code == OK, err
    assert _committed(repo) == before
    assert "lan" in (repo / ".ddflow" / "local" / "config.toml").read_text()
    assert "lan" in {r.name for r in R.load_reviewers(repo)}


def test_mcp_configure_local(repo):
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    before = _committed(repo)
    srv = Server(repo)
    r = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_configure",
                "arguments": {"set": "lease.ttl_s", "value": "777", "local": True},
            },
        }
    )
    assert not r["result"].get("isError"), r
    assert _committed(repo) == before
    assert "777" in (repo / ".ddflow" / "local" / "config.toml").read_text()


def test_the_mcp_tools_advertise_the_local_and_shared_switches(repo):
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    tools = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]
    props = {t["name"]: t["inputSchema"]["properties"] for t in tools["tools"]}
    assert "local" in props["ddflow_configure"]
    assert "shared" in props["ddflow_reviewers_detect"]


# -- the human-gate guards hold on the local layer ---------------------------------------


def _human_repo(repo: Path) -> None:
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(HUMAN_GATE)
    code, _o, err = run_cli(repo, "workflow", "pipeline", "task", "plan_approved,implement,merge")
    assert code == OK, err


def test_a_local_pipeline_that_drops_a_human_gate_is_refused(repo):
    _human_repo(repo)
    out = AS.configure(
        repo, AS.ConfigEdit(set="gates.task_pipeline", value='["implement", "merge"]', local=True)
    )
    assert out.exit != OK, "a local pipeline deleted the operator's checkpoint"
    assert "human-approval gate" in out.reason
    local = repo / ".ddflow" / "local" / "config.toml"
    assert not local.exists() or "task_pipeline" not in local.read_text()


def test_a_local_append_that_drops_a_human_gate_is_refused(repo):
    _human_repo(repo)
    out = AS.configure(
        repo,
        AS.ConfigEdit(append_toml='[gates]\ntask_pipeline = ["implement", "merge"]\n', local=True),
    )
    assert out.exit != OK
    assert "human-approval gate" in out.reason


def test_appending_human_false_is_refused_on_either_layer(repo):
    _human_repo(repo)
    for local in (True, False):
        out = AS.configure(
            repo,
            AS.ConfigEdit(append_toml="[gate.plan_approved]\nhuman = false\n", local=local),
        )
        assert out.exit != OK and "refusing" in out.reason, (local, out.reason)


def test_the_local_set_of_a_human_flag_is_refused(repo):
    _human_repo(repo)
    out = AS.configure(
        repo, AS.ConfigEdit(set="gate.plan_approved.human", value="false", local=True)
    )
    assert out.exit != OK and "refusing" in out.reason


# -- what tells a new user where their services go ---------------------------------------


def test_the_handshake_and_readme_say_services_go_local():
    root = Path(__file__).resolve().parents[1]
    instr = (root / "ddflow" / "templates" / "prompts" / "mcp_instructions.md").read_text()
    assert ".ddflow/local/" in instr and "local=true" in instr
    readme = (root / "README.md").read_text()
    assert "What is committed, and what stays on your machine" in readme
    assert "ddflow config --local --set" in readme


def test_reviewers_add_with_a_launch_table_writes_valid_toml(repo):
    """Bug B-reviewers-add-launch-json: the ollama preset's `launch` dict was written
    with json.dumps -- `{"command": ...}` is JSON, not TOML -- and the file stopped
    parsing, taking every later reviewer read down with it."""
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "reviewers", "add", "--preset", "ollama")
    assert code == OK, err
    (rv,) = [r for r in R.load_reviewers(repo) if r.name == "ollama"]
    assert isinstance(rv.launch, dict) and rv.launch.get("command")


def test_a_local_edit_that_keeps_the_human_gate_is_allowed(repo):
    _human_repo(repo)
    out = AS.configure(
        repo,
        AS.ConfigEdit(
            set="gates.task_pipeline", value='["plan_approved", "implement", "merge"]', local=True
        ),
    )
    assert out.exit == OK, out.reason
    out = AS.configure(repo, AS.ConfigEdit(set="lease.ttl_s", value="999", local=True))
    assert out.exit == OK, out.reason
