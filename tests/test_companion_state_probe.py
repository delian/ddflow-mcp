"""Two things `ddflow companions` said that were not true.

Bug B87d456f1cd: a `cli` companion with a leftover MCP config entry, and nothing on the
PATH, reported state `registered`. `usable` and `gate_coverage` already judge a cli
companion by INSTALLED -- registration is a state it cannot reach -- while `state` still
read the config file first, so the report showed `[x] registered for: claude` beside a
tool that is not there.

Bug B40760556f6: the "installed (...)" detail took the first line of the probe's output,
whatever it was. `docker image inspect` prints JSON, so codeguide read `installed ([)`;
an `npx` probe whose stdout is empty leads its stderr with npm's own config warnings,
so memory and sequential read `installed (npm warn Unknown user config "email"...)`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import companions as CO
from ddflow.surfaces.commands.setup import _companion_lines


def _companion(**over) -> CO.Companion:
    spec = {
        "id": "probe_target",
        "title": "Probe target",
        "detect": ["python3", "-c", "pass"],
        "install": "pip install nothing",
        "gates": ["rules"],
    }
    spec.update(over)
    return CO.Companion(**spec)


def _ran(monkeypatch, stdout: str, stderr: str = "") -> None:
    class Done:
        returncode = 0

    Done.stdout, Done.stderr = stdout, stderr
    monkeypatch.setattr(CO.P, "run", lambda *a, **k: Done())


# -- B87d456f1cd: a cli companion is never "registered" -----------------------------------


def test_a_missing_cli_companion_with_a_leftover_mcp_entry_is_missing():
    st = CO.Status(_companion(kind="cli"), installed=False, registered_in=["claude"])
    assert st.state == "missing", st.state
    assert st.usable is False


def test_an_installed_cli_companion_with_a_leftover_mcp_entry_is_installed():
    st = CO.Status(_companion(kind="cli"), installed=True, registered_in=["claude"])
    assert st.state == "installed", st.state


def test_an_unprobed_cli_companion_with_a_leftover_mcp_entry_is_unknown():
    st = CO.Status(_companion(kind="cli"), installed=None, registered_in=["claude"])
    assert st.state == "unknown", st.state


def test_an_mcp_companion_is_still_registered_by_its_config_entry():
    """The other half: for an MCP server the config entry IS the goal state."""
    st = CO.Status(_companion(), installed=False, registered_in=["claude"])
    assert st.state == "registered"


def test_the_report_tells_the_operator_to_install_a_missing_cli_companion():
    st = CO.Status(
        _companion(kind="cli"), installed=False, registered_in=["claude"], detail="x is not on PATH"
    )
    text = "\n".join(_companion_lines([st]))
    assert "[ ]" in text, text
    assert "registered for" not in text, text
    assert "pip install nothing" in text, text


# -- B40760556f6: the detail is never the probe's noise -----------------------------------


def test_json_output_is_not_reported_as_the_version(monkeypatch):
    _ran(
        monkeypatch,
        '[\n    {\n        "Id": "sha256:8cab47c8",\n        "Created": "2026-09-28T10:11:12.123Z"\n    }\n]\n',
    )
    installed, detail = CO.is_installed(_companion(detect=["python3", "inspect"]))
    assert installed is True
    assert not detail.startswith(("[", "{", '"')), detail
    assert "exited 0" in detail, detail


def test_npm_warnings_are_skipped_for_the_tools_own_line(monkeypatch):
    _ran(
        monkeypatch,
        "",
        'npm warn Unknown user config "email". This will stop working in the next major version.\n'
        'npm WARN Unknown user config "//init.author.url".\n'
        "Knowledge Graph MCP Server running on stdio\n",
    )
    installed, detail = CO.is_installed(_companion())
    assert installed is True
    assert detail == "Knowledge Graph MCP Server running on stdio", detail


def test_stdout_is_preferred_over_stderr_warnings(monkeypatch):
    _ran(monkeypatch, "Usage: context7-mcp [options]\n", 'npm warn Unknown user config "email".\n')
    _installed, detail = CO.is_installed(_companion())
    assert detail == "Usage: context7-mcp [options]", detail


def test_a_plain_version_line_is_kept(monkeypatch):
    _ran(monkeypatch, "roborev v0.63.0\n")
    _installed, detail = CO.is_installed(_companion())
    assert detail == "roborev v0.63.0"


def test_only_noise_falls_back_to_the_probe_that_succeeded(monkeypatch):
    _ran(monkeypatch, "", 'npm warn Unknown user config "email".\n')
    _installed, detail = CO.is_installed(_companion(detect=["python3", "--help"]))
    assert "npm warn" not in detail, detail
    assert "`python3 --help` exited 0" == detail, detail


def test_a_bracketed_tag_line_is_not_mistaken_for_json(monkeypatch):
    """Rubber-duck on B207: a first-character rule dropped `[tool] v1.2.3` as noise."""
    _ran(monkeypatch, "[codeguide] v1.2.3\n")
    _installed, detail = CO.is_installed(_companion())
    assert detail == "[codeguide] v1.2.3", detail


def test_a_quoted_version_line_is_kept(monkeypatch):
    _ran(monkeypatch, '"1.2.3"\n')
    _installed, detail = CO.is_installed(_companion())
    assert detail == '"1.2.3"', detail
