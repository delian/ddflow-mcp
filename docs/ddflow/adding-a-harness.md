# Adding an agent (a harness)

An agent is **data**: one descriptor file, `ddflow/harnesses/<id>.toml`. No Python changes
for an agent whose hooks fit an existing style. The README's table of supported agents
(`README/agents`) is rendered from the descriptors, so a new file adds its row.

You finish with four things, and `tests/test_harness_conformance.py` checks all of them with
no new test:

1. a descriptor, with every fact either stated, or marked absent, or left out (not verified);
2. payload fixtures for each hook event the agent sends;
3. a delta note, `ddflow/templates/drivers/deltas/<delta>.md`;
4. one passing conformance run for the new id.

## The three-valued rule

For every fact the descriptor says one of:

- a **value** (`path = ".gemini/settings.json"`);
- **documented absent** (`none = true`, `style = "none"`, `shape = "none"` with `path = ""`);
- **nothing**, which means *not verified*. ddflow never guesses: it reports the step
  `unknown`, and `ddflow harness doctor` lists it. Writing `none` for something you did not
  check is the one wrong answer.

Cite where each value came from in `[verified]`: `sources` (at least one URL), `date`, and
`unverified = [...]` for the facts you could not check. A key the loader does not know is an
error that names the key and the file.

## Worked example: a made-up agent `acmecli`

This block is parsed by `tests/test_adding_a_harness.py`, so it cannot rot.

```toml acmecli.toml
id = "acmecli"
rank = 40
family = "acme-cli"
title = "Acme CLI"
delta = "acmecli.md"

[mcp]
path = ".acme/mcp.json"
shape = "mcpServers"

[instructions]
reads = ["ACME.md", "AGENTS.md"]
agents_md = "yes"
claude_md = "no"

[hooks]
style = "claude"
file = ".acme/settings.json"
normalizer = "claude"
emitter = "claude"
block = "exit2"
session_id = "session_id"
inject = ["session_start", "prompt"]

[hooks.events]
session_start = "SessionStart"
prompt = "UserPromptSubmit"
stop = "Stop"

[plugin]
none = true

[commands]
none = true

[skills]
none = true

[subagents]
none = true

[worktrees]
none = true

[verified]
date = "2026-10-19"
sources = ["https://example.com/acme-cli/docs/hooks"]
unverified = ["bash-tool session id env"]
```

What each section is for:

| Section | Says | Read by |
|---|---|---|
| top level | `id` (must equal the file name), `rank` (order of detection and of the README table), `family` (the model-family label used by review independence), `delta`, optional `title` | everywhere |
| `[mcp]` | the project file that registers MCP servers and its `shape`: `mcpServers`, `servers`, `mcp.servers`, `opencode`, `copilot`, `toml.mcp_servers`, `none`; optional `approve_file`, `approve_key`, `deny_key` when the agent needs a server approved before it starts it | `adopt`, `onboard harness` |
| `[instructions]` | which files the agent reads and whether it reads `AGENTS.md` (`yes`, `no`, `conditional`, `config`, `fallback`, `nv`) | `adopt`, AGENTS.md |
| `[hooks]` | the hook `style` (`claude`, `gemini`, `copilot`, `cursor`, `cline`, `cascade`, `antigravity`, `plugin`, `none`), where it lives, how a payload is normalized and an answer emitted, how a refusal is signalled (`block`), and which events can inject text (`inject`) | `hooks install`, `hooks run` |
| `[hooks.events]` | canonical event to the agent's event name: `session_start`, `prompt`, `pre_tool`, `post_tool`, `pre_compact`, `stop`, `session_end` | hook writers, the ladder |
| `[plugin]` `[commands]` `[skills]` `[subagents]` `[worktrees]` | the other surfaces the agent has, or `none = true` | `adopt`, `inventory` |
| `[env]` | the environment variables that carry the agent's session id (`session_id = [...]`) | session identity, attribution |
| `[verified]` | sources, date, probe, record | `harness doctor`, review |

