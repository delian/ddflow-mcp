"""Environment fingerprint: same machine same key, a changed cap changes it, nothing leaks."""

from __future__ import annotations

import getpass
import json
import socket
from pathlib import Path

from ddflow.services.perf import env as E


def _reader(files: dict[str, str]):
    return files.get


def _facts(**kw):
    base = {
        "cpu_count": 8,
        "load_ratio": 0.1,
        "xdist_workers": 4,
        "parallel_cap": 3,
        "system": "Linux",
        "python_version": "3.12.4",
        "ddflow_version": "0.2.10",
        "git_head": "a" * 40,
        "cache": "warm",
        "container": False,
        "cgroup": "unlimited",
    }
    base.update(kw)
    return E.fingerprint(**base)


def test_same_inputs_same_key():
    assert _facts().key == _facts().key
    assert len(_facts().key) == 12


def test_changed_cap_changes_key():
    assert _facts().key != _facts(parallel_cap=4).key
    assert _facts().key != _facts(xdist_workers=8).key


def test_head_is_stamped_but_not_in_key():
    a, b = _facts(git_head="a" * 40), _facts(git_head="b" * 40)
    assert a.key == b.key
    assert a.facts["git_head"] == "a" * 12 and b.facts["git_head"] == "b" * 12


def test_load_and_cache_change_cond_not_key():
    a, b = _facts(load_ratio=0.1, cache="cold"), _facts(load_ratio=2.0, cache="warm")
    assert a.key == b.key and a.cond != b.cond
    assert a.facts["load"] == "idle" and b.facts["load"] == "saturated"


def test_cgroup_v1_threshold_is_a_parameter():
    mem = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
    r = _reader({mem: "2147479552\n"})
    assert E.cgroup_state(r) == "limited"
    assert E.cgroup_state(r, v1_unlimited_bytes=2147479552) == "unlimited"


def test_cgroup_v1_memory():
    mem = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
    cpu = "/sys/fs/cgroup/cpu/cpu.cfs_quota_us"
    assert E.cgroup_state(_reader({mem: "1073741824\n", cpu: "-1\n"})) == "limited"
    assert E.cgroup_state(_reader({mem: "9223372036854771712\n", cpu: "-1\n"})) == "unlimited"


def test_load_classes_use_bands():
    assert E.load_class(0.05) == "idle"
    assert E.load_class(0.6) == "busy"
    assert E.load_class(2.0) == "saturated"
    assert E.load_class(0.6, bands=(0.7, 1.5)) == "idle"
    assert E.load_class(None) == "unavailable"


def test_python_is_minor_only_and_bad_probe_is_unavailable_not_zero():
    f = _facts(python_version="3.12.4")
    assert f.facts["python"] == "3.12"
    g = _facts(cpu_count=None, cache=None, git_head="")
    assert g.facts["cpu_count"] == "unavailable"
    assert g.facts["cache"] == "unavailable"
    assert g.facts["git_head"] == "unavailable"
    assert g.key != _facts().key


def test_container_and_vm_flags():
    assert _facts(container=True).facts["container"] is True
    assert _facts(container=True).key != _facts().key
    assert _facts(container=True, cgroup="limited").key != _facts(container=True).key
    assert _facts(system="Darwin").facts["vm"] == "unknown"
    assert _facts(container=True, cgroup="unavailable").facts["vm"] == "unknown"
    assert _facts().facts["vm"] == "no"


def test_cgroup_probe_v2_v1_and_missing():
    assert E.cgroup_state(_reader({"/sys/fs/cgroup/cpu.max": "200000 100000\n"})) == "limited"
    assert (
        E.cgroup_state(
            _reader(
                {"/sys/fs/cgroup/cpu.max": "max 100000\n", "/sys/fs/cgroup/memory.max": "max\n"}
            )
        )
        == "unlimited"
    )
    assert E.cgroup_state(_reader({"/sys/fs/cgroup/memory.max": "1073741824\n"})) == "limited"
    assert E.cgroup_state(_reader({"/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "-1\n"})) == "unlimited"
    assert E.cgroup_state(_reader({"/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "50000\n"})) == "limited"
    assert E.cgroup_state(_reader({})) == "unavailable"


def test_container_probe():
    assert E.in_container(lambda p: p == "/.dockerenv") is True
    assert E.in_container(lambda p: False) is False


def test_cache_state(tmp_path: Path):
    assert E.cache_state([]) == "unavailable"
    d = tmp_path / "c"
    assert E.cache_state([d]) == "cold"
    d.mkdir()
    assert E.cache_state([d]) == "cold"
    (d / "x").write_text("1")
    assert E.cache_state([d]) == "warm"


def _identifiers(tmp_path: Path) -> list[str]:
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    paths = (tmp_path, Path.home())
    names = [socket.gethostname(), user, tmp_path.name]
    names += [str(p) for p in paths] + [p.as_posix() for p in paths]
    return [n for n in names if len(n) > 1]


def test_collect_runs_here_and_leaks_nothing(tmp_path: Path):
    fp = E.collect(tmp_path, parallel_cap=2, xdist_workers=1, cache_dirs=[tmp_path / "none"])
    blob = "\n".join([fp.key, fp.cond, json.dumps(fp.facts, ensure_ascii=False), fp.stamp()])
    for secret in _identifiers(tmp_path):
        assert secret not in blob
        assert json.dumps(secret)[1:-1] not in blob
    assert fp.stamp().startswith(f"env:{fp.key}/{fp.cond}")
    assert set(fp.facts) >= {
        "cpu_count",
        "load",
        "xdist_workers",
        "parallel_cap",
        "os",
        "python",
        "cache",
        "ddflow",
        "git_head",
        "container",
        "cgroup",
        "vm",
    }
