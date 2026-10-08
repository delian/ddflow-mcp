"""One guidance engine for rules and decisions (B-uni-guidance-core, D-unify).

* `record`   -- the shared record, its scope, and what a kind adds to it;
* `kinds`    -- `rule` and `decision`, and the adapter from a logged decision;
* `resolve`  -- which guidance governs this work, and why;
* `fileformat` / `store` -- the authored ``.ddflow/<kind>s/*.toml`` format and its files;
* `limits`   -- per-kind limits and the lint every record passes;
* `similarity` -- how alike two pieces of guidance read.
"""
