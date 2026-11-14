# Swarm headroom

`ddflow/services/swarm/headroom.py` reads the layered `[prices]` limit table
(`limit_routing.headroom`; no second table) for a lane's model and optional subscription id.

- **Hard filter.** Remaining USD at or below `[swarm] headroom_floor_usd` (default 0, so only an
  exhausted lane), or below the item's estimated cost, refuses the lane with a reason. No weight
  overrides it. A project or user layer may raise the floor; a caller argument never lowers it
  (`effective_floor`).
- **Score term.** `[swarm] w_headroom` (default 1.0) times the share of the limit still unspent.
  Zero ignores headroom in the score.
- **Unknown is neutral.** A lane with no limit on record (or a non-numeric one) scores 0.5 and is
  never refused; `limit_routing.pick`, which chooses a model on faith, stays stricter.

Set limits under `[prices] limit` and `reset`; a lane carries its `Standing` from
`headroom.standing(prices, model, spent, subscription=...)`.
