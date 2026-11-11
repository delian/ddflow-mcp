# Contributing: publishing, releases, CI, reviewers and testing

Publishing, cutting a release, what CI checks, reviewers, companion tools and the test suite. Part of the ddflow [README](../../README.md); the reference moved out of it by D-readme-split. Every heading here keeps the anchor it had in the README.

## Shipping and reviewing

Publishing, releases, what CI checks, reviewers and companion tools.

### Publishing and registry

**Nobody should have to paste JSON into an IDE to use this.** `server.json` is the
[MCP registry](https://modelcontextprotocol.io/registry/quickstart) manifest
(`io.github.delian/ddflow-mcp`), and publishing it is what makes ddflow findable in the
VS Code and Cursor marketplaces rather than something you configure by hand. It offers
three ways to run the same server, so a client picks whichever it supports:

| Package | Identifier | For |
|---|---|---|
| `pypi` | `ddflow-mcp`, `runtimeHint: uvx` | Anything with `uv` — no clone, no install step |
| `oci` | `docker.io/delian/ddflow-mcp:<version>` | Operators with no Python toolchain |
| `oci` | `ghcr.io/delian/ddflow-mcp:<version>` | The same image, no Docker Hub account needed |

The image is built for `amd64` and `arm64`, because an Apple-silicon operator running it
under emulation pays that cost on every tool call, and tool calls are all this server
does.

**How CI authenticates — four mechanisms, one stored secret:**

| Target | Mechanism | Stored secret? | Setup |
|---|---|---|---|
| PyPI | OIDC trusted publishing (`id-token: write`) | No | Add a trusted publisher on PyPI, once |
| ghcr.io | `GITHUB_TOKEN`, injected per run, expires with the job | No | none |
| Docker Hub | `DOCKERHUB_USERNAME` + `DOCKERHUB_TOKEN` | **Yes** | Create an access token, add both secrets |
| MCP registry | GitHub OIDC — proves control of the account that owns the `io.github.delian/*` namespace | No | none |
| tag + release | `GITHUB_TOKEN` (`contents: write`) | No | none |

**Docker Hub is the only one that needs a long-lived credential**, because it has no OIDC
equivalent. Use an access token scoped to this repository, never an account password. If
that is one secret too many, delete the Docker Hub login and its two tags — ghcr.io alone
satisfies the OCI entries a marketplace needs, and `server.json` lists both so a client
picks whichever resolves.

`environment: release` on the publishing jobs is a control worth knowing about: point it
at a GitHub environment with required reviewers and every release waits for a human,
with no change to the workflow.

**Order matters and the workflow encodes it.** `mcp-publisher` validates that every
package named in the manifest exists, so the registry step runs *after* both PyPI and
Docker — publishing the manifest first would advertise a version nobody can fetch.

**The registry verifies ownership, and the workflow checks it first.** The registry accepts
a package only when the artefact itself names the server: the PyPI package's README
carries `<!-- mcp-name: io.github.delian/ddflow-mcp -->` (the first lines of this file),
and each image carries the label `io.modelcontextprotocol.server.name` with the same name.
The `verify` job runs `tests/test_registry_ownership.py` first, so a release the registry
would reject — a missing marker, or a label that does not match — is refused before
anything is uploaded; a `server.json` `description` over the registry's 100-character limit
fails the suite the same way. So do the registry's per-package rules, which the JSON schema
does not express and the registry enforces only at publish: an `oci` package must **not**
carry a `version` field (or `registryBaseUrl`/`fileSha256`) — the tag in its `identifier` is
the version — while the `pypi` package must carry one. publish #40 failed on exactly that, at
the last job, after PyPI and both images had shipped; `tests/test_registry_manifest_rules.py`
now encodes the rules offline and `verify` runs it first. The publish step also retries a *transient* registry failure
(a 504, a 408/429, a network error) with backoff, checking the registry for the exact
version after every attempt, because the publish behind a 504 may have committed; a 4xx
that retrying cannot fix fails at once, and only after the budget does it fail with a
"Re-run failed jobs" hint.

Four things gate a release, and each exists because the failure it catches is public and
irreversible:

* the tag, `ddflow/__init__.py` (the one declared version), `server.json`'s version **and
  every OCI identifier's tag** must agree — a `:0.1.0` left behind while `version` moved on publishes a manifest
  pointing at the previous image, installable and wrong;
* the full suite, plus the slow end-to-end scenarios, which `-m 'not slow'` otherwise
  excludes from every ordinary run;
* the wheel must **install into a clean venv and run**, and carry its templates —
  `uv build` succeeding proves the metadata parses, not that `ddflow help` works;
* the image must answer `initialize` over stdio. A built image that cannot is a broken
  release every marketplace will happily offer.

### Cutting a release

```console
$ git push origin main           # that is the whole release
```

**Every push to main that changes shipped code releases, numbered by the impact its changes declare.**
A patch unless the upgrade manifest declares a breaking change (then a minor while ddflow is 0.x); major moves only when you move it.
"Shipped" means `ddflow/`, `pyproject.toml`, `uv.lock`, `Dockerfile`,
`docker-entrypoint.sh`, `.dockerignore`, `server.json`, `server.template.json` or `README.md` (the PyPI page, and
the line the registry verifies): a push of other docs, tests or the ddflow event log
releases nothing, because PyPI keeps every version forever and one identical to the last
is noise nobody can withdraw. CI runs `scripts/bump.sh` at the level `scripts/release_impact.py` reads from the manifest, commits `release 0.1.2` to
main, publishes PyPI, Docker Hub, ghcr.io and the MCP registry, then creates `v0.1.2` and
a GitHub release — **last**, and only once every publish succeeded, because a tag pointing
at a half-release is worse than no tag: it looks authoritative.

**Pull after a release.** The release commit is CI's, so your `main` is one commit behind
it and the next push is refused until you `git pull`.

How the gate picks the version: it publishes the declared version if PyPI does not have it
yet, and bumps (by the declared impact: patch, or minor for a `breaking` entry while 0.x) only if it does. Moving to an exact version, or major, is yours to do —

```console
$ scripts/bump.sh minor          # 0.1.4 -> 0.2.0 (or: major, or an exact 1.0.0)
$ git commit -am 'release 0.2.0' && git push origin main
```

— and CI publishes exactly `0.2.0`; the next push that bumps nothing releases `0.2.1`.

The same rule makes a release that failed *before* its PyPI upload retry its number with
the next push. One that failed after it — Docker Hub, ghcr.io, the MCP registry — does
not: PyPI has the number, so the next push moves past it; re-run that run's failed jobs
from the Actions page instead. A bump never lands on a number PyPI already holds (a `v*`
tag can publish one out of band): CI takes the next free number at the declared level
(moving on by patch when that one is taken) before it commits anything. `scripts/release.sh` refuses a declared version that is a smaller step than the manifest's
`impact = "breaking"` entries ask for, and a surface change (a command, flag, tool, argument, knob or
event kind gone) that the previous release's snapshot shows and nothing declares, with the options. The bump is pushed to main *before* anything publishes: a push that
loses a race with another commit fails the run and publishes nothing, where pushed last it
would leave PyPI holding a version main does not declare. Runs are serialized, and only
`main` or a `v*` tag releases.

The version is declared in **one** place: `__version__` in `ddflow/__init__.py`, the only
line a bump edits. `pyproject.toml` reads it (a hatch dynamic version, so `uv.lock` records no
version for the project and a bump never makes the lock stale), `SERVER_INFO` (what the
server tells every client it is, and what `ddflow --version` prints) is built from it, and
`server.json` — the manifest's version, the `pypi` package's version and the tag inside every
OCI identifier (an `oci` package has no version field) — is a **generated, committed** file:
edit `server.template.json`, then run `scripts/render_server_json.py` (`--check` fails when
`server.json` is not the render; a test and the publish workflow both run it).
`scripts/bump.sh` edits the literal, renders `server.json` and re-reads the result;
`tests/test_packaging.py` fails if anything drifts. Do not edit `server.json` by hand.

**`scripts/release.sh` runs all of that locally and publishes nothing.** It is dry by
default, needs no credentials, and exists because a tag is not reversible: PyPI refuses a
re-upload, `:latest` is on someone's disk before you notice, and a registry manifest is
what an IDE offers people. If it fails on your laptop, the tag was going to fail an hour
later in public. `--publish` is the escape hatch for when CI is unavailable, and it makes
you type the version to confirm.

### What CI checks

`.github/workflows/ci.yml` runs on every push and pull request, in four jobs that fail
for different reasons so you can tell at a glance which:

| Job | Checks |
|---|---|
| **quality** | `ruff check` + `format --check`; the wheel **installs into a clean venv, runs, and carries its templates**; `gitleaks` over full history; `bandit` over the package; a dependency audit that also asserts every runtime dependency is on an explicit allowlist (today: Jinja2, tomlkit) |
| **tests** | The suite on Python 3.11 and 3.13 — the floor and the current release, because a version-specific break is a break for somebody |
| **codeql** | GitHub's `security-and-quality` queries, landing in the Security tab rather than a log |
| **scenarios** | The slow end-to-end runs, and the concurrency/load suite, each as its own step with `if: always()` |

Two of those exist because of specific failures. The wheel check is there because `uv
build` succeeding proves the metadata parses, not that `ddflow help` works — a wheel
missing its templates fails on the user's machine. And **`gitleaks` is there because this
project has already committed a live API key**: a secret in git is a leaked secret,
rotation is the only remedy, so the check that matters is the one that runs before every
push.

`bandit` deliberately skips `tests/`, which use `subprocess` and temporary paths
constantly and by design. A scanner that cries wolf on every fixture is a scanner nobody
reads.

**The same checks run before you push.** Install the pre-push hook once per clone:

```console
$ ln -sf ../../scripts/ci/pre-push "$(git rev-parse --git-common-dir)/hooks/pre-push"
```

It runs what CI runs, from `.pre-commit-config.yaml`: ruff, the wheel probe, bandit, the
dependency allowlist, gitleaks over the commits being pushed, the unit suite and the
scenarios, and a schema check of the workflow files. The build probe and the allowlist are
`scripts/ci/` files that CI calls too, so the two cannot drift.

It runs them **in a scratch worktree of the commit being pushed**, not in your checkout —
which is why it is not `pre-commit install`. pre-commit fails a hook when anything in the
tree changes while it runs, and ddflow agents append to the committed event logs
constantly: in your own tree the test hook failed with every test green. A scratch tree has
no other writers, and it checks what you push rather than what is uncommitted.

The unit suite and the scenarios run as **one** pytest session, one worker per CPU
(`-n auto`) with work-stealing, so the scenarios overlap the unit tests instead of
following them: about a minute on a 192-thread machine. The scenarios take CI's
no-model-server path (`DDFLOW_DEMO_NO_REVIEWER=1`), since a real review there costs
minutes and is not what CI checks.

CI's `tests` job runs each matrix Python (3.11, 3.12, 3.13) as a uv-managed standalone
build (`uv python install` plus `UV_PYTHON_PREFERENCE=only-managed`), never the runner's
system Python. To reproduce locally: `uv python install 3.12` and run
`UV_PYTHON_PREFERENCE=only-managed uv run --python 3.12 pytest tests/`.

It is a push hook, not a commit hook, because ddflow's own hooks own the commit (`ddflow
hooks install`), and because minutes per commit teaches `--no-verify`. `SKIP=tests git
push` skips one check by id. It does not run the 3.11/3.13 matrix, CodeQL, pip-audit or
the load suite.

### Any LLM as a reviewer — local, remote, SaaS, or a CLI

The `critic` and `rubber_duck` gates are **run by ddflow, not claimed by the agent**.
Point them at whatever you have:

```sh
ddflow reviewers presets            # 19 ready-made provider settings
ddflow reviewers add --preset ollama --model qwen3:8b
ddflow reviewers detect --write     # probe local ports, register what is serving (machine-local)
ddflow reviewers test               # send a known-buggy diff, check the reply
```

Four backends, because "any LLM" means four wire formats in practice:

| `kind` | Reaches | Examples |
|---|---|---|
| `openai` *(default)* | anything OpenAI-compatible — which is most things | ollama, vLLM, LM Studio, llama.cpp, sglang, LiteLLM, OpenAI, DeepSeek, Groq, Together, Fireworks, Mistral, OpenRouter, xAI |
| `anthropic` | the Messages API (system is a top-level field, not a message) | Claude |
| `gemini` | `generateContent` (key in the query string, not a header) | Gemini |
| `command` | **anything at all** — a CLI that reads a prompt on stdin and writes the reply to stdout | `claude -p`, `gemini -p`, `codex exec`, `llm -m`, your own script |

`command` is the escape hatch that makes the answer to "can it use *X*?" always yes: a
model with no HTTP API, behind a corporate gateway, or wrapped in an in-house tool is
still usable, with no SDK and no dependency.

**Who may add a reviewer.** A reviewer decides whether a review counts as independent, so
an agent must not be able to mint one (decision D-reviewer-trust):

- A `kind = "command"` reviewer runs any program and can print any verdict, so **only a
  person adds one**: by editing `.ddflow/local/reviewers.toml`, or with `ddflow reviewers
  add` from their own terminal. Every agent surface refuses it — `ddflow_configure`,
  `config --append-toml`, and any command run under `--agent` or `DDFLOW_AGENT`, or in a
  Claude Code shell (`CLAUDECODE`, the one harness marker known for certain; another
  harness is recognised by the `--agent`/`DDFLOW_AGENT` its ddflow setup passes).
- A reviewer **a tool writes** (`reviewers add`, `reviewers detect --write`,
  `ddflow_configure`) is recorded with who wrote it, and its reviews do not count toward
  the cross-family rule until a person checks the entry and runs
  `ddflow reviewers approve <name>` (`ddflow reviewers approve` alone lists what is
  waiting). Approval is for the entry as it was: a tool changing its endpoint, model,
  family or command makes a new, unapproved reviewer; tuning `max_tokens` or `hedge` does
  not. `reviewers approve` has no MCP tool and refuses under an agent identity.
- A reviewer **no tool wrote** — every entry configured by hand, including all of them
  from before this rule — counts exactly as before.

Like human gates, this makes a forged reviewer visible in the log; it cannot stop a shell
edit of the local files.

The approval is the shared approve-by-digest primitive (`services/approval.py`,
B-uni-approval). It records one event, `approval.granted`, with the subject
(`reviewer:<name>`), the digest a person approved, the OS user, the host and `human`. It has
one check: the digest of what is about to run must equal a digest a person approved, so a
later edit is not approved. A single-use approval also carries the hash of a token minted
for the person; whoever presents the token may act once (`approval.used`), and the token
itself is never written. The planned approvals (skill installs, upstream reports, drafts,
schedule escalations, upgrades) use the same primitive. A log from before it holds
`reviewer.approved`, which still counts.

```toml
[[reviewer]]
name   = "local-qwen"
kind   = "openai"
base_url = "http://127.0.0.1:11434/v1"
model  = "qwen3:8b"
family = "alibaba"              # must differ from the author's family
gates  = ["critic"]
# Optional: start it if it is not already running.
launch = { command = "ollama serve", ready_url = "http://127.0.0.1:11434/v1/models" }

[[reviewer]]
name    = "claude-via-cli"
kind    = "command"
command = "claude -p --model {model}"
model   = "claude-sonnet-5"
family  = "anthropic"
gates   = ["rubber_duck"]
```

Auto-launch is **opt-in per reviewer** — starting a multi-gigabyte model server as a
side effect of asking for a code review is a surprise nobody wants by default. When it
fails it never leaves a half-started process behind, because a reviewer stuck "starting"
forever is indistinguishable from one that is down except that it also holds a process.

**Keys are never written to the config.** Only `api_key_env`, the *name* of an
environment variable — the config file is committed, and a key in git is a leaked key.

To not export the variable in every shell, a reviewer in the git-ignored
`.ddflow/local/reviewers.toml` may also name `api_key_file = "~/.config/me/gemini.key"`.
The variable wins; the file is the fallback. The file must be a regular file you own with
no group or other access (`chmod 600`, as ssh insists), is read at each call and never
cached, and a missing, empty or too-open file makes the reviewer UNAVAILABLE with the
reason. The path is data and the key never is: it appears in no log, event, error,
review record or listing. `api_key_file` is refused in the committed `.ddflow/config.toml`.

Every way of not reviewing is reported distinctly, with its remedy: no key names the
variable, a missing CLI names the binary, a dead server names the launch block you could
add, a non-zero exit shows stderr, **and empty output on exit 0 is UNAVAILABLE rather
than "no findings"** — the vacuous pass arriving by the most innocent-looking path
there is.

> **Reasoning models need a large `max_tokens`.** Default 32000, measured not guessed:
> on Qwen3.8-Flash-Next over a 30 KB diff, a 6000-token budget produced **zero
> characters of content** — the whole budget went to reasoning and the reply was
> truncated. That case is reported as `TRUNCATED` with the remedy named, never as an
> empty completion and never as a clean review. A chunk lost that way on every copy is
> tried once more, split in halves when it splits, before it is reported unreviewed.
>
> **Leave `temperature` unset** unless you mean to override the model. Unset, the request
> carries none and the server applies the model's own recommended sampling. A reasoning
> model sampled cold (ddflow used to send 0.3) can loop — "let me reconsider" — until
> the whole budget is gone; at its vendor's recommended 1.0 the same diff reviewed fully.

**Reviews run in parallel, and each chunk is raced.** A large diff is reviewed in chunks
(`max_chunk_chars`). They go out together — by default every chunk and every copy in one
wave (up to 32 requests); `max_concurrency` caps it — so a review takes as long as its
slowest chunk rather than the sum. Each
chunk is also sent `hedge` times (default 2): the first copy that answers **on contract**
is the chunk's review, and the rest are cancelled — the connection is closed so the
server aborts the request, a `command` reviewer's process group is killed. A chunk whose
every copy failed is reported exactly as before; nothing is counted as reviewed that was
not.

Why: a reasoning model's time is its reasoning length, and that is random. Measured on a
LAN vLLM: the same 3 KB diff took 64–391 s across eight identical calls, about one call
in four on a hard diff never answers before `max_tokens`, and the server was idle —
eight concurrent requests each ran 28% slower for 5.7× the throughput. A 24 KB review
went from 1 274 s (chunks in sequence) to the time of its slowest chunk.

```toml
[[reviewer]]
# ...
hedge = 3             # copies per chunk; 1 turns racing off
max_concurrency = 10  # cap on requests in flight; 0 (default) = all in one wave
```

On a **metered API** a cancelled copy is still billed for what it generated before it
was stopped: `hedge = 1` if cost matters more than time there. On a single-slot local
server (an `ollama` with `OLLAMA_NUM_PARALLEL=1`) the extra copies queue behind the first
ones, which are always sent first; there, set `max_concurrency` to the server's slots so a
queued request's `timeout_s` does not run out while it waits.

**What a review reports, and how to finish one that was partial.** Every chunk that was not
reviewed is named, with its files and its cause: a chunk whose generation ran out the
clock is labelled *did not converge* (the model was reachable), distinct from an endpoint
that was down. No chunk is header-only and none is dropped; hunk-header context is stripped
so a finding cannot cite a function the diff does not touch. Progress is printed per chunk,
the caller's lease is renewed while the review runs, and `extra_rules` on a `[[reviewer]]`
reaches its prompt. Findings are numbered `#1..#N`.

**A review that outlives its caller keeps its findings.** Each chunk's whole reply is
appended to `.ddflow/local/reviews/<item>.<gate>.<reviewer>.<run>.jsonl` (git-ignored; one file per reviewer and run, so a re-review never overwrites an earlier one) the moment it arrives,
and the gate evidence records `output_file` (repo-relative) and `output_digest` beside the per-finding
text, so a call that is cut off (a long critic over MCP) loses nothing. Over MCP,
`ddflow_review` sends `notifications/progress` for each progress line when the client
passed a `progressToken`, which resets a client's idle timer.

```sh
ddflow review T1 --gate critic --chunk 2,5      # re-review only chunks 2 and 5 of that review
ddflow review triage T1 --gate critic --finding 3 --refuted --probe "tests/test_x.py::t shows..."
ddflow review triage T1 --gate critic --finding 1 --confirmed --probe "fixed in 4f2a, test_y"
```

`--chunk` re-runs the named chunks of the **same cut** — the same diff, `max_chunk_chars`
and reviewer, checked before anything is sent — and merges their coverage into the gate's
recorded outcome. `review triage` records what became of each finding as its own event
(`review.triaged`): *refuted*, with the run that shows it false, or *confirmed*, with the
fix or test that answers it. The gate's outcome is not changed — a review that reported
findings stays `failed`, and that does not block completion; the log now shows what became
of each finding (once the round budget is spent, the triage that settles the LAST finding
records the gate passed on refutation, flagged: see below). Likewise, when every finding of a failed round was *refuted*, the triage that settles the last one records the gate passed on refutation at once, flagged and with each probe as evidence (`[review].settle_all_refuted`, default true) -- no hand-recorded pass is needed. Triage appears in `gate status` and `show`, and a re-review carries it over
only for a finding whose text is identical (decision D-review-triage). Over MCP it is
`ddflow_review_triage`; `--chunk` is an argument of `ddflow_review`. The `triage` verb is
required: `--finding`/`--refuted`/`--confirmed`/`--probe` on a plain `review` are refused
(exit 1) before any reviewer is contacted, as is `--chunk` on `review triage`.
Finding numbers are per gate, so `--gate` is never defaulted on `review triage`: omitted, it is
refused (exit 1) naming the gates that have findings when several do, and resolved to that gate
(the output names it) when exactly one does. `ddflow_review_triage`'s `gate` works the same.

`ddflow review show <item> [--gate G]` lists the recorded round's numbered findings -- the `#N` that
`review triage` takes -- each with severity, location, digest, triage state and probe; omit `--gate` for every
gate that has a round (a gate whose findings blob is gone is named, not hidden). Over MCP,
`ddflow_review_triage` without `finding`, `verdict` and `probe` lists them.

**The review-round budget (`[review]`).** A shipped default in every project, with no
configuration: a gate (`rubber_duck`, `critic`) gets **2 review rounds** per item, full and
delta alike (decision D-gate-economy 2: a delta after the cap was the way around it), then a
third is refused (exit 3) with the way forward — later rounds each find fewer defects than
the one before (measured: rounds 3 and later yielded about a quarter of the confirmed findings). What stays allowed after the cap:

```sh
ddflow review triage T1 --gate critic --finding 2 --refuted --probe "..."   # settle what is left (always allowed)
ddflow review T1 --gate critic --force --reason "..."   # one more round; the reason is recorded
```

**Passed on refutation** (decisions D-gate-economy 2, D-unify 5). Once the budget is spent,
the `review triage` that gives the gate's LAST finding a verdict records the gate `passed`
itself: the review's own evidence and reviewer, plus `passed_on_refutation` (how many
findings were refuted and confirmed, and the rounds used). The pass is flagged, never
silent: `gate status` shows `-- PASSED ON REFUTATION` beside the triage counts, `status`
counts such gates (`refuted_passes` in `--json`), the brief names those of unfinished
items, `complete` prints a `PASSED ON REFUTATION:` line for each (`refuted_passes` in its result), and `ddflow gate list --refuted` (MCP `ddflow_gate_list` with
`refuted=true`) lists every one for the operator to spot-check; `--since <ISO date or timestamp>` (MCP `since`) keeps the ones recorded at or after it (refused without `--refuted`). Without `--refuted`, `gate
list` lists the gates the project defines. A gate whose findings were ALL confirmed (and fixed), none refuted, is recorded `passed` too but is not flagged: `gate status` shows `-- findings fixed` and `gate list --refuted` leaves it out; a mix of refuted and confirmed is flagged with both counts. A finding still without a verdict holds the gate where it is,
and the triage output says so: settle it, or ask the operator for one more round
(`--force --reason`, theirs to grant). With `review.max_rounds = 0` (no budget) a gate is
never passed this way; a re-review settles it.

