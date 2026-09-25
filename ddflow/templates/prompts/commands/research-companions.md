Find the MCP servers worth enabling for **this** project's stack{% if scope %}, focusing on {{ scope }}{% endif %}, and propose them to the operator.

`ddflow` ships a registry of companions it already knows about — those cover the gates that are the same in every project (review, standards, current documentation, memory, structured reasoning). What it cannot know is your stack. A Rust project wants different tooling than a Terraform one, and the `research` and `standards` gates are only as good as what sits behind them.

**You propose. The operator installs.** Nothing here downloads or runs anything: fetching and executing code on someone's machine because a config file named it is not a thing a work-queue tool gets to do.

## 1. Start from what is already known and what is already missing

    ddflow_companions        — the registry, each one's state, and which gates it serves
    ddflow_workflow          — the live pipeline, so you research gates that actually run

Read the `uncovered_gates` field first. A gate in the pipeline with no companion behind it is a gate whose judgement is currently unassisted — that is where a new server buys the most, and it is a fact about this project rather than a guess.

Do not re-propose something already registered, and do not propose a second tool for a gate that is already covered unless you can say what the first one misses.

## 2. Establish the stack from the repository, not from the project's description

A README says what a project meant to be. Determine what it *is*:

- manifests — `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, `Gemfile`, `*.csproj`
- infrastructure — `Dockerfile`, `*.tf`, `k8s/`, `.github/workflows/`
- data and services — migrations, ORM config, schema files, client libraries in the lockfile
- the languages by actual volume, not by presence: one `.sh` file is not a shell project

Write down the three or four things that dominate. Research those; ignore the long tail.

## 3. Research candidates — and apply the same evidence rule as every other gate

For each candidate, you must be able to state:

- **what it does**, in terms of a gate in the pipeline above;
- **the primary source** — the repository or the official docs, opened, not a marketplace summary of it;
- **who publishes it**, and whether it is maintained (last commit, open issue count);
- **what it executes on this machine** and what it reaches over the network;
- **whether it needs a credential**, and if so what that credential can do.

Prefer first-party servers published by the vendor of the thing they wrap. A third-party server for a popular API is the higher-risk one, and popularity on an aggregator is not evidence of either quality or provenance.

**A search-result snippet is a pointer, never a source.** If you did not open the repository, you did not verify it — say so and drop it.

## 4. Reject loudly

A decline is a first-class result and belongs in the report. "We looked at X and rejected it — it is unmaintained since 2024 / it wants a write-scoped token / it duplicates `context7`" is what stops the next agent researching it again in three weeks.

Be sceptical of: servers that want broad credentials for a narrow job; servers whose only distribution is a copy-paste snippet; anything that would execute code from a registry at gate time on a machine you do not control.

## 5. Propose, as a diff the operator can read

Write the proposal as `[[companion]]` blocks for `.ddflow/companions.toml` — the project-local registry, which overrides and extends the shipped one. That is config, not code, and the operator can delete a block they dislike.

    [[companion]]
    id = "<stable key>"
    title = "<one line>"
    gates = ["<the gate above that this serves>"]
    kind = "mcp"          # or "cli" for a tool the agent shells out to
    why = "<what it buys, in the terms the gate is phrased in>"
    detect = ["<cheap, side-effect-free existence check>"]
    command = "..."
    args = ["..."]
    install = "<the command a human runs>"
    url = "<where to read about it BEFORE installing it>"
    default = false       # start opt-in; promote once it has earned it

`detect` runs on every `ddflow_companions` call — keep it a `--version`-class check with no side effects.

Then hand the operator the list: what you found, what each serves, what it costs, what you rejected and why. Register nothing until they say so; when they do, `ddflow_companions_add` writes the config for the ones already installed.

## 6. Record it

Put the findings where the next session will find them — the research log for the reasoning and the declines, a `ddflow_decision` for anything the operator actually chooses. A verbal recommendation is one that gets re-derived from scratch next month.
