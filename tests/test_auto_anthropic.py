"""Tests for Anthropic auto-instrumentation (ATTACH_ANY_SYSTEM P0)."""

from __future__ import annotations

from snagline.auto.anthropic import instrument_anthropic, wrap_client


class _SpyMonitor:
    def __init__(self):
        self.events: list = []

    def ingest(self, event) -> None:
        self.events.append(event)


class _FakeMessages:
    def create(self, *, model="claude", messages=None, **kw):
        return "ok"


class _FakeClient:
    # Resources are per-instance, like the real SDK builds per client, so a
    # wrap_client in one test cannot leave a wrapper on another test's client.
    def __init__(self):
        self.messages = _FakeMessages()


def test_wrap_client_records_on_success():
    mon = _SpyMonitor()
    client = wrap_client(mon, _FakeClient())
    out = client.messages.create(model="claude-3-5-sonnet", messages=[{"role": "user"}])
    assert out == "ok"
    assert len(mon.events) == 1
    ev = mon.events[0]
    assert ev.tool_name == "anthropic.messages.create"
    assert ev.error is False
    assert ev.latency_ms is not None


def test_wrap_client_missing_messages_attr_is_safe():
    mon = _SpyMonitor()

    class _Bare:
        pass

    wrap_client(mon, _Bare())
    assert mon.events == []


def test_wrap_client_records_error_and_propagates():
    class _BoomMessages:
        def create(self, **kw):
            raise RuntimeError("boom")

    class _BoomClient:
        messages = _BoomMessages()

    mon = _SpyMonitor()
    client = wrap_client(mon, _BoomClient())
    raised = False
    try:
        client.messages.create(model="claude", messages=[{"role": "user"}])
    except RuntimeError:
        raised = True
    assert raised, "exception must propagate"
    assert len(mon.events) == 1
    assert mon.events[0].error is True


def test_instrument_anthropic_without_sdk_is_safe_noop():
    mon = _SpyMonitor()
    assert instrument_anthropic(mon) is False


def test_wrap_client_is_idempotent():
    # Per-client mode used to have no sentinel guard, so a second call stacked
    # a second wrapper layer and every call emitted one event per layer,
    # doubling every detector's counts (issue #336).
    mon = _SpyMonitor()
    client = wrap_client(mon, _FakeClient())
    client = wrap_client(mon, client)
    client.messages.create(model="claude", messages=[{"role": "user"}])
    assert len(mon.events) == 1, "one event per call, not one per wrapper layer"


def test_wrap_client_warns_when_nothing_patched(caplog):
    class _Bare:
        pass

    with caplog.at_level("WARNING", logger="snagline"):
        wrap_client(None, _Bare())
    assert any("found no messages resource" in r.message for r in caplog.records)


def test_instrument_anthropic_with_explicit_client():
    mon = _SpyMonitor()
    assert instrument_anthropic(mon, client=_FakeClient()) is True
