"""Tests for OpenAI auto-instrumentation (ATTACH_ANY_SYSTEM P0).

Exercises wrap_client with a fake client that mimics the OpenAI SDK surface,
so no real SDK is required. Also confirms instrument_openai is a safe no-op
when the SDK is absent.
"""

from __future__ import annotations

from snagline.auto.openai import instrument_openai, wrap_client


class _SpyMonitor:
    def __init__(self):
        self.events: list = []

    def ingest(self, event) -> None:
        self.events.append(event)


class _FakeCompletions:
    def create(self, *, model="gpt", messages=None, prompt=None, **kw):
        return "ok"


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeClient:
    # Resources are per-instance, like the real SDK builds per client, so a
    # wrap_client in one test cannot leave a wrapper on another test's client.
    def __init__(self):
        self.chat = _FakeChat()
        self.completions = _FakeCompletions()


def test_wrap_client_records_on_success():
    mon = _SpyMonitor()
    client = wrap_client(mon, _FakeClient())
    out = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user"}])
    assert out == "ok"
    assert len(mon.events) == 1
    ev = mon.events[0]
    assert ev.tool_name == "openai.chat.completions.create"
    assert ev.error is False
    assert ev.latency_ms is not None


def test_wrap_client_records_both_paths():
    mon = _SpyMonitor()
    client = wrap_client(mon, _FakeClient())
    client.chat.completions.create(model="gpt-4o", messages=[{"role": "user"}])
    client.completions.create(model="gpt-4o", prompt="hi")
    assert len(mon.events) == 2
    assert mon.events[1].tool_name == "openai.completions.create"


def test_wrap_client_records_error_and_propagates():
    class _BoomCompletions:
        def create(self, **kw):
            raise RuntimeError("boom")

    class _BoomClient:
        chat = type("C", (), {"completions": _BoomCompletions()})()

    mon = _SpyMonitor()
    client = wrap_client(mon, _BoomClient())
    raised = False
    try:
        client.chat.completions.create(model="gpt-4o", messages=[{"role": "user"}])
    except RuntimeError:
        raised = True
    assert raised, "exception must propagate"
    assert len(mon.events) == 1
    assert mon.events[0].error is True


def test_instrument_openai_without_sdk_is_safe_noop():
    mon = _SpyMonitor()
    assert instrument_openai(mon) is False


def test_wrap_client_is_idempotent():
    # Per-client mode used to have no sentinel guard, so a second call stacked
    # a second wrapper layer and every call emitted one event per layer,
    # doubling every detector's counts (issue #336).
    mon = _SpyMonitor()
    client = wrap_client(mon, _FakeClient())
    client = wrap_client(mon, client)
    client.chat.completions.create(model="gpt-4o", messages=[{"role": "user"}])
    assert len(mon.events) == 1, "one event per call, not one per wrapper layer"


def test_wrap_client_warns_when_nothing_patched(caplog):
    class _Bare:
        pass

    with caplog.at_level("WARNING", logger="snagline"):
        wrap_client(None, _Bare())
    assert any("no create method" in r.message for r in caplog.records)


def test_instrument_openai_with_explicit_client():
    mon = _SpyMonitor()
    assert instrument_openai(mon, client=_FakeClient()) is True
