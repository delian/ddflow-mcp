"""Remote approvals (P-remote-approvals): an agent runs an operator-gated command with a
person's recorded, single-use, expiring approval bound to the exact command.

The protocol rides on `services/ask` (request, answer, consume); this package holds what is
specific to gated commands: the inventory of sites (`sites`), later the canonical command and
its digest, the request and the consume check. Decisions D-RA-Q1..Q12.
"""
