# Drivers: how ddflow keeps the core common and the agents and models as data

A **driver** is everything ddflow needs to talk to one coding agent and one model. Most of it
is **data** (a descriptor or a model catalog), interpreted by a small closed set of named
**engines**; code remains only for things data cannot say. The point (operator request
2026-10-23, decided D-DRV-1..8): adjust to a new agent or model **mostly with configuration,
not code**, so future agents cost a file instead of a release.

Code: `ddflow/services/harnessreg.py` (descriptor schema and loader), `driverpacks.py` (packs),
`engines.py` (E1..E8), `lane_driver.py` + `adapters/lanes.py` (the Lane contract), and one
driver per transport. Decision `decisions/driver-architecture.md`; research
`research/driver-architecture.md`.

## 1. Three axes, one combination rule

A driver is the effective answer for one **harness** (the client program), one **model** (the
weights/vendor terms) and one **route** (how the call reaches the model). They own different
facts and combine without per-pair code:

| Axis | Owns | Files |
|---|---|---|
| harness | hooks, flags, settings, resume, exit codes, transcripts, MCP, limits *mechanism* | `ddflow/harnesses/<id>.toml` |
| model | window, price, cache semantics, reasoning, limits/price *numbers* | `ddflow/models/` (`CC-inventory`) |
| route | endpoint, key env, wire format, who bills, cache through a gateway | `ddflow/models/routes/` |

Rules for `effective(harness, model, route)`:

1. **Mechanism is the harness's, numbers are the model's.** "How to set a window" is a harness
   fact; `window = 1_000_000` is a model fact. Effective window = `min(model, harness cap,
   operator cap)`; caps only tighten.
2. **Route overrides model only where it changes semantics.** Route facts are stated only where
   they differ from the model's.
3. **Precedence, strongest first:** observation (a probe) > operator/company override > pair
   table > route > model family > harness default > the policy default.
4. **Unknown never reads as supported.** A missing fact yields the lowest-common-denominator
   behaviour and is *named* ("not verified, using fallback X"), never guessed from a neighbour.
5. **Safety combines by `min` or `any-prohibits`**: a limit on either side tightens; a
   prohibition on either side wins.

A model family is **not** a harness: one harness runs many models; a route may point a harness
at a different vendor. The harness descriptor's `family` is a *lineage* label, the model's
`family` is the pretraining family used for review independence — same word, two meanings.

## 2. Data versus code

**Data** stays a record: validated scalars, no conditionals, no loops, no expressions, no
includes; placeholders from a closed linted list; every field has a reader in code (a dead field
is a lint error). A field enters the schema only with a consumer **and** either two agents using
it or a safety reason.

**Engines** (D-DRV-2) interpret the data — the one place per mechanism:

| Engine | Interprets | Ships as |
|---|---|---|
| E1 `fieldmap` | hook stdin fields | `[hooks.fields]` dotted paths, first-non-empty |
| E2 `emit` | hook output variant | `plain json_context copilot cursor cline antigravity silent` |
| E3 `mcp_shape` | MCP container + entry shape | container path + entry template + serialiser |
| E4 `render_argv` | launch command | argv template `{model} {session} {dir}`, never a shell |
| E5 `stream_map` | per-line stream events | NDJSON rules to canonical lane events |
| E6 `limit_detect` | limit/reset detection | exit codes and bounded regexes |
| E7 `transcript_map` | flat-JSONL transcript ingest | path template + row table |
| E8 `probe` | version/presence probe | declared argv only, no network, no model call |

**Code** stays (for these, data would be a worse language): hook-config **writers** that edit the
operator's own files idempotently, **transport protocols** (ACP, JSON-RPC, OpenCode HTTP, tmux),
**non-flat transcripts**, **stateful stream meaning** (`agent_end` vs `agent_settled`), and
**tiered price arithmetic**. Each such piece is a driver behind the `Lane` interface (§4), and
descriptors only **name** it.

## 3. The zero-Python recipe

A new agent whose hooks fit an existing dialect, whose transcript is flat JSONL, and whose
headless run is an argv with an exit-code table needs **no Python**: add a descriptor under
`ddflow/harnesses/<id>.toml` (and, when it runs headless, a `[headless]` section), add fixtures,
and the conformance suite (`tests/test_driver_conformance.py`) picks it up. The metric
"agents added without a Python diff" is the success measure.

## 4. Writing a transport

One `Lane` interface, one driver per **transport** (`ddflow/services/lane_driver.py`,
`adapters/lanes.py`; docs `lane-driver.md`):

```
Lane.start(spec) -> LaneHandle | Result     # Result = unavailable | failed
Lane.events(h, idle_s=None) -> Iterator[LaneEvent]
Lane.steer(h, text) -> Result              # unavailable unless "steer" advertised
Lane.stop(h, grace_s=None) -> Result       # group kill, remove private files
Lane.status(h) -> LaneStatus
```

Capabilities: `steer interrupt resume usage tool_events permission_broker`. A missing capability
returns `unavailable`, never emulated. Implement the five calls + `capabilities`, then
`register_transport(name, factory)` at import. Rules every transport keeps are in
`lane-driver.md`: argv list, secrets by env NAME, private home, one group-kill cleanup.

Third-party drivers of an irregular protocol register through the **plugin SDK**
(`ddflow/services/driverplugins.py`, option C) as a Python entry point (an object that builds a
`Lane`) or an external executable speaking the versioned JSON-over-stdio protocol; registration
is operator-only and a plugin conforms before it is enabled.

## 5. Packs and trust

Descriptors and models load from the D-config-layer-order layers — packaged (`ddflow/harnesses`),
user (`~/.config/ddflow/harnesses.d`), project (`.ddflow/harnesses.d`) and machine-local
(`.ddflow/local/harnesses.d`) — merged per fact, later layers winning (`driverpacks.py`;
docs `decisions/driver-architecture.md` D-DRV-5, D-DRV-Q5). The security split:

- A **cloned project** may add detection, paths and facts; it may **never** set a key that runs
  a process. `[headless] argv` and `[probe]` are **operator-only** (the shipped set and the user
  layer). A project-layer descriptor with one is refused at load, not run.
- A **company pack** is data only and pinned by digest (`CM-packs`): a bare URL without a digest
  is refused; a mismatched digest keeps the content out.
- **Schema versioned**: each file carries `schema = N`; a newer schema is "unavailable: upgrade
  ddflow", never half-read. Unknown keys stay an error.
- **Plugins** (code, running) are registered only in the operator's directory (outside the
  repository); each carries a `trust` label and a `sandbox` (env names only), and nothing enables
  before conformance.

## 6. Probing (E8)

`[probe]` lists safe read-only argv (`--version`, `--dump-config` where documented). A probe runs
only the declared argv, never the network and never a model call, with a timeout; its output is
stored as a fact with the `tool_version`. Facts carry `{value, status, source, retrieved,
versions, expires, by}`; a fact past `expires`, or an installed agent outside its declared
`versions` range, is demoted to unverified (never deleted) and `doctor` flags it.
