"""Synthetic daemon acceptance: real IPC, lsof and serve loop; no live browser."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def test_idle_daemon_exits_within_two_minutes():
    with tempfile.TemporaryDirectory(prefix='bh-idle-') as directory:
        env = {k: v for k, v in os.environ.items() if not k.startswith(('BU_', 'BH_'))}
        env.update(BU_NAME='idle-acceptance', BU_IDLE_EXIT_HOURS='0.01',
                   BH_RUNTIME_DIR=directory, BH_TMP_DIR=directory,
                   BH_HOME=directory, HOME=directory)
        code = '''
import asyncio
from browser_harness import daemon
async def main():
    d = daemon.Daemon()
    d.stop = asyncio.Event()
    await daemon.serve(d)
asyncio.run(main())
'''
        start = time.monotonic()
        child = subprocess.Popen([sys.executable, '-c', code], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = child.communicate(timeout=120)
            assert child.returncode == 0, (stdout, stderr)
            elapsed = time.monotonic() - start
            assert elapsed >= 36, elapsed
            log = (Path(directory) / 'bu.log').read_text()
            assert 'expiring after 36s idle and disconnected' in log
            assert not (Path(directory) / 'bu.sock').exists()
            print(f'real synthetic daemon exited after {elapsed:.2f}s, rc=0; IPC removed')
        finally:
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=10)
