Install and register the companion tools this project's gates expect{% if scope %} — only {{ scope }}{% endif %}, with the operator's consent for every install.

A gate with no companion behind it passes on the agent's word alone. This closes that gap for the companions ddflow already knows — roborev, codeguide, context7, the memory server and the rest of the registry. It does not look for new ones; that is `research-companions`.

**The operator consents to every install.** Installing runs code fetched from a registry on their machine. Ask first, per companion, and install only what they said yes to. Registering an already-installed MCP server in an agent's config is the lighter step, but it is still shown before it is written.

## 1. Survey

    ddflow_companions        — every known companion: state, kind, gates, install command, url

Sort each row into exactly one bucket:

| state | kind | what to do |
|---|---|---|
| `registered` | any | nothing — it is live |
| `installed` | `cli` | nothing — an agent shells out to it; there is no config to write |
| `installed` | `mcp` | **register** it (step 4) — one config entry away |
| `missing` | any | **install** it (steps 2–3), then register it if it is `mcp` |
| `unknown` | any | **check** — nobody probed. Re-run `ddflow_companions`; unknown is a question, never "absent" |

{% if scope %}Work only on `{{ scope }}`. An id it names that the registry does not know is an error to report, not something to install by guesswork.{% else %}Start with the `default = true` companions, and the gates in `uncovered_gates` — those are the gaps that exist today. A `default = false` companion is opt-in: mention it in one line, install it only if the operator asks.{% endif %}

## 2. Propose — one decision per companion, before anything runs

For each companion to install, give the operator:

- **what it buys**, in terms of the gates it serves (its `why`, not a marketing line);
- **the exact command** that would run — the registry's `install` field, verbatim;
- **what that executes** — a package manager fetching from a public registry, a container pull, a binary download;
- **where to read about it first** — its `url`.

Ask with the harness's question tool, your recommendation first. Several independent companions may go in one multi-select question; never bundle a "yes" to one with a "yes" to another.

## 3. Install what was approved — and prove it

- Run the registry's `install` command **verbatim**. Do not substitute a different installer, pin a different version, or pipe a remote script to a shell because it looked quicker.
- If it needs `sudo`, a credential, or an interactive prompt, **stop and hand that one to the operator** with the command to run. Do not work around it.
- **An install that exited 0 is not an installed tool.** Re-run `ddflow_companions` and read the state: only `installed` or `registered` counts. A tool the probe still reports `missing` did not install, whatever the installer printed.
- Record what this machine now has — `ddflow_memory_add` with the tool, version and how it was installed. That is a fact about this environment, not about the project.

## 4. Register the MCP ones

    ddflow_companions_add  dry_run=true   — the exact config entry, written nowhere
    ddflow_companions_add                 — write it, once the operator has seen it

Pass `id` for exactly the companions approved, and `agents` for the harnesses this project uses (`ddflow_companions_add` defaults to Claude Code). A cli companion needs no registration — `companions add` refuses one, correctly.

A newly registered MCP server is picked up when the agent next starts or reconnects its servers, not mid-session. Say so; do not report its gate as served until a session can reach it.

## 5. Record the declines

A decline is a result. For each companion the operator said no to:

- `ddflow_decision_add` — "declined <id>: <their reason>", so the next session does not propose it again unasked;
- from now on, the gate it would have served is recorded `unavailable` with that reason when it comes up — **never `passed` unaided**.

## 6. Report

A short table: companion · gates · outcome (`installed`, `registered`, `declined`, `handed to operator`, `failed — <what the probe said>`). Then the gates still uncovered after this run, from a final `ddflow_companions`.

## Never

- Install anything the operator did not approve, or anything outside the registry.
- Report a companion as working because its installer exited 0.
- Edit an agent's MCP config by hand instead of through `ddflow_companions_add`, which preserves the servers already there.
- Record a gate `passed` because the tool that would have checked it is missing.
