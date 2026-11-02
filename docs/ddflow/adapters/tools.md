# Adapter family `tools`

Port: **ExternalTool**, declared in `ddflow/adapters/tools.py` and marked `@port`.

A command-line tool ddflow shells to (roborev, gitleaks, pre-commit, prek; pip-audit, osv-scanner and
semgrep arrive with SEC-scanners). A multi-adapter family keyed by tool id: each tool is a row of
`ddflow/adapters/tools.toml` (argv, version probe, parser name, exit codes meaning clean and
findings, optional pin, install hint), and `TableTool` is the one class behind every row. A tool whose
output needs code gets a named parser in `tool_parsers.py`.

- **Surface:** `probe()` (installed True/False/None, version, pin met), `run(args)`, `parse(output)`,
  `install_hint()`.
- **Result:** `clean`, `findings` (each a `Finding{tool, rule, severity, file, line, advisory}`) or
  `unavailable`. Every failure (not installed, timeout, could not start, an exit code the row does not
  use, unreadable output, an unmet pin) is `unavailable`, never clean, and the reason names the next step.
- **Runner:** injected (`ToolRunner`); production is `ddflow.services.toolrun.external_tool(id, repo)`
  over `cmdrunner`. Timeouts: `[companions] tool_probe_timeout_s`, `tool_run_timeout_s`.
- **Install hint:** data. Show it; run it only after the operator agrees.
- **Fake:** `FakeExternalTool`, scripted `RunResult`s (`scripted(probe=, run=)`).
- **Conformance cases:** tests/test_adapter_tools.py.

The pattern is in [README.md](README.md).
