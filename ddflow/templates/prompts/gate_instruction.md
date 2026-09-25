**{{ gate.title }}** — {{ gate.description }}

{{ gate.prompt }}

{% if gate.human %}
This one is **not yours to record**. It is a human-approval gate: the operator decides
whether this work proceeds, before the compute is spent. Show them what you are
proposing, then ask them to run:

    ddflow approve {{ item }} {{ gate.id }}
    ddflow approve {{ item }} {{ gate.id }} --reject --reason '<why not>'

`ddflow gate record` and `ddflow gate skip` both refuse this gate (exit 3), and there
is deliberately no MCP tool for it. Nothing has gone wrong when they refuse — you are
simply not the party who can clear it.
{% else %}
When you are done:

    ddflow gate record {{ item }} {{ gate.id }} --outcome passed --evidence '<what you ran / what it said>'

If it could not run — tool missing, endpoint down, no reviewer configured — record that
honestly instead. It is a coverage gap, not a failure, and never a pass:

    ddflow gate record {{ item }} {{ gate.id }} --outcome unavailable --reason '<why>'
{% endif %}
