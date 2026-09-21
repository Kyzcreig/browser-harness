"""POSIX daemon census with transition-only alerts; never signals processes.

Run under a supervisor. --notify accepts the fleet notify executable's
--severity/--source/--body interface; omit it for stdout-only monitoring.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time


def orphan_pids():
    result = subprocess.run(['ps', '-axo', 'pid=,ppid=,args='],
                            capture_output=True, text=True, timeout=10)
    result.check_returncode()
    if result.stderr or not result.stdout.strip():
        raise RuntimeError('cannot measure process census')
    pids = []
    for line in result.stdout.splitlines():
        pid, parent, command = line.split(None, 2)
        if int(parent) == 1 and command.split()[-2:] == ['-m', 'browser_harness.daemon']:
            pids.append(int(pid))
    return pids


def check(state, threshold, notify):
    import fcntl  # POSIX watchdog; imported here so Windows package imports still work.
    state = Path(state)
    state.parent.mkdir(parents=True, exist_ok=True)
    with state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous = json.loads(state.read_text())['status']
            if previous not in ('ok', 'high'):
                raise ValueError('invalid watchdog state')
        except FileNotFoundError:
            previous = 'ok'
        pids = orphan_pids()
        status = 'high' if len(pids) > threshold else 'ok'
        if status != previous:
            event = 'high' if status == 'high' else 'recovered'
            message = f'browser-harness daemon census {event}: {len(pids)} reparented daemons (threshold {threshold}); inspect activity/connections before any cleanup'
            if notify:
                result = subprocess.run([notify, '--severity', 'high', '--source',
                                         'browser-harness-watchdog', '--body', message], timeout=30)
                result.check_returncode()
            print(message, flush=True)
        # Commit state only after successful delivery; a failed send retries.
        temporary = state.with_suffix('.tmp')
        temporary.write_text(json.dumps({'status': status, 'count': len(pids),
                                         'checked_at': time.time()}))
        os.replace(temporary, state)
        return len(pids)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--threshold', type=int, default=32)
    parser.add_argument('--notify')
    args = parser.parse_args()
    if args.threshold < 0:
        parser.error('--threshold must be nonnegative')
    check(args.state, args.threshold, args.notify)


if __name__ == '__main__':
    main()