A hook style with no writer yet (`plugin`, `cascade`, `cline`, `antigravity` today) is
loaded and reported: `onboard harness` says `unknown` for its hooks step, never `n/a` and
never `done`. A new style is a small writer in `ddflow/services/hookwriters/`, not a new
descriptor field.

The ladder: ddflow installs the best level the agent supports, in this order: `native hook`,
`plugin`, `first-call` (the first MCP call carries the brief), `mcp-text` (the MCP handshake
instructions), `instruction text`, `none`. `ddflow harness show acmecli` prints the level per
event.

## Fixtures

For every event the agent sends to a command hook, record the real stdin once and save it as
`tests/fixtures/harness/acmecli/<event>.json`:

```json
{
  "_meta": {"recorded": true, "source": "acme 1.4.2 --debug-hooks", "version": "1.4.2"},
  "stdin": {"session_id": "s-1", "prompt": "hello"},
  "expect": {"session_id": "s-1", "prompt": "hello"}
}
```

`expect` is what the normalizer must produce. A fixture written from the documentation
instead of a recording says `"recorded": false` and `"version": "unversioned"`; the
conformance run accepts it and the table of gaps keeps it visible. An agent with a command
hook and no fixtures is reported as a skipped test, a visible gap.

## The delta note

Copy the shape of an existing delta (for example `deltas/gemini-cli.md`): the driver is
canonical, and the delta lists only what differs: how iteration continues, how to ask the
operator, how to spawn a subagent, where the rules live, file-reference syntax. Name it as the
descriptor's `delta` says. Do not copy the driver; a copy drifts.

## The conformance run

```
uv run pytest tests/test_harness_conformance.py -k acmecli -q
```

It checks for the new id: paths are project-relative; every mapped event is canonical and the
ladder is total; the normalizer and emitter named exist; each fixture normalizes to its
`expect`; installing hooks twice changes nothing and uninstalling restores the operator's
file; `adopt` twice changes nothing. A live probe (`<id> --version`) runs only when the binary
is on `PATH`; otherwise it is **skipped, a recorded gap, never a pass**.

Then `ddflow pins regenerate` to add the README row, and `ddflow harness doctor acmecli`.

## Tiers: how much testing an agent gets

From D-agent-compat. Put the agent in the lowest tier you can honestly support.

| Tier | Agents | Test |
|---|---|---|
| 1 live headless smoke | agents with a headless mode, an MCP client and hooks (Claude Code, Codex CLI, Gemini CLI, Copilot CLI, Kilo, opencode, Qwen Code, ...) | a real run with a 3 USD cap, majority of 3, weekly and before a release |
| 2 fixtures only | IDE agents with no headless mode (Cursor IDE, Cline, Antigravity, Aider, ...) | recorded payload fixtures and the conformance run |
| 3 advisory cloud bots | cloud agents that open pull requests | a sandbox repository, when the operator creates one |
| 4 event sources only | Dependabot, Renovate, CodeRabbit, Qodo | none; their events are read |

Credentials come from the environment or the git-ignored local config, never from a commit. A
missing key, CLI or rate limit is `unavailable`, never a pass. A tier-1 agent also needs the
`[test]` section for the smoke harness (B-ac-smoke): the headless command, its prompt and what
the run must show. That section is reserved by the descriptor schema work
(P-agent-parity.descriptor-v2); until it lands a tier-1 agent is tested by the conformance run
only, and the guide above is the whole contract.

## Overriding a value

Every value is data a project may override in its config; the descriptor is the default, not
the only source. The upgrade manifest records a changed descriptor, so a project learns its
agent's facts moved.

## Checklist

- [ ] `ddflow/harnesses/<id>.toml`, id equal to the file name, `[verified]` filled
- [ ] `tests/fixtures/harness/<id>/<event>.json` for each hooked event
- [ ] `ddflow/templates/drivers/deltas/<delta>`
- [ ] `uv run pytest tests/test_harness_conformance.py -k <id>` green
- [ ] `ddflow pins regenerate`; `ddflow harness doctor <id>` shows nothing broken
