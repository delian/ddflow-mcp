# Z Code harness id: rename glm to zcode and keep glm as a compatibility alias

Decision document for the task `ZC-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-zcode-harness-id`).

## D-zcode-harness-id: Z Code harness id: rename glm to zcode and keep glm as a compatibility alias

status **accepted** · decided by operator · recorded by Monster3-bridge-cse_01TtjTtBjYUURHw5euBdHh8z-7267fe

**Context.** R-zcode-support; ZC-decide. The other ZC-decide questions (hook delivery under workspace trust, scope, GLM as a model family, installing zcode for the probe) are still open.

**Decision.** Operator 2026-10-31 (chat answer to ZC-decide question 1): the existing 'glm' harness (family zcode, title Z Code (GLM)) is renamed to 'zcode' and 'glm' stays as a compatibility alias (D-compat): old configs and records that say glm keep working and say once that the name is old.

Governs: `ddflow/harnesses/glm.toml`, `ddflow/harnesses/zcode.toml`
