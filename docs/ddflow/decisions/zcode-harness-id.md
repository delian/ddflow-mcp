# Z Code harness id: rename glm to zcode and keep glm as a compatibility alias

Decision document for the task `ZC-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-zcode-harness-id`).

## D-zcode-harness-id: Z Code harness id: rename glm to zcode and keep glm as a compatibility alias

status **accepted** · decided by operator · recorded by Monster3-bridge-cse_01TtjTtBjYUURHw5euBdHh8z-7267fe

**Context.** R-zcode-support; ZC-decide. The other ZC-decide questions (hook delivery under workspace trust, scope, GLM as a model family, installing zcode for the probe) are still open.

**Decision.** Operator 2026-10-31 (chat answer to ZC-decide question 1): the existing 'glm' harness (family zcode, title Z Code (GLM)) is renamed to 'zcode' and 'glm' stays as a compatibility alias (D-compat): old configs and records that say glm keep working and say once that the name is old.

Governs: `ddflow/harnesses/glm.toml`, `ddflow/harnesses/zcode.toml`

## Other answers to ZC-decide (all accepted, decided by operator 2026-10-31)

Quoted from the recorded decisions; `ddflow decision show <id>` has the full record.

| Question | Decision id | Operator's answer |
|---|---|---|
| 2. Hook delivery under workspace trust | D-zcode-hooks | Options 2 and 3 both supported, with option 3 (a ZCode plugin) preferred. Hook delivery is by a plugin the operator installs where ZCode supports it; otherwise project config plus an explicit workspace-trust step. User-level config is not the default (the recommended default, user-level config with consent, was not chosen). |
| 3. Scope of "all agents" | D-zcode-scope | "Every feature other harnesses have (Recommended)". ZCode gets hooks, lane driver, reviewer use, session ingest, install and doctor, as the other tier-1 harnesses. |
| 4. GLM as a reviewer family | D-zcode-glm-family | "Yes, family 'zhipu' (Recommended)". GLM models carry family zhipu and may serve as independent reviewers against an Anthropic author. |
| 5. Installing zcode for the probe | D-zcode-install-probe | "Yes, with a prompt each time (Recommended)". The agent proposes the exact install command and waits for the operator's answer; nothing installed unasked. |

Governs: `ddflow/harnesses/zcode.toml`, `ddflow/models/**`, `ddflow/services/lanehooks*.py`.
