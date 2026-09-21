"""Run behavioral mutations in temporary copies, never touch the working sources."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
mutants = [
    ('no-expiry', 'daemon.py', 'if now - max(last_activity, disconnected_since) >= idle_seconds:', 'if False:', 'test_disconnected_daemon_expires_through_real_serve'),
    ('ignore-tcp', 'daemon.py', 'if connected or active_clients:', 'if active_clients:', 'test_connection_or_unknown_state_prevents_expiry[tcp]'),
    ('ignore-ipc', 'daemon.py', 'if connected or active_clients:', 'if connected:', 'test_connection_or_unknown_state_prevents_expiry[ipc]'),
    ('ignore-command-activity', 'daemon.py', 'max(last_activity, disconnected_since)', 'disconnected_since', 'test_activity_and_connection_free_windows_restart[command]'),
    ('keep-old-disconnection-window', 'daemon.py', 'if connected or active_clients:\n                disconnected_since = None', 'if connected or active_clients:\n                pass', 'test_activity_and_connection_free_windows_restart[connection]'),
    ('no-alert', 'watchdog.py', 'if status != previous:', 'if False:', 'test_transitions_are_delivered_once_and_failure_retries'),
    ('no-dedup', 'watchdog.py', 'if status != previous:', 'if True:', 'test_transitions_are_delivered_once_and_failure_retries'),
    ('ignore-delivery-error', 'watchdog.py', '                result.check_returncode()', '                pass', 'test_transitions_are_delivered_once_and_failure_retries'),
]
for label, filename, old, new, test in mutants:
    with tempfile.TemporaryDirectory(prefix='bh-mutation-') as temp:
        temp = Path(temp)
        shutil.copytree(root / 'src', temp / 'src', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(root / 'tests' / 'unit', temp / 'tests', ignore=shutil.ignore_patterns('__pycache__'))
        source = temp / 'src' / 'browser_harness' / filename
        original = source.read_text()
        assert original.count(old) == 1, label
        source.write_text(original.replace(old, new, 1))
        assert source.read_text() != original
        env = dict(os.environ, PYTHONPATH=str(temp / 'src'))
        suite = 'test_idle.py' if filename == 'daemon.py' else 'test_watchdog.py'
        config = temp / 'pytest.ini'
        config.write_text('[pytest]\n')
        result = subprocess.run([sys.executable, '-m', 'pytest', '-c', str(config),
                                 str(temp / 'tests' / suite) + '::' + test, '-q', '--tb=short'],
                                cwd=temp, env=env, capture_output=True, text=True, timeout=30)
        print(label, 'exit', result.returncode, result.stdout.splitlines()[-1:])
        assert result.returncode == 1 and '1 failed' in result.stdout, result.stdout + result.stderr
        assert (root / 'src' / 'browser_harness' / filename).read_text() == original
print(f'{len(mutants)}/{len(mutants)} behavioral mutants killed; working sources unchanged')
