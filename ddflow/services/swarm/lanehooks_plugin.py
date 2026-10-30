"""The lane-private pre-tool plugin for opencode-style harnesses (kilo, opencode) (SW-lane-hooks).

Executable code, so it is shown to the operator as a diff and approved once per TEMPLATE
(``digest``), not per lane: the lane-specific values (the check argv, the lane identity) are
the only substitutions, and they are data (JSON literals), never code. The plugin does one
thing: before a tool runs it hands the call to the check command and throws when that exits 2
(the opencode block mode, descriptor ``block = "throw"``). It lives in the lane's home, never
in the project.
"""

from __future__ import annotations

import json

from ...core.digest import content_digest

#: Bump when the template text changes: an approval names the digest of the TEMPLATE.
VERSION = 1
FILE_NAME = "ddflow-lane-hook.js"
_ARGV, _AGENT = "__DDFLOW_ARGV__", "__DDFLOW_AGENT__"

TEMPLATE = """// ddflow lane pre-tool hook v1 -- lane-private; written and removed by ddflow swarm lanehooks.
// Before a tool runs, ask the lane's lease check; a refusal (exit 2) throws, which blocks the call.
// A check that cannot run allows the call (fails open), as ddflow's other hooks do.
import { spawnSync } from "node:child_process";

const ARGV = __DDFLOW_ARGV__;
const AGENT = __DDFLOW_AGENT__;

export const DdflowLaneHook = async (ctx) => ({
  "tool.execute.before": async (input, output) => {
    const payload = JSON.stringify({
      tool_name: input.tool,
      tool_input: output.args || {},
      cwd: (ctx && (ctx.worktree || ctx.directory)) || process.cwd(),
    });
    const r = spawnSync(ARGV[0], ARGV.slice(1), {
      input: payload,
      encoding: "utf8",
      env: { ...process.env, DDFLOW_AGENT: AGENT },
    });
    if (r.error || r.status === null) {
      console.error("ddflow lane hook: the check could not run; the call is allowed (not checked).");
      return;
    }
    if (r.status === 2) throw new Error((r.stderr || "").trim() || "ddflow lane hook: refused");
  },
});
"""


def digest() -> str:
    """What the operator approves: the template with its placeholders, version included."""
    return content_digest(TEMPLATE, "sha256")


def render(argv: list[str], agent: str) -> str:
    """The plugin text for one lane: JSON literals substituted for the two placeholders."""
    if not argv or not all(isinstance(x, str) for x in argv) or not agent:
        raise ValueError("the plugin needs the check argv and the lane identity")

    # json.dumps output is a valid JS literal except U+2028/2029 in old engines: escape them.
    def esc(v):
        text = json.dumps(v)
        return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

    return TEMPLATE.replace(_ARGV, esc(list(argv))).replace(_AGENT, esc(agent))
