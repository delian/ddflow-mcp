"""`ddflow rule ...`: the whole CLI transcript (exit code, stdout, stderr) of one scripted
session, pinned before the verbs moved onto the declarative executor (B-uc-surf-registry).
Only the volatile parts (timestamps, the repo path) are normalised."""

from __future__ import annotations

import re
from pathlib import Path

from conftest import run_cli

_CONTENT = "write the failing test before the fix"
SCRIPT: tuple[tuple[str, ...], ...] = (
    ("rule",),
    ("--json", "rule"),
    ("rule", "list"),
    ("rule", "add", "--id", "r-a", "--title", "Tests first", "--content", _CONTENT),
    (
        "rule",
        "add",
        "--id",
        "r-b",
        "--title",
        "Naming",
        "--content",
        "snake_case names",
        "--tags",
        "style, naming",
        "--scope",
        "task",
        "--priority",
        "70",
        "--globs",
        "a/*.py, b/*.py",
    ),
    ("--json", "rule", "add", "--id", "r-c", "--title", "Layers", "--content", "api over services"),
    ("rule", "add", "--id", "r-dup", "--title", "Tests again", "--content", _CONTENT),
    ("--json", "rule", "add", "--id", "r-dup", "--title", "Tests again", "--content", _CONTENT),
    ("rule", "add", "--id", "r-dup", "--title", "Tests again", "--content", _CONTENT, "--check"),
    (
        "--json",
        "rule",
        "add",
        "--id",
        "r-dup",
        "--title",
        "Tests again",
        "--content",
        _CONTENT,
        "--check",
    ),
    (
        "rule",
        "add",
        "--id",
        "r-dup",
        "--title",
        "Tests again",
        "--content",
        _CONTENT,
        "--related",
        "r-a",
    ),
    (
        "rule",
        "add",
        "--id",
        "r-x",
        "--title",
        "Tests yet again",
        "--content",
        _CONTENT,
        "--extends",
        "r-a",
    ),
    ("rule", "add", "--id", "r-y", "--title", "Tests once more", "--content", _CONTENT, "--new"),
    ("rule", "add", "--id", "r-b", "--title", "Naming", "--content", "again"),
    ("rule", "add", "--title", "No id"),
    ("rule", "list"),
    ("--json", "rule", "list"),
    ("rule", "list", "--tag", "style"),
    ("rule", "list", "--scope", "task"),
    ("rule", "list", "--scope", "nowhere"),
    ("--json", "rule", "list", "--scope", "nowhere"),
    ("rule", "search", "snake_case"),
    ("--json", "rule", "search", "snake_case", "--limit", "1"),
    ("rule", "search", "snake", "--exact"),
    ("rule", "search", "snake.*case", "--regex", "--tag", "style"),
    ("rule", "search", "zzzzqqq"),
    ("rule", "show", "r-b"),
    ("--json", "rule", "show", "r-b"),
    ("rule", "show", "r-nope"),
    ("--json", "rule", "show", "r-nope"),
    ("rule", "edit", "r-b", "--priority", "80", "--tags", "style"),
    ("rule", "edit", "r-b", "--title", "Naming rules", "--globs", "", "--scope", "project"),
    ("--json", "rule", "edit", "r-b", "--content", "snake_case, always"),
    ("rule", "edit", "r-b"),
    ("rule", "edit", "r-nope", "--title", "x"),
    ("--json", "rule", "edit", "r-nope", "--title", "x"),
    ("rule", "edit", "r-c", "--content", _CONTENT),
    ("--json", "rule", "edit", "r-c", "--content", _CONTENT),
    ("rule", "edit", "r-c", "--content", _CONTENT, "--new"),
    ("rule", "edit", "r-c", "--content", _CONTENT + " now", "--related", "r-a"),
    ("rule", "sync"),
    ("--json", "rule", "sync"),
    ("rule", "remove", "r-c"),
    ("--json", "rule", "remove", "r-b"),
    ("rule", "remove", "r-nope"),
    ("--json", "rule", "remove", "r-nope"),
    ("rule", "list"),
)


