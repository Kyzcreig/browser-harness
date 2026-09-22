import asyncio
import os
import subprocess
import types

import pytest

from browser_harness import daemon


@pytest.mark.parametrize(('hours', 'expected'), [(None, 3600), ('1', 900), ('0.01', 9)])
def test_idle_poll_cadence_scales_with_window(monkeypatch, hours, expected):
    async def scenario():
        if hours is None:
            monkeypatch.delenv('BU_IDLE_EXIT_HOURS', raising=False)
        else:
            monkeypatch.setenv('BU_IDLE_EXIT_HOURS', hours)
        d = daemon.Daemon()
        d.stop = asyncio.Event()
        observed = asyncio.get_running_loop().create_future()
        async def sleep(seconds):
            if asyncio.current_task().get_coro().__name__ != 'idle_watch':
                await asyncio.sleep(0)
                return
            observed.set_result(seconds)
            await asyncio.Event().wait()
        async def server(name, handler):
            await asyncio.Event().wait()
        proxy = types.SimpleNamespace(**{name: getattr(asyncio, name) for name in dir(asyncio)})
        proxy.sleep = sleep
        monkeypatch.setattr(daemon, 'asyncio', proxy)
        monkeypatch.setattr(daemon, 'log', lambda message: None)
        monkeypatch.setattr(daemon.ipc, 'serve', server)
        monkeypatch.setattr(daemon.ipc, 'cleanup_endpoint', lambda name: None)
        task = asyncio.create_task(daemon.serve(d))
        try:
            assert await asyncio.wait_for(observed, 5) == expected
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


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
                while probes < 12 and not task.done():
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


@pytest.mark.parametrize('reset', ['command', 'connection'])
def test_activity_and_connection_free_windows_restart(monkeypatch, reset):
    async def scenario():
        d = daemon.Daemon()
        d.stop = asyncio.Event()
        monkeypatch.setenv('BU_IDLE_EXIT_HOURS', '0.01')
        clock = [0]
        messages = []
        handler = None
        monkeypatch.setattr(daemon, 'time', types.SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 1000 + clock[0]))
        monkeypatch.setattr(daemon, 'log', messages.append)
        async def server(name, callback):
            nonlocal handler
            handler = callback
            await asyncio.Event().wait()
        monkeypatch.setattr(daemon.ipc, 'serve', server)
        monkeypatch.setattr(daemon.ipc, 'cleanup_endpoint', lambda name: None)
        steps = iter([0, 35, 40, 70, 77])
        async def probe(fn):
            clock[0] = next(steps)
            assert not d.stop.is_set(), 'expired before the restarted window elapsed'
            if clock[0] == 35 and reset == 'command':
                reader = asyncio.StreamReader()
                reader.feed_data(b'{"meta":"ping"}\n')
                class Writer:
                    def write(self, data): pass
                    async def drain(self): pass
                    def close(self): pass
                await handler(reader, Writer())
            return clock[0] == 35 and reset == 'connection'
        async def sleep(seconds):
            await asyncio.sleep(0)
        proxy = types.SimpleNamespace(**{name: getattr(asyncio, name) for name in dir(asyncio)})
        proxy.sleep = sleep
        proxy.to_thread = probe
        monkeypatch.setattr(daemon, 'asyncio', proxy)
        await asyncio.wait_for(daemon.serve(d), 5)
        assert clock[0] == 77
        assert d.stop.is_set()
        if reset == 'command':
            assert 'last-command-at: 1035.000000' in messages
    asyncio.run(scenario())
