**{{ gate.title }}** — {{ gate.description }}

{{ gate.prompt }}

When you are done:

    orchard gate record {{ item }} {{ gate.id }} --outcome passed --evidence '<what you ran / what it said>'

If it could not run — tool missing, endpoint down, no reviewer configured — record that
honestly instead. It is a coverage gap, not a failure, and never a pass:

    orchard gate record {{ item }} {{ gate.id }} --outcome unavailable --reason '<why>'
