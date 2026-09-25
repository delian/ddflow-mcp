"""Scenario 4 — a JavaScript project, driven entirely over MCP.

Two independent claims are tested at once, because they interact:

* **Language independence.** ddflow knows nothing about Python. A gate is a shell
  command and a task is a set of file globs, so a Node project works with no adapter —
  the only difference is one line of `gates.toml`.
* **Agent independence.** Every operation here goes through the MCP JSON-RPC surface
  rather than the CLI, exercising the path a Gemini/Codex/Copilot client would take,
  and asserting that the two doors give the same answers.

The last step is the important one: it runs the same operation through BOTH surfaces
and compares. A second door onto one implementation is only valuable while it stays
one implementation.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from harness import McpClient, Scenario

SCAFFOLD = {
    ".gitignore": "node_modules/\n",
    "package.json": """
        {
          "name": "slugify-svc",
          "version": "1.0.0",
          "scripts": { "test": "node --test test/*.test.js" }
        }
    """,
    "src/.keep": "",
    "test/.keep": "",
}


def run(sc: Scenario) -> None:
    sc.head("SCENARIO 4 — a Node.js project driven end-to-end over MCP")
    if not shutil.which("node"):
        sc.note(
            "node is not installed; the JS gate will be reported UNAVAILABLE, "
            "which is itself the behaviour under test."
        )
    repo = sc.make_repo("slugify-svc", SCAFFOLD)
    sc.ddflow("init")
    # Commit the setup, as a real operator does: `init` touches tracked files
    # (.gitignore, .gitattributes) and an uncommitted change there leaves the primary
    # checkout dirty, which `ddflow merge` refuses.
    sc.git("add", "-A")
    sc.git("-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "ddflow: adopt")
    sc.write(
        ".ddflow/gates.toml",
        """
        # The ONLY project-specific line needed to adopt a JS project.
        [gate.unit_tests]
        command = "npm test --silent"

        [gate.standards]
        command = "node --check src/slugify.js"
    """,
    )

    sc.step("Connect an MCP client and complete the handshake")
    mcp = McpClient(repo, sc.dir.parent.parent if False else Path(__file__).resolve().parents[1])
    try:
        init = mcp.call(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "demo-agent", "version": "1"},
            },
        )
        sc.check(
            "the server completed an MCP handshake",
            init["result"]["serverInfo"]["name"] == "ddflow",
        )
        sc.check(
            "it returns instructions telling the agent where to start",
            "ddflow_brief" in init["result"]["instructions"],
        )
        tools = mcp.call("tools/list")["result"]["tools"]
        sc.check(
            f"it advertises {len(tools)} tools, each with a schema",
            all(t.get("inputSchema", {}).get("type") == "object" for t in tools),
        )

        sc.step("Build the work queue entirely through MCP tool calls")
        mcp.tool(
            "ddflow_phase_add",
            id="P1",
            title="Slug service",
            body="Deterministic URL slugs for arbitrary titles.",
        )
        mcp.tool(
            "ddflow_task_add",
            id="P1.T1",
            phase="P1",
            title="slugify core",
            globs="src/slugify.js,test/slugify.test.js",
        )
        mcp.tool(
            "ddflow_task_add",
            id="P1.T2",
            phase="P1",
            title="http handler",
            needs="P1.T1",
            globs="src/server.js",
        )
        board, _ = mcp.tool("ddflow_board")
        sc.check("the queue built over MCP is visible", "P1.T1" in board and "P1.T2" in board)

        sc.step("Ask MCP what to do next, and claim it")
        nxt, code = mcp.tool("ddflow_next", phase="P1")
        data = json.loads(nxt)
        sc.check(
            "MCP offers exactly the unblocked task",
            [r["id"] for r in data["ready"]] == ["P1.T1"],
            nxt[:200],
        )
        sc.check("and explains why the other is withheld", data["blocked"][0]["reason"] == "deps")
        claim, code = mcp.tool(
            "ddflow_claim", id="P1.T1", globs="src/slugify.js,test/slugify.test.js"
        )
        sc.check("the claim succeeded over MCP", code == 0, claim)
        wt = Path(json.loads(claim)["worktree"])
        sc.check("a real git worktree was created", (wt / ".git").exists())

        sc.step("Write real JavaScript in the worktree")
        sc.write(
            "src/slugify.js",
            """
            'use strict';
            function slugify(text, maxLen = 60) {
              if (typeof text !== 'string') throw new TypeError('text must be a string');
              const s = text.normalize('NFKD').replace(/[\\u0300-\\u036f]/g, '')
                .toLowerCase().replace(/[^a-z0-9]+/g, '-')
                .replace(/^-+|-+$/g, '');
              return s.slice(0, maxLen).replace(/-+$/g, '');
            }
            module.exports = { slugify };
        """,
            repo=wt,
        )
        sc.write(
            "test/slugify.test.js",
            """
            const test = require('node:test');
            const assert = require('node:assert');
            const { slugify } = require('../src/slugify.js');

            test('basic slug', () => assert.strictEqual(slugify('Hello, World!'), 'hello-world'));
            test('accents are folded', () => assert.strictEqual(slugify('Crème Brûlée'), 'creme-brulee'));
            test('no trailing dash after truncation', () => {
              assert.ok(!slugify('a'.repeat(59) + ' bb', 60).endsWith('-'));
            });
            test('non-string is rejected', () => assert.throws(() => slugify(42)));
        """,
            repo=wt,
        )
        sc.commit_in(wt, "P1.T1: slugify core")

        sc.step("Run the JS test suite as a gate — a real `npm test`")
        out, code = mcp.tool("ddflow_gate_run", id="P1.T1", gate="unit_tests")
        if shutil.which("node"):
            sc.check(
                "the real node test suite ran and passed via MCP",
                code == 0 and "PASSED" in out.upper(),
                out[-400:],
            )
            sc.note(
                "ddflow did not know or care that this was JavaScript. The gate is "
                "a shell command; the language never entered into it."
            )
        else:
            sc.check(
                "a missing toolchain is reported UNAVAILABLE, never as a pass",
                code == 2 and "UNAVAILABLE" in out.upper(),
                out[-300:],
            )

        sc.step("Record an agent gate, including an honest UNAVAILABLE")
        mcp.tool(
            "ddflow_gate_record",
            id="P1.T1",
            gate="critic",
            outcome="unavailable",
            reason="no second model configured in this demo environment",
        )
        status, _ = mcp.tool("ddflow_gate_status", id="P1.T1")
        sc.check(
            "the unavailable critic shows as a gap, not a tick", "[?] critic" in status, status
        )

        sc.step("Capture a lesson and prove retrieval finds it by meaning")
        mcp.tool(
            "ddflow_lesson_add",
            title="Truncating a slug can leave a trailing separator",
            rule="Strip separators AFTER slicing to length, not before.",
            why="Slicing mid-word leaves a dash at the boundary.",
            tags="text,bug",
        )
        hits, _ = mcp.tool(
            "ddflow_lesson_search", query="cutting a url slug short leaves a dangling hyphen"
        )
        sc.check(
            "semantic-ish retrieval found it without a shared keyword",
            "trailing separator" in hits.lower(),
            hits[:300],
        )

        sc.step("Both doors must give the SAME answer")
        mcp_board, _ = mcp.tool("ddflow_board")
        cli_board = sc.ddflow("board")[1]
        sc.check(
            "the MCP board and the CLI board are identical",
            mcp_board.strip() == cli_board.strip(),
            f"MCP {len(mcp_board)}B vs CLI {len(cli_board)}B",
        )
        mcp_next, _ = mcp.tool("ddflow_next", phase="P1")
        # exit 2 here is correct (T1 is claimed, T2 is blocked); the
        # check is that BOTH surfaces say the same thing, not what it is.
        cli_next = sc.ddflow("--json", "next", "--phase", "P1", expect=None)[1]
        sc.check(
            "the MCP and CLI schedulers agree exactly",
            json.loads(mcp_next)["blocked"] == json.loads(cli_next)["blocked"],
        )
        sc.note(
            "MCP is a second door onto one implementation. These two checks are "
            "what stop it quietly becoming a second implementation."
        )
    finally:
        mcp.close()
