import asyncio
import os
import subprocess

import pytest

from browser_harness import daemon


def test_disconnected_daemon_expires_through_real_serve(monkeypatch):
    async def scenario():
        d = daemon.Daemon()
        d.stop = asyncio.Event()
        monkeypatch.setenv('BU_IDLE_EXIT_HOURS', '0.00001')
        monkeypatch.setattr(daemon, 'log', lambda message: None)
        monkeypatch.setattr(daemon, 'tcp_connected', lambda: False, raising=False)
        async def server(name, handler):
            await asyncio.Event().wait()
        monkeypatch.setattr(daemon.ipc, 'serve', server)
        monkeypatch.setattr(daemon.ipc, 'cleanup_endpoint', lambda name: None)
        task = asyncio.create_task(daemon.serve(d))
        try:
            await asyncio.wait_for(asyncio.shield(task), 5)
            assert d.stop.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['tcp', 'probe-error', 'ipc'])
def test_connection_or_unknown_state_prevents_expiry(monkeypatch, mode):
    async def scenario():
        d = daemon.Daemon()
        d.stop = asyncio.Event()
        monkeypatch.setenv('BU_IDLE_EXIT_HOURS', '0.00001')
        messages = []
        monkeypatch.setattr(daemon, 'log', messages.append)
        ready = asyncio.get_running_loop().create_future()
        probes = 0

        def probe():
            nonlocal probes
            probes += 1
            if mode == 'probe-error':
                raise OSError('inspection unavailable')
            return mode == 'tcp'
        monkeypatch.setattr(daemon, 'tcp_connected', probe)
        async def server(name, handler):
            ready.set_result(handler)
            await asyncio.Event().wait()
        monkeypatch.setattr(daemon.ipc, 'serve', server)
        monkeypatch.setattr(daemon.ipc, 'cleanup_endpoint', lambda name: None)
        task = asyncio.create_task(daemon.serve(d))
        client = None
        try:
            handler = await ready
            if mode == 'ipc':
                reader = asyncio.StreamReader()
                class Writer:
                    def close(self): pass
                client = asyncio.create_task(handler(reader, Writer()))
            async def enough_probes():
                while probes < 12:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(enough_probes(), 5)
            assert not task.done()
            assert not d.stop.is_set()
            if mode == 'probe-error':
                assert any('cannot measure connections' in m for m in messages)
            # Remove the protection: the same daemon must now expire.
            monkeypatch.setattr(daemon, 'tcp_connected', lambda: False)
            if client:
                reader.feed_eof()
                await client
            await asyncio.wait_for(asyncio.shield(task), 5)
            assert d.stop.is_set()
        finally:
            task.cancel()
            if client: client.cancel()
            await asyncio.gather(task, *( [client] if client else []), return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize(('code', 'out', 'err', 'expected'), [
    (1, '', '', False),
    (0, f'p{os.getpid()}\n', '', True),
    (2, '', '', None),
    (1, '', 'inspection failed', None),
    (0, '', '', None),
    (1, 'unexpected', '', None),
])
def test_tcp_inspection_distinguishes_empty_connected_and_error(monkeypatch, code, out, err, expected):
    def run(argv, **kwargs):
        assert '-a' in argv and '-sTCP:ESTABLISHED' in argv
        assert argv[argv.index('-p') + 1] == str(os.getpid())
        assert kwargs['timeout'] == 5
        return subprocess.CompletedProcess(argv, code, out, err)
    monkeypatch.setattr(daemon.subprocess, 'run', run)
    if expected is None:
        with pytest.raises(RuntimeError):
            daemon.tcp_connected()
    else:
        assert daemon.tcp_connected() is expected


@pytest.mark.parametrize('hours', ['0', '-1', 'nan', 'inf', 'invalid'])
def test_invalid_expiry_refused(monkeypatch, hours):
    monkeypatch.setenv('BU_IDLE_EXIT_HOURS', hours)
    with pytest.raises(ValueError):
        asyncio.run(daemon.serve(daemon.Daemon()))
