# Upgrading ddflow

    ddflow upgrade --check      is a newer release out, and is this project behind? (exit 1 = yes)
    ddflow upgrade --plan       what the running version would change in this project
    ddflow upgrade --apply      do it, after saving the originals (--confirm KEY --reason WHY)
    ddflow upgrade --self       install the newer release (--yes --reason WHY)
    ddflow upgrade --status     where it stands: versions, steps and their states, backups, agents behind
    ddflow upgrade --history    the upgrades this project's log records
    ddflow upgrade --resume     finish an interrupted --apply without repeating its finished steps
    ddflow upgrade --restore    put back what a backup holds

Over MCP these are the one tool `ddflow_upgrade`: `check`, `plan`, `apply`, `self`, `status`,
`history`, `resume`, `restore`. An apply reports each step as progress while it runs.

## For an agent

- Propose an upgrade to the operator; never install a new ddflow without their consent.
  `--self` and `ddflow_upgrade` with `self` need it, recorded as `--yes --reason` /
  `confirm: ["self"]` and `reason`.
- Asked "is ddflow up to date?": run `status` (or `check`) and report what it says: the
  running version, the newest release, the project's stamp, the steps still pending.
- Asked "what did the upgrade do?": run `history`, then `status` for the plan that is left.
- `status` names the agents and MCP servers last seen on an older version: tell the operator
  those must restart.
- A step in state `needs operator confirmation` is the operator's call; ask, then
  `apply` with `confirm` and the reason they gave.
- An interrupted apply shows as `interrupted` in `status`; `resume` finishes it.

## Turning it off

`ddflow config upgrade.release_check off` (or `DDFLOW_NO_UPDATE_CHECK=1`) stops the release
check; `upgrade.on_start off` stops the check on a server or container start;
`upgrade.self operator` keeps an agent from installing. Nothing here runs on a timer.
