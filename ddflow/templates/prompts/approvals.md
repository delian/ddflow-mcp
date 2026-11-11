<!-- section: driver -->
An operator-only act is refused to you: file it with `ddflow approvals ask` (MCP: `ddflow_session action=ask`), show the operator the question it returns, then re-run the command with `--approval ID` once they have answered. Never approve it yourself.
<!-- section: route -->
This is the operator's act, not yours. Ask them in chat: run `ddflow approvals ask {{ ask_id }}` (MCP: `ddflow_session action=ask`) and show them the question it returns. When they have answered, re-run this command with `--approval {{ ask_id }}` (`ddflow approvals attest {{ ask_id }}` records a chat answer; `ddflow wait --approval {{ ask_id }}` blocks until it is answered). The approval is single-use, bound to this exact command and its current state, and expires.
