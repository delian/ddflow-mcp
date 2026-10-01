"""A string-valued knob set from the environment as JSON refuses a non-string value
(bug B7506c1124a).

`str()`-casting turned `{"core": null}` into `{"core": "None"}` and `[null]` into
`["None"]`: a value nobody wrote, loaded as if they had. For `agent.families` that is the
map the reviewer-independence check reads.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.config import Config, _coerce


@pytest.mark.parametrize("bad", ['{"core": null}', '{"core": 1}', '{"core": ["anthropic"]}'])
def test_a_non_string_value_in_a_string_map_is_refused(bad):
    with pytest.raises(ValueError, match="core"):
        _coerce(bad, "dict[str, str]")


def test_the_environment_cannot_load_null_as_the_word_none(tmp_path):
    with pytest.raises(ValueError, match="core"):
        Config.load(tmp_path, env={"DDFLOW_AGENT_FAMILIES": '{"core": null}'})


@pytest.mark.parametrize("bad", ["[null]", '["a", 2]', '[["a"]]'])
def test_a_non_string_element_in_a_string_list_is_refused(bad):
    with pytest.raises(ValueError):
        _coerce(bad, "list[str]")


def test_strings_still_load():
    assert _coerce('{"core": "anthropic"}', "dict[str, str]") == {"core": "anthropic"}
    assert _coerce('["a", "b,c"]', "list[str]") == ["a", "b,c"]
    assert _coerce("core=anthropic", "dict[str, str]") == {"core": "anthropic"}
