import json
import subprocess

import pytest

from browser_harness import watchdog


def test_census_excludes_parents_and_similar_commands(monkeypatch):
    def run(argv, **kwargs):
        assert argv == ['ps', '-axo', 'pid=,ppid=,args=']
        return subprocess.CompletedProcess(argv, 0, '10 1 /a/python -m browser_harness.daemon\n11 22 /a/python -m browser_harness.daemon\n12 1 /a/python -m browser_harness.daemon_extra\n13 1 sh -c looking for browser_harness.daemon\n', '')
    monkeypatch.setattr(watchdog.subprocess, 'run', run)
    assert watchdog.orphan_pids() == [10]


def test_transitions_are_delivered_once_and_failure_retries(tmp_path, monkeypatch):
    state = tmp_path / 'watch.json'
    pids = [10, 20, 30]
    monkeypatch.setattr(watchdog, 'orphan_pids', lambda: pids)
    calls = []
    def notify(argv, **kwargs):
        calls.append(argv)
        assert argv[1:5] == ['--severity', 'high', '--source', 'browser-harness-watchdog']
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(watchdog.subprocess, 'run', notify)
    watchdog.check(state, 2, 'notify')
    watchdog.check(state, 2, 'notify')
    assert len(calls) == 1
    pids[:] = [10]
    watchdog.check(state, 2, 'notify')
    assert len(calls) == 2
    assert 'recovered' in calls[-1][-1]
    pids[:] = [10, 20, 30]
    monkeypatch.setattr(watchdog.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 1))
    with pytest.raises(subprocess.CalledProcessError):
        watchdog.check(state, 2, 'notify')
    assert json.loads(state.read_text())['status'] == 'ok'
    monkeypatch.setattr(watchdog.subprocess, 'run', notify)
    watchdog.check(state, 2, 'notify')
    assert len(calls) == 3


def test_census_error_is_not_recovery(tmp_path, monkeypatch):
    state = tmp_path / 'watch.json'
    state.write_text('{"status":"high"}')
    monkeypatch.setattr(watchdog, 'orphan_pids', lambda: (_ for _ in ()).throw(OSError('ps failed')))
    with pytest.raises(OSError):
        watchdog.check(state, 32, None)
    assert json.loads(state.read_text())['status'] == 'high'