A *full* round is any review that can cover the item's whole diff. What is not one is
judged by what it covers, not by the flag: `--delta`, a `--commit <sha>` or `--base <ref>`
at or after the head the gate's last review covered (so it can only be narrower), and a
`--chunk` re-run of a recorded review. A delta still counts against the cap (a `--chunk`
re-run does not: it repeats part of a recorded round); a `--base` or `--commit` that reaches
back past that head counts as a full round. Rounds are
counted from the log's recorded reviews (`review_kind`, `round`, `rounds` and `reviewed_head`
in the gate evidence), so a re-claim, a delta or a manual `gate skip` does not reset the
count; a round that reached no reviewer is not counted. `--delta` reads the reviewed head
from the log too, so it works after a re-claim or a `gate skip`; it is refused only when no
review was ever recorded or nothing changed. A delta is the item's OWN change: after
`git merge main` into the branch, the commits that came in from the base branch are not
sent (they were reviewed as their own items). The delta diffs the tip against the reviewed
head merged with what came in (keeping the incoming side where the two conflict), so the
branch's own commits and where its merge result departs from main are what the reviewer
sees: a conflict resolved the item's own way (overriding another item's change) is
reviewed, one where it took main's side is not. Criss-cross histories (more than one merge
base) merge each base the reviewed head lacks. When that merge cannot be written (a
modify/delete conflict, say), the delta is the item's whole own change on top of the
merged-in main commit, or, with several merge bases, everything since the reviewed head. A
delta of the item's tree or branch whose diff cannot be produced is refused with the
reason, never reported as "nothing changed".

