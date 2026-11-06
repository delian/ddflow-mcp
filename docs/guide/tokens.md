# Paired tokens

A paired token is how a client that is not you at your terminal proves who it is: a chat
gateway, n8n, a remote agent, and later `ddflow serve`. It has a name, which is the client's
identity, and a role, which says what it may do.

## Pair a client

At the host, from your own terminal:

```sh
ddflow pair openclaw --role bot          # add --ttl-h 24 for a shorter life
```

It prints a one-time code, valid for five minutes (`[agent].pair_ttl_s`). The client spends it:

```sh
ddflow tokens redeem                     # reads the code from stdin, prints the token once
export DDFLOW_TOKEN=...                  # how a client presents it
```

ddflow keeps only hashes, in `~/.config/ddflow/auth/` (`$XDG_CONFIG_HOME` if set), one private
file per project and never in the project. The log records `token.issued` (who, when, the role
and the expiry) and `token.revoked` (who, when and why), and no secret. A pasted token is masked by
`[session].redact_patterns`.

## What a token changes

* Its name is the identity of every event written with it. `--agent` and `as_agent` cannot
  override it.
* Its role is checked on every call. A verb is allowed when a capability of the role
  (`[agent].roles`) lists it in `[agent].capability_verbs`; a verb nobody lists is the
  operator's. A bad, expired or revoked token refuses the call, it never falls back.
* Every operator-only verb (`approve`, `approvals approve`, `grant add`, `reviewers approve` and
  the rest) asks the same check, so a `bot` token cannot run one even from a bare terminal, and
  an `operator` token can from anywhere. A caller with no token is another matter: see below.
* `DDFLOW_TOKEN` is removed from the environment once read, so a lane or gate started by that
  call does not inherit it. `ddflow mcp` ignores it: a stdio server speaks for whichever agent
  connects.

## Manage tokens

```sh
ddflow tokens list [--all]               # names, roles, dates; --all adds expired and revoked
ddflow tokens rotate openclaw            # a new code; the old token works until it is redeemed
ddflow tokens revoke openclaw --reason "left the team"
```

Rotation, revocation and pairing are the operator's. There is no MCP tool for them: a token in a
tool result would sit in an agent's context and transcript.

## The gap that stays open by default

Tokens do not close the operator-only gap by default. With no token presented, the operator-only
verbs still ask only whether the caller carries an agent marker (`--agent`, `DDFLOW_AGENT`, a
harness variable), so a shell that unsets them passes as the operator, exactly as it did before
tokens existed. This is the operator's decision (D-operator-token-default): shell access to the
machine is the trust boundary.

What tokens do change: a call that presents one is held to its role, always.

To close the gap, set `[agent].require_operator_token = true`: those verbs then need an operator
token. While no live operator token exists, `ddflow pair --role operator` still works from a
marker-free terminal, for the first one or after the last has expired, so the operator cannot be
locked out.

Even then, it stops accidents and prompt injection locally, not a hostile process of your own
user, which can write the store and the config. A role is a hard boundary only when the client
reaches ddflow through a server on a host it cannot otherwise touch.

## Knobs

`[agent]`: `token_ttl_h`, `token_max_ttl_h`, `pair_ttl_s`, `pair_code_length`,
`pair_max_attempts`, `capability_verbs`, `require_operator_token`. `ddflow config --explain` has
each one's text. The roles themselves are `[agent].roles`.
