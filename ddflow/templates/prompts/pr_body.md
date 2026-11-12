{#
  The pull-request description (`[flow].pr_body_template`, BL-pr-evidence-template). Rendered only from
  derived records and passed through the secret redactor. Variables: item, title, body, stacked_on,
  stacked_on_url, gates (gate, outcome, note, rounds, triage_confirmed, triage_refuted),
  bugs (id, summary, regression_tests), changelog (category, line).
    ddflow prompts eject pr_body   -> .ddflow/prompts/pr_body.md
#}
{{ body }}

{% if stacked_on %}**Stacked on {{ stacked_on }}** ({{ stacked_on_url }}). Review that first; this request is retargeted automatically once it merges.

{% endif %}{% if gates %}| gate | outcome | note |
|---|---|---|
{% for g in gates %}| {{ g.gate }} | {{ g.outcome }}{% if g.rounds %} ({{ g.rounds }} round(s)){% endif %}{% if g.triage_confirmed or g.triage_refuted %} [{{ g.triage_confirmed }} confirmed, {{ g.triage_refuted }} refuted]{% endif %} | {{ g.note }} |
{% endfor %}
{% endif %}{% if bugs %}Bugs fixed:
{% for b in bugs %}- {{ b.id }}: {{ b.summary }}{% if b.regression_tests %} -- regression test: {{ b.regression_tests | join(", ") }}{% else %} -- NO regression test recorded{% endif %}
{% endfor %}
{% endif %}{% if changelog %}Changelog: {{ changelog.category }}: {{ changelog.line }}

{% endif %}Item: {{ item }}