**A re-review sees the whole diff and what was said last time** (decision
D-gate-economy 3). Once a gate has a recorded review, a plain `ddflow review T1 --gate
critic` is a full round over the item's whole diff against its base, and the prompt lists
each earlier finding with the author's triage -- `confirmed` (check the fix), `refuted`
(check the probe) or `untriaged` -- asking the reviewer to report one again only if it still
holds, then to look for new issues; the output says `re-review: the whole diff of T1, with N
previous critic finding(s) and their triage`. A delta that showed the reviewer only the
follow-up commit kept reporting the fix as absent, because the code it fixed was not in
what it saw. A re-review counts against `review.max_rounds` like any full round.

**One review for both gates of a small change** (decision D-gate-economy 2). `ddflow
review T1 --gate rubber_duck,critic` on a change with fewer than
`review.combined_under_lines` changed lines (default 150; `0` = never) sends ONE reviewer
request whose intent names both lenses -- refute the change, and criticise its design and
tests -- and records the outcome for each gate with the same evidence and a shared
`review_id` (each gate's findings are triaged on that gate); the output starts `combined
review <id>: N changed line(s) ...`; it is a full round of each gate. A larger change runs
one review per gate, one after the other. `--chunk`, `--delta`, `--commit` and `--base` work
on one gate's record, so with several gates they run per gate; so does a pair where either
gate is out of rounds, is served by other reviewers than the first, or is not one of the two
review lenses.

**Delta re-reviews, on request.** `--delta` (MCP `delta=true`), or `review.delta_default =
true` for every plain re-review, is for a diff too large to send twice. Once a gate has a
recorded review that reviewed the whole diff, a delta reviews only the commits
since the head that review covered (`reviewed_head`), says so (`delta review of 1 commit
since a1b2c3d4e5`), is not a full round but counts against the cap like one. Its findings and
coverage are **merged into the gate's record**: earlier findings stay where they were
(their `#N` and their triage, which is keyed by the finding's exact text, are unchanged),
a byte-identical finding is not duplicated, the delta's new ones are appended and the
output maps each to its place in the record; the gate's outcome is the delta's own, except
that a clean delta does not pass while an earlier finding on the record has no triage
verdict (it records `failed` and says so; `review triage` settles each, then the next clean
delta passes). `gate status` shows the rounds separately (`1 full round, 2 delta rounds`).
`--full` forces a full round (counted against `review.max_rounds`; combined with `--delta`
it is an error). A delta falls back to a full round, and says why, when the reviewed head
is no ancestor of the current head (the branch was rebased or amended) or, for the
automatic delta, the earlier review was partial; a review that never reached a reviewer
records no reviewed head at all, so the next delta starts from the last real one; with nothing changed since the reviewed
head the review is refused and names `--full`. A `--force --reason` review is a full round, like `--full` (and, like `--full`, an error with `--delta`); a `--chunk`, `--commit` or
`--base` review is not second-guessed. `review.delta_default = false` (the default) makes
every re-review a full round with the previous findings. Knobs, changeable at every layer:

| knob | default | meaning |
|---|---|---|
| `review.max_rounds` | `2` | review rounds (full and delta) per gate per item; `0` = unlimited |
| `review.on_exceed` | `"refuse"` | `"warn"` runs the round and says the budget is spent |
| `review.combined_under_lines` | `150` | changed lines under which `--gate rubber_duck,critic` is ONE review recorded for both; `0` = never |
| `review.delta_default` | `false` | `true` = a review of a gate with a recorded review is a delta; `false` = a full re-review with the previous findings |

```sh
ddflow config review.max_rounds 3                 # this project (committed .ddflow/config.toml)
ddflow config review.max_rounds 0 --local         # this machine only (.ddflow/local/config.toml)
ddflow config review.delta_default true           # automatic deltas again (add --local for this machine)
ddflow review T1 --gate critic --full             # one full round, whatever the default
                                                  # (`--set KEY VALUE` is the same)
DDFLOW_REVIEW_MAX_ROUNDS=0 ddflow review ...      # one run
```

Over MCP, `ddflow_configure` accepts `review.max_rounds`, `review.on_exceed` and
`review.delta_default` on either layer (`local=true`), and tells the operator in its reply
and in a session note — an agent does not lift the cap for itself; `--force --reason` is
CLI-only and `ddflow_review` takes `delta=true` and `full=true`. `ddflow config --explain
--filter review.` documents the knobs. A tool
built before this knob skips an unknown `[review]` key with a note rather than failing.

### Companion tools

ddflow imposes the order and demands the evidence. It does not *perform* the judgement
inside most of its gates: `standards` wants an automated standards review, `research`
wants documentation to check a claim against. (`rules` wants memory of the last time
somebody hit this, and ddflow serves that one itself: `ddflow brief` shows the lessons
and the operational memory.) A project that installs ddflow and stops has the others
wired to nothing — and because an agent gate passes on an assertion, that gap is invisible in
exactly the way the rest of this design exists to prevent.

So the gap is **named**:

```console
$ ddflow companions
Companion tools

  [x] context7   Current library documentation
       gates: research, standards
       registered for: claude, cursor
  [x] roborev    Automated second-opinion code review
       gates: standards, bug_hunt, dedupe
       installed (roborev 0.9.1). A cli tool — the agent shells out to it, so
       there is nothing to register.
  [+] codeguide  Language and framework coding standards
       gates: standards
       installed (…) but no agent is configured to launch it.
       -> ddflow companions add --id codeguide
  [ ] sequential Structured step-by-step reasoning
       gates: research, rubber_duck, bug_hunt
       not here: `npx --no-install @modelcontextprotocol/server-sequential-thinking` exited 1
       -> ask the operator, then: npx -y @modelcontextprotocol/server-sequential-thinking

Gates in this project's task pipeline with no companion behind them:
  implement, rubber_duck, critic, unit_tests, bug_hunt, dedupe, merge
```

The gap is also told to the agent, not only shown: the MCP handshake and the
SessionStart brief (hook path, nothing probed, "not checked" stated as such) both list the
default companions that are not wired up. To install them with the operator's consent run
the `install-companions` prompt (MCP: `/mcp__ddflow__install-companions`; `ddflow prompts
show install-companions` elsewhere).

A companion counts as **registered** only when an entry under its id can actually launch
something: a bare or junk table with its name does not hide a real launch registered
elsewhere, and does not pass for one. A non-table `mcp_servers` is refused with the parser's
reason, and `companions add` appends TOML only when the result still parses.
A stale entry under the id is refreshed in `.codex/config.toml` too, as in `.mcp.json`: the
old `[mcp_servers.<id>]` table (and its sub-tables) is cut out and the registry's launch
appended. A table that cannot be cut out safely (an inline or dotted form) is refused with
"replace it by hand", and a launch that already runs under another name is not doubled.

Three states, reported separately because the remedies differ: **registered**,
**installed but not wired up** (one command away), **not installed** (with the command
and the URL). `ddflow adopt` prints the same summary, so the gap is visible at
adoption rather than discovered six tasks later. Exit 2 when a default companion is
missing — "no data", never collapsed into "no problem".

A server is found by its launch as well as its name. A project that registered
codeguide-mcp as `coding-guides` before ddflow knew it reads as
``registered for: claude (as `coding-guides`)``, and `companions add` leaves it alone
instead of writing a second copy under the id. "The same launch" is the same command
(by basename) with the companion's arguments in order — only flags, and the value of a flag known to take one
(`-e TOKEN`) may sit between them, the server's own arguments may follow, and a version
tag on an npm package (`@latest`, `@1.2.3`) is ignored. Another image, package or
launcher is another server, and so is reordered arguments.

`rules` never shows as uncovered: `ddflow brief` serves it from ddflow's own lessons
and operational memory (`ddflow memory add|list|forget`), which is the job the memory
companions below were once recommended for.

| | Serves | Why |
|---|---|---|
| **roborev** *(cli)* | `standards`, `bug_hunt`, `dedupe` | Cross-file duplication analysis, which is the failure mode of agent-written code specifically: an agent changing replicated logic reliably updates one copy and misses the rest |
| **codeguide** | `standards` | Checks against a written standard instead of the reviewer's taste |
| **context7** | `research`, `standards` | A model's memory of a library's API is exactly the kind of claim that is cheap to check and often wrong |
| **memory** *(opt-in)* | `rules` | A machine-local knowledge graph, for a project that wants one. ddflow already keeps operational facts itself (`ddflow memory`), so this is a second store outside the committed log |
| **sequential** *(opt-in)* | `research`, `rubber_duck`, `bug_hunt` | Records and counts the thoughts you send it; returns no thought text, persists nothing, no search or validation. Benefit to the reasoning gates is unmeasured |
| **optmem** *(cli, opt-in)* | `rules` | Superseded by `ddflow memory`, which holds the operational facts OptMem was recommended for; listed for a project that wants an OptMem store anyway. An existing OptMem `LOG.txt` imports as ddflow memories |

**Servers and command-line tools are different things**, and the registry says which:
`kind = "mcp"` is registrable into an agent's config, `kind = "cli"` is a tool the agent
shells out to. OptMem and pre-commit are examples — real tools with no MCP mode, so
`companions add` refuses them and says why instead of writing a launch entry that would
fail its first handshake. A `cli` companion counts toward its gate's coverage once it is
**installed**; `registered` is a state it cannot reach.

**Your stack needs servers this registry cannot know about.** `ddflow prompts show
research-companions` walks an agent from the pipeline's *uncovered* gates, through the
repository's actual manifests, to candidates checked against their primary sources —
provenance, maintenance, what they execute, what credential they want — and produces
`[[companion]]` blocks you can read and delete. It proposes; you install. A rejection is
part of its report, so the next session does not re-research it.

**Registering is previewable.** `ddflow companions add --dry-run` (and
`ddflow_companions_add` with `dry_run=true`) reports the exact config entry it would
write and writes nothing — not the file, not even its parent directory. The handshake
tells an agent to dry-run first and show the operator the actual entry rather than a
description of it, because registering changes which processes their agent launches.
The preview is asserted to match what the real write produces; a preview that drifts
from the write is worse than none, since the operator has now signed off on it.

**ddflow never installs anything itself** — running an install command on someone's
machine is the operator's decision. What it does instead is *instruct the agent to ask*:
the MCP instruction block lists each missing companion with the gates it serves and the
exact command that would install it, and tells the agent to put that to the operator
early, install it if they agree, and record the affected gates `unavailable` if they
decline. Never on its own word.

`companions add` also refuses to register a server that is not present: that writes a
launch command which fails mid-task, at the moment a gate told the agent to reach for
it. Detection is read-only and bounded — and when it has not run, the state is reported
as **unknown**, not as absent. `ddflow companions` probes; the MCP handshake does not,
because making an agent wait on `npx` before it can do anything is the wrong trade.

**Verifying that a companion is a server.** Detection says a binary of that name exists;
it cannot say the binary speaks MCP (B113 was a registry entry whose launch command was
not a server at all). `ddflow companions --verify` (MCP: `ddflow_companions_verify`)
spawns each MCP companion's `command args`, sends a JSON-RPC `initialize` on stdio and
requires a response, then stops it. A response, even a JSON-RPC error, is `speaks_mcp`;
a missing command, an early exit, or a binary that only echoes the request back (`cat`)
is `not_mcp`, exit 1; no answer within 30 s is `unknown`, exit 2, because a cold `npx`
cache is slow and silence is not proof. Without `--id` it launches only companions that
are registered with an agent or detected as installed (launching a registry entry that is
merely known would download it); `--id a,b` launches exactly those. It is opt-in and
**never on the scan path**: `ddflow companions` and the MCP handshake still launch nothing.

Adding another is a TOML block in `.ddflow/companions.toml`, not a patch:

```toml
[[companion]]
id      = "my-linter"
title   = "House linter"
gates   = ["standards"]
detect  = ["my-linter", "--version"]
command = "my-linter"
args    = ["mcp"]
install = "cargo install my-linter"
```

## Testing

```sh
uv run pytest tests/ -q -n auto      # unit/integration tests, in parallel (pytest-xdist)
python3 demos/run_all.py             # 6 end-to-end scenarios, 219 assertions
```

While working on an item, `ddflow tests --item <id>` (MCP: `ddflow_tests`) lists the
tests the change reaches — changed test files, tests importing a changed module directly
or one step removed, tests named after a changed file, everything under a changed
`conftest.py`, tests that name a changed data file kept below a test directory (a
fixture, a golden file, a guard baseline: by its name with its directory, else by its
name or its nearest directory), and the tests of repo-wide structure (the architecture guards,
the lifecycle layout, packaging) whenever a path they govern changes: a test file says what it
checks with a module-level `GOVERNS = ("ddflow/api/**", ...)` of globs — each with why, and prints one command that runs them in parallel
with the project's own runner and worker flags. The agent driver tells agents to run it
after each change; `ddflow workflow` and `ddflow doctor` say when the configured test
command uses one core.

**A failing gate keeps which tests failed.** The evidence of a `ci` or `unit_tests` run that fails
holds `failed_tests` (the ids, from pytest's short summary or unittest's `FAIL:`/`ERROR:`
headings, read from ALL of the output, capped at 50), `failed_reasons` and `failure_tails`
(the last lines of each failure section, capped), and `gate run` and `ci run` print them: the
summary line alone said "3 failed" and nobody could tell which three. A test that FAILED
and then PASSED on the same tree is a flake, and goes to the machine-local log
`.ddflow/local/flakes.jsonl` (never committed) with its commit, the host load and the failure
text: either the gate was run again on identical content and passed, or `ddflow gate run <id>
<gate> --rerun-failed` ran only the failed tests once more and they passed. `ddflow tests
--flakes` shows the log; `ddflow doctor` names a test that flaked three times. The rerun
changes nothing about the verdict (decision D-failed-gate-rerun): both outcomes are in the
evidence (`rerun`), the first failure stays, and the gate is passed only by running the gate
itself again until it passes.

**Where the whole suite runs** (decision D-gate-economy 1). The `ci` gate runs it on the
branch merged with the base. Once ci has **passed on the commit the item's tree holds
now, with nothing uncommitted** (ci tests the committed HEAD), `ddflow gate run <id> unit_tests` for a **bug fix** (a task that fixes a
bug) or a **small task** (fewer than `gates.unit_tests_small_lines` changed lines since its
base, default 150) runs only that selection, plus the pytest regression tests of the bugs
it fixes, and its evidence says so: `scope = "selected"`, `scope_why`, `changed_lines` and
`selected_tests` (each path with why). So run `ci` first. Everything else runs the whole
suite in `unit_tests`, in parallel, because a targeted run hides breakage that was already
there: a ci that has no outcome, was skipped, could not run, failed, ran over uncommitted
edits or on another commit (an edit after it); a phase, a promotion, a larger task; `gates.unit_tests_scope = "full"`;
and any selection that cannot be made -- no base, git cannot say what changed, no test
reaches the change (running nothing would pass vacuously), or a command that does not run
pytest. `scope_why` names which.

| knob | default | meaning |
|---|---|---|
| `gates.unit_tests_scope` | `"selected"` | `"full"` = every item's unit_tests gate runs the whole suite |
| `gates.unit_tests_small_lines` | `150` | changed lines under which a task is small; `0` = only bug fixes select |

The demos invent whole projects and drive them for real — real git worktrees, real
`pytest` and `npm test` runs, real merges, real concurrent processes:

| Scenario | What it proves |
|---|---|
| `parallel-phase` | Two agents build a URL shortener in parallel; a third is refused on a file conflict; a dependent task unblocks automatically when its last dependency lands |
| `crash-recovery` | An agent is killed holding uncommitted work; it is found, measured, never stolen, and adopted intact on resume |
| `reconstruct-from-log` | The entire repository is deleted; everything rebuilds from 3.9 KB of JSONL, and nine specific facts are checked present |
| `mcp-polyglot` | A Node.js project driven end-to-end over real MCP JSON-RPC, with both surfaces asserted to agree |
| `mcp-orchestration` | **A whole two-phase Python library built by two agents entirely over MCP** — bootstrap, configure, discover a reviewer, fan out, get refused by the hook, real pytest, a real cross-family review, merge, close both phases, reconstruct. 24 steps, 57 assertions. |
| `full-lifecycle` | **26 steps, 89 assertions — the whole arc, from an operator's first sentence to a rebuild from the log.** A double-entry bookkeeping library across two dependent phases with sub-tasks: the operator states requirements in English, a decision is recorded and scoped to the files it governs, two agents fan out, a task turns out to be two concerns and grows sub-tasks, a bug is found and may not be closed without its regression test, a phase closes on its own pipeline, a new requirement arrives **while a task is in flight**, that task is **split in place**, the guardrails are tested by trying to break them, and finally every `.py` file is deleted and the project is reconstructed from the log alone. |

**The scenarios and the stress test have found most of the bugs this project fixed; the unit tests found few of them.** The `full-lifecycle` scenario was written to exercise the *requirements* rather than the code, and found four defects before it passed once — every one of them a CLI/MCP divergence that command-level parity could not see. The composed MCP run alone found eight that 213 unit tests and four other scenarios missed — including two that made core features useless out of the box. They all lived in *seams*: between two processes, between a read and a write, between two output surfaces, between a declared vocabulary and its callers, including one that does
not reproduce below ~6 concurrent processes. They are catalogued with their regression
tests in [R6](../../docs/RESEARCH.md#r6--bugs-this-project-found-in-itself).

---