def normalise(text: str, repo: Path) -> str:
    text = text.replace(str(repo), "<repo>")
    text = re.sub(r"\d{4}-\d\d-\d\dT[\d:.+Z-]+", "<time>", text)
    return re.sub(r"\b[0-9a-f]{10}\b", "<hex>", text)


def transcript(repo: Path) -> list[tuple[tuple[str, ...], int, str, str]]:
    out = []
    for argv in SCRIPT:
        code, so, se = run_cli(repo, *argv)
        out.append((argv, code, normalise(so, repo), normalise(se, repo)))
    return out


def test_the_rule_cli_transcript_is_unchanged(repo):
    got = transcript(repo)
    assert len(got) == len(EXPECTED)
    for g, e in zip(got, EXPECTED, strict=True):
        assert g == e, f"{g[0]}:\n got {g[1:]}\nwant {e[1:]}"


#: Generated from the transcript of the code before the executor (B-uc-surf-registry).
EXPECTED = [
    (("rule",), 2, "No rules found.\n", ""),
    (("--json", "rule"), 2, '{\n  "schema": "rule@1",\n  "rows": [],\n  "count": 0\n}\n', ""),
    (("rule", "list"), 2, "No rules found.\n", ""),
    (
        (
            "rule",
            "add",
            "--id",
            "r-a",
            "--title",
            "Tests first",
            "--content",
            "write the failing test before the fix",
        ),
        0,
        "added rule r-a\n",
        "",
    ),
    (
        (
            "rule",
            "add",
            "--id",
            "r-b",
            "--title",
            "Naming",
            "--content",
            "snake_case names",
            "--tags",
            "style, naming",
            "--scope",
            "task",
            "--priority",
            "70",
            "--globs",
            "a/*.py, b/*.py",
        ),
        0,
        "added rule r-b\n",
        "",
    ),
    (
        (
            "--json",
            "rule",
            "add",
            "--id",
            "r-c",
            "--title",
            "Layers",
            "--content",
            "api over services",
        ),
        0,
        "{\n"
        '  "schema": "rule_add@1",\n'
        '  "id": "r-c",\n'
        '  "candidates": null,\n'
        '  "related": null,\n'
        '  "options": null,\n'
        '  "extended": null,\n'
        '  "extended_kind": null,\n'
        '  "relation": null,\n'
        '  "dedupe_unavailable": null\n'
        "}\n",
        "",
    ),
    (
        (
            "rule",
            "add",
            "--id",
            "r-dup",
            "--title",
            "Tests again",
            "--content",
            "write the failing test before the fix",
        ),
        3,
        "",
        "Possible duplicate rule. It reads like:\n"
        "  r-a (project, score 1.00): Tests first [before, failing, fix, test, the]\n"
        "\n"
        "Answer: new (different rule), extends r-a (add to existing), duplicate_of r-a (same rule), or "
        "related r-a (a different rule about the same thing)\n",
    ),
    (
        (
            "--json",
            "rule",
            "add",
            "--id",
            "r-dup",
            "--title",
            "Tests again",
            "--content",
            "write the failing test before the fix",
        ),
        3,
        "{\n"
        '  "refusal": {\n'
        '    "reason": "Possible duplicate rule. It reads like:\\n  r-a (project, score 1.00): Tests '
        "first [before, failing, fix, test, the]\\n\\nAnswer: new (different rule), extends r-a (add to "
        "existing), duplicate_of r-a (same rule), or related r-a (a different rule about the same "
        'thing)",\n'
        '    "outcome": "refused",\n'
        '    "exit": 3\n'
        "  },\n"
        '  "schema": "rule_add@1",\n'
        '  "id": "r-dup",\n'
        '  "candidates": [\n'
        "    {\n"
        '      "id": "r-a",\n'
        '      "title": "Tests first",\n'
        '      "score": 1.0,\n'
        '      "scope": "project",\n'
        '      "tags": [],\n'
        '      "overlap": [\n'
        '        "before",\n'
        '        "failing",\n'
        '        "fix",\n'
        '        "test",\n'
        '        "the"\n'
        "      ],\n"
        '      "kind": "rule"\n'
        "    }\n"
        "  ],\n"
        '  "related": null,\n'
        '  "options": null,\n'
        '  "extended": null,\n'
        '  "extended_kind": null,\n'
        '  "relation": null,\n'
        '  "dedupe_unavailable": null\n'
        "}\n",
        "Possible duplicate rule. It reads like:\n"
        "  r-a (project, score 1.00): Tests first [before, failing, fix, test, the]\n"
        "\n"
        "Answer: new (different rule), extends r-a (add to existing), duplicate_of r-a (same rule), or "
        "related r-a (a different rule about the same thing)\n",
    ),
    (
        (
            "rule",
            "add",
            "--id",
            "r-dup",
            "--title",
            "Tests again",
            "--content",
            "write the failing test before the fix",
            "--check",
        ),
        0,
        "{'id': 'r-a', 'title': 'Tests first', 'score': 1.0, 'scope': 'project', 'tags': [], 'overlap': "
        "['before', 'failing', 'fix', 'test', 'the'], 'kind': 'rule'}\n",
        "",
    ),
    (
        (
            "--json",
            "rule",
            "add",
            "--id",
            "r-dup",
            "--title",
            "Tests again",
            "--content",
            "write the failing test before the fix",
            "--check",
        ),
        0,
        "{\n"
        '  "schema": "rule_add@1",\n'
        '  "id": null,\n'
        '  "candidates": [\n'
        "    {\n"
        '      "id": "r-a",\n'
        '      "title": "Tests first",\n'
        '      "score": 1.0,\n'
        '      "scope": "project",\n'
        '      "tags": [],\n'
        '      "overlap": [\n'
        '        "before",\n'
        '        "failing",\n'
        '        "fix",\n'
        '        "test",\n'
        '        "the"\n'
        "      ],\n"
        '      "kind": "rule"\n'
        "    }\n"
        "  ],\n"
        '  "related": null,\n'
        '  "options": null,\n'
        '  "extended": null,\n'
        '  "extended_kind": null,\n'
        '  "relation": null,\n'
        '  "dedupe_unavailable": null\n'
        "}\n",
        "",
    ),
    (
        (
            "rule",
            "add",
            "--id",
            "r-dup",
            "--title",
            "Tests again",
            "--content",
            "write the failing test before the fix",
            "--related",
            "r-a",
        ),
        0,
        "added rule r-dup (related to r-a)\n",
        "",
    ),
    (
        (
            "rule",
            "add",
            "--id",
            "r-x",
            "--title",
            "Tests yet again",
            "--content",
            "write the failing test before the fix",
            "--extends",
            "r-a",
        ),
        0,
        "rule text added to r-a (); no rule r-x filed\n",
        "",
    ),
    (
        (
            "rule",
            "add",
            "--id",
            "r-y",
            "--title",
            "Tests once more",
            "--content",
            "write the failing test before the fix",
            "--new",
        ),
        0,
        "added rule r-y\n",
        "",
    ),
    (
        ("rule", "add", "--id", "r-b", "--title", "Naming", "--content", "again"),
        1,
        "",
        "Rule r-b already exists; use operation='updated' to modify it\n",
    ),
    (
        ("rule", "add", "--title", "No id"),
        2,
        "",
        "usage: ddflow rule add [-h] --id ID --title TITLE [--content CONTENT]\n"
        "                       [--tags TAGS] [--scope SCOPE] [--priority PRIORITY]\n"
        "                       [--globs GLOBS] [--new | --extends ID |\n"
        "                       --duplicate-of ID | --related ID | --check]\n"
        "ddflow rule add: error: the following arguments are required: --id\n",
    ),
    (
        ("rule", "list"),
        0,
        "r-a  [project] Tests first\n"
        "r-b  [task] Naming\n"
        "r-c  [project] Layers\n"
        "r-dup  [project] Tests again\n"
        "r-y  [project] Tests once more\n",
        "",
    ),
    (
        ("--json", "rule", "list"),
        0,
        "{\n"
        '  "schema": "rule_list@1",\n'
        '  "rows": [\n'
        "    {\n"
        '      "id": "r-a",\n'
        '      "title": "Tests first",\n'
        '      "scope": "project",\n'
        '      "tags": [],\n'
        '      "priority": 50\n'
        "    },\n"
        "    {\n"
        '      "id": "r-b",\n'
        '      "title": "Naming",\n'
        '      "scope": "task",\n'
        '      "tags": [\n'
        '        "style",\n'
        '        "naming"\n'
        "      ],\n"
        '      "priority": 70\n'
        "    },\n"
        "    {\n"
        '      "id": "r-c",\n'
        '      "title": "Layers",\n'
        '      "scope": "project",\n'
        '      "tags": [],\n'
        '      "priority": 50\n'
        "    },\n"
        "    {\n"
        '      "id": "r-dup",\n'
        '      "title": "Tests again",\n'
        '      "scope": "project",\n'
        '      "tags": [],\n'
        '      "priority": 50\n'
        "    },\n"
        "    {\n"
        '      "id": "r-y",\n'
        '      "title": "Tests once more",\n'
        '      "scope": "project",\n'
        '      "tags": [],\n'
        '      "priority": 50\n'
        "    }\n"
        "  ],\n"
        '  "count": 5\n'
        "}\n",
        "",
    ),
    (("rule", "list", "--tag", "style"), 0, "r-b  [task] Naming\n", ""),
    (("rule", "list", "--scope", "task"), 0, "r-b  [task] Naming\n", ""),
    (("rule", "list", "--scope", "nowhere"), 2, "No rules found in scope 'nowhere'.\n", ""),
    (
        ("--json", "rule", "list", "--scope", "nowhere"),
        2,
        '{\n  "schema": "rule_list@1",\n  "rows": [],\n  "count": 0\n}\n',
        "",
    ),
    (("rule", "search", "snake_case"), 0, "r-b  [task] Naming\n", ""),
    (
        ("--json", "rule", "search", "snake_case", "--limit", "1"),
        0,
        "{\n"
        '  "schema": "rule_search@1",\n'
        '  "rows": [\n'
        "    {\n"
        '      "id": "r-b",\n'
        '      "title": "Naming",\n'
        '      "scope": "task",\n'
        '      "tags": [\n'
        '        "style",\n'
        '        "naming"\n'
        "      ],\n"
        '      "priority": 70,\n'
        '      "score": 1.0\n'
        "    }\n"
        "  ],\n"
        '  "count": 1,\n'
        '  "query": "snake_case"\n'
        "}\n",
        "",
    ),
    (("rule", "search", "snake", "--exact"), 0, "r-b  [task] Naming\n", ""),
    (("rule", "search", "snake.*case", "--regex", "--tag", "style"), 0, "r-b  [task] Naming\n", ""),
    (("rule", "search", "zzzzqqq"), 2, "No rules found matching 'zzzzqqq'\n", ""),
    (
        ("rule", "show", "r-b"),
        0,
        "r-b  Naming\n"
        "  scope task  priority 70  tags ['style', 'naming']  globs ['a/*.py', 'b/*.py']\n"
        "\n"
        "snake_case names\n",
        "",
    ),
    (
        ("--json", "rule", "show", "r-b"),
        0,
        "{\n"
        '  "schema": "rule_show@1",\n'
        '  "id": "r-b",\n'
        '  "title": "Naming",\n'
        '  "content": "snake_case names",\n'
        '  "tags": [\n'
        '    "style",\n'
        '    "naming"\n'
        "  ],\n"
        '  "scope": "task",\n'
        '  "priority": 70,\n'
        '  "globs": [\n'
        '    "a/*.py",\n'
        '    "b/*.py"\n'
        "  ],\n"
        '  "created": "<time>",\n'
        '  "updated": "<time>"\n'
        "}\n",
        "",
    ),
    (("rule", "show", "r-nope"), 1, "", "Rule r-nope not found\n"),
    (("--json", "rule", "show", "r-nope"), 1, "", "Rule r-nope not found\n"),
    (
        ("rule", "edit", "r-b", "--priority", "80", "--tags", "style"),
        0,
        "updated r-b: priority, tags\n",
        "",
    ),
    (
        ("rule", "edit", "r-b", "--title", "Naming rules", "--globs", "", "--scope", "project"),
        0,
        "updated r-b: globs, scope, title\n",
        "",
    ),
    (
        ("--json", "rule", "edit", "r-b", "--content", "snake_case, always"),
        0,
        "{\n"
        '  "schema": "rule_edit@1",\n'
        '  "id": "r-b",\n'
        '  "candidates": null,\n'
        '  "related": null,\n'
        '  "options": null,\n'
        '  "dedupe_unavailable": null\n'
        "}\n",
        "",
    ),
    (("rule", "edit", "r-b"), 0, "updated r-b: nothing\n", ""),
    (("rule", "edit", "r-nope", "--title", "x"), 1, "", "Rule r-nope not found\n"),
    (("--json", "rule", "edit", "r-nope", "--title", "x"), 1, "", "Rule r-nope not found\n"),
    (
        ("rule", "edit", "r-c", "--content", "write the failing test before the fix"),
        0,
        "updated r-c: content\n",
        "",
    ),
    (
        ("--json", "rule", "edit", "r-c", "--content", "write the failing test before the fix"),
        0,
        "{\n"
        '  "schema": "rule_edit@1",\n'
        '  "id": "r-c",\n'
        '  "candidates": null,\n'
        '  "related": null,\n'
        '  "options": null,\n'
        '  "dedupe_unavailable": null\n'
        "}\n",
        "",
    ),
    (
        ("rule", "edit", "r-c", "--content", "write the failing test before the fix", "--new"),
        0,
        "updated r-c: content\n",
        "",
    ),
    (
        (
            "rule",
            "edit",
            "r-c",
            "--content",
            "write the failing test before the fix now",
            "--related",
            "r-a",
        ),
        0,
        "updated r-c: content (related to r-a)\n",
        "",
    ),
    (("rule", "sync"), 0, "rule files and the log agree\n", ""),
    (
        ("--json", "rule", "sync"),
        0,
        "{\n"
        '  "schema": "rule_sync@1",\n'
        '  "recorded": [],\n'
        '  "updated": [],\n'
        '  "restored": [],\n'
        '  "failed": []\n'
        "}\n",
        "",
    ),
    (("rule", "remove", "r-c"), 0, "removed r-c\n", ""),
    (
        ("--json", "rule", "remove", "r-b"),
        0,
        '{\n  "schema": "rule_remove@1",\n  "id": "r-b"\n}\n',
        "",
    ),
    (
        ("rule", "remove", "r-nope"),
        1,
        "",
        "Failed to delete rule: Rule r-nope not found at <repo>/.ddflow/rules/r-nope.toml\n",
    ),
    (
        ("--json", "rule", "remove", "r-nope"),
        1,
        "",
        "Failed to delete rule: Rule r-nope not found at <repo>/.ddflow/rules/r-nope.toml\n",
    ),
    (
        ("rule", "list"),
        0,
        "r-a  [project] Tests first\nr-dup  [project] Tests again\nr-y  [project] Tests once more\n",
        "",
    ),
]
