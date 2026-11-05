# Adapter family `notify`

Port: **HumanChannel**, declared in `ddflow/adapters/notify.py` and marked `@port`.

One way to reach a person: `notify(message, severity)` returns a `Delivery`, `ask(question, options)` an `Answer` with who answered and how, and `relay(approval)` the same for an `Approval`, echoing the digest the person was shown. No method raises for the channel's own trouble: that is a status. `failed` means the other end refused, `unavailable` that it could not be tried, and neither is ever an answer.

- **Adapters:** `CHANNELS`, a `Registry` keyed by `name`. `terminal` and `harness` are in `ddflow/adapters/notify.py`; `desktop`, `email`, `slack`, `teams`, `webhook`, `n8n` and `openclaw` in `ddflow/adapters/notify_outbound.py`, loaded when first named.
- **Fake:** `FakeHumanChannel` in `ddflow/adapters/fakes/notify.py`: scripted answers, every call recorded. An `Answer` in the script is returned as it stands, so a test can have a channel go pending, go away or lie.
- **Conformance cases:** tests/test_adapter_notify.py. The service half is tests/test_ask_channels.py.

| Adapter | `notify` | `ask`, `relay` | Needs |
|---|---|---|---|
| `terminal` | stderr | answered, from a real terminal only | stdin is a tty (over MCP it is the protocol, and is never read) |
| `harness` | unavailable: a chat has no push | pending with `courier`, the exact AskUserQuestion input; `relay` answered once `approvals attest` finds the answer | an agent to courier it |
| `desktop` | `notify-send` (Linux), `osascript` (macOS) | unavailable | the command; other platforms are unavailable |
| `webhook`, `n8n` | JSON POST | pending | consent `webhook`, endpoint |
| `openclaw` | `/hooks/agent` with the hook token as bearer | pending | consent `webhook`, endpoint, token |
| `slack`, `teams` | incoming webhook; Teams takes an Adaptive Card from a Workflows webhook | pending | consent `chat`, endpoint |
| `email` | SMTP over STARTTLS, no fallback to plain text | pending | consent `email`, server, sender, recipient |

**Where an answer is recorded.** A channel reports and never decides: it holds no identity check and no digest comparison, and this layer imports nothing above it. `ddflow.services.ask.relay.relay` puts an ask on a channel and records what came back through `engine.answer`, under the caller's real identity, so the digest check, the one-time code and the refusal of an agent identity are in one place for every adapter. The harness bridge is the one channel that vouches for itself, through `approvals attest`, which proves the answer from the transcript the harness wrote; `relay` requires the row to be answered through that channel and returns the row's outcome, person and digest, not the channel's report. A question put through the bridge stays pending: nothing reads a plain question's answer back from a transcript yet.

**Consent.** `ddflow.services.ask.channels` builds a channel from its `Settings`. Chat, webhook and email need a person's consent once per kind (D-adapters-answers-3): `grant_consent` records it through the approve-by-digest primitive, which refuses an agent identity, and without it `open_channel` returns an `UnavailableChannel` that sends nothing. No command grants it yet, and no `[notify]` knob fills `Settings` yet: ADP-notify-knobs adds both.

**Secrets.** A chat webhook's URL is its credential. An address whose value came from a committed layer (named in `Settings.committed`) makes its adapter unavailable, tokens and passwords are read from the variable a setting names, no `detail` quotes an endpoint, and every word that leaves goes through the `upstream` redaction profile. A digest, an ask id and a one-time code are the protocol's, so they are sent as they are.

The pattern is in [README.md](README.md).
