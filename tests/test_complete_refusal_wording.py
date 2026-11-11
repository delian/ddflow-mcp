"""A completion refusal for a person-only baseline-red partial names `ddflow approve`, not an
agent approval request (B-complete-refusal-contradicts-approval-advice).

`gate_accept.next_step` everything, to the agent, "Do NOT request an approval: fix what it
names". The generic tail that survives it ("to ask the operator to override these in chat,
re-run with `--approval request`") contradicts it, so it must be dropped when the unmet gate
says the next step is that one, not an approval request.
"""

from __future__ import annotations

from conftest import run_cli
from test_accept_partial_gate import _partial_item

OK = 0


def test_a_person_only_partial_names_ddflow_approve_not_an_approval_request(repo) -> None:
    _partial_item(repo)
    code, out, err = run_cli(repo, "complete", "T1", agent="bot")
    assert code != OK, out + err
    # The next step for a person-only baseline-red partial is `ddflow approve`, so no generic
    # tail may point the agent at `--approval request` beside it.
    assert "Do NOT request an approval" in out + err
    assert "to ask the operator to override these in chat" not in out + err
