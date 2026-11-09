"""Selection lease: while held, no daemon caller (helper or raw IPC) can move the tab selection."""
import asyncio
import os

import pytest

from browser_harness import daemon


class _CDP:
    def __init__(self, pages):
        self.pages = pages  # targetId -> url
        self.calls = []

    async def send_raw(self, method, params=None, session_id=None):
        self.calls.append((method, params, session_id))
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": t, "type": "page", "url": u} for t, u in self.pages.items()]}
        if method == "Target.getTargetInfo":
            tid = params["targetId"]
            if tid not in self.pages:
                raise RuntimeError("No target with given id found")
            return {"targetInfo": {"targetId": tid, "type": "page", "url": self.pages[tid]}}
        if method == "Target.attachToTarget":
            return {"sessionId": "s-" + params["targetId"]}
        return {}


def _daemon(pages, selected="T1"):
    d = daemon.Daemon()
    d.cdp = _CDP(pages)
    d.target_id, d.session = selected, "s-" + selected
    return d


def _run(d, req):
    return asyncio.run(d.handle(req))


def _acquire(d, **kw):
    return _run(d, {"meta": "lease_acquire", "ttl": 60, "owner_pid": os.getpid(), **kw})


def test_lease_refuses_selection_move_from_any_caller_until_released():
    d = _daemon({"T1": "https://login.example/", "T2": "https://other.example/"})
    lease = _acquire(d)
    assert lease["targetId"] == "T1" and lease["token"]

    moved = _run(d, {"meta": "set_session", "session_id": "s-T2", "target_id": "T2"})
    assert moved["error"].startswith("selection_leased")
    assert (d.target_id, d.session) == ("T1", "s-T1")
    assert _run(d, {"meta": "lease_check", "token": lease["token"]})["targetId"] == "T1"

    # Re-pointing the session at the leased tab itself is not a selection move.
    assert "error" not in _run(d, {"meta": "set_session", "session_id": "s-T1b", "target_id": "T1"})

    assert _run(d, {"meta": "lease_release", "token": "wrong"}) == {"released": False}
    assert _run(d, {"meta": "lease_release", "token": lease["token"]}) == {"released": True}
    assert "error" not in _run(d, {"meta": "set_session", "session_id": "s-T2", "target_id": "T2"})
    assert d.target_id == "T2"
    assert _run(d, {"meta": "lease_check", "token": lease["token"]}) == {"error": "lease_invalid"}


def test_only_one_holder_and_token_is_required():
    d = _daemon({"T1": "https://login.example/"})
    lease = _acquire(d)
    assert _acquire(d)["error"].startswith("selection_leased")
    assert _run(d, {"meta": "lease_check", "token": "guess"}) == {"error": "lease_invalid"}
    assert _run(d, {"meta": "lease_check"}) == {"error": "lease_invalid"}
    assert _run(d, {"meta": "lease_check", "token": lease["token"]})["generation"] == lease["generation"]


def test_acquire_pins_only_the_expected_target():
    d = _daemon({"T1": "https://a/", "T2": "https://b/"})
    assert _acquire(d, target_id="T2") == {"error": "selection_changed"}
    assert d._lease is None
    assert _acquire(d, target_id="T1")["targetId"] == "T1"


def test_acquire_fails_closed_on_closed_target():
    d = _daemon({"T2": "https://b/"}, selected="T1")
    assert _acquire(d) == {"error": "cdp_disconnected"}
    assert d._lease is None


@pytest.mark.parametrize("ttl", [0, -1, 601, "60", None, True])
def test_acquire_rejects_unbounded_ttl(ttl):
    d = _daemon({"T1": "https://a/"})
    assert "error" in _run(d, {"meta": "lease_acquire", "ttl": ttl})
    assert d._lease is None


def test_expired_or_dead_holder_frees_the_selection(monkeypatch):
    d = _daemon({"T1": "https://a/", "T2": "https://b/"})
    now = [1000.0]
    monkeypatch.setattr(daemon.time, "monotonic", lambda: now[0])
    lease = _acquire(d)
    now[0] += 61
    assert _run(d, {"meta": "lease_check", "token": lease["token"]}) == {"error": "lease_invalid"}
    assert "error" not in _run(d, {"meta": "set_session", "session_id": "s-T2", "target_id": "T2"})

    lease = _acquire(d)
    monkeypatch.setattr(daemon, "_owner_alive", lambda pid: False)
    assert "error" not in _run(d, {"meta": "set_session", "session_id": "s-T1", "target_id": "T1"})
    assert _run(d, {"meta": "lease_check", "token": lease["token"]}) == {"error": "lease_invalid"}


def test_stale_session_recovery_keeps_the_leased_tab(monkeypatch):
    # Default daemon recovery otherwise attaches the first real page.
    monkeypatch.setattr(daemon, "NAME", "default")
    monkeypatch.setattr(daemon, "BROWSER_KIND", "cdp")
    d = _daemon({"T0": "https://first/", "T1": "https://login/"})
    lease = _acquire(d)
    asyncio.run(d.attach_first_page(replaces_session="s-T1", enable_domains=False))
    assert d.target_id == "T1"
    assert _run(d, {"meta": "lease_check", "token": lease["token"]})["targetId"] == "T1"


def test_recovery_away_from_a_closed_leased_tab_invalidates_the_lease(monkeypatch):
    monkeypatch.setattr(daemon, "NAME", "default")
    monkeypatch.setattr(daemon, "BROWSER_KIND", "cdp")
    d = _daemon({"T1": "https://login/", "T0": "https://first/"})
    lease = _acquire(d)
    del d.cdp.pages["T1"]
    asyncio.run(d.attach_first_page(replaces_session="s-T1", enable_domains=False))
    assert d.target_id == "T0"
    assert _run(d, {"meta": "lease_check", "token": lease["token"]}) == {"error": "selection_changed"}


def test_every_selection_move_bumps_the_generation_a_lease_pins():
    d = _daemon({"T1": "https://a/", "T2": "https://b/"})
    first = _acquire(d)
    _run(d, {"meta": "lease_release", "token": first["token"]})
    _run(d, {"meta": "set_session", "session_id": "s-T2", "target_id": "T2"})
    _run(d, {"meta": "set_session", "session_id": "s-T1", "target_id": "T1"})
    second = _acquire(d)
    assert second["targetId"] == first["targetId"]
    assert second["generation"] == first["generation"] + 2
