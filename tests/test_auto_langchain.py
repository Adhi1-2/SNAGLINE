"""Tests for LangChain auto-instrumentation (ATTACH_ANY_SYSTEM P0)."""

from __future__ import annotations

from snagline.auto.langchain import instrument_langchain, wrap_client


class _SpyMonitor:
    def __init__(self):
        self.events: list = []

    def ingest(self, event) -> None:
        self.events.append(event)


class _FakeLLM:
    model_name = "gpt-4o"

    def invoke(self, prompt, **kw):
        return "ok"

    def generate(self, prompts, **kw):
        return "result"


def test_wrap_client_records_invoke_and_generate():
    mon = _SpyMonitor()
    llm = wrap_client(mon, _FakeLLM())
    assert llm.invoke("hello") == "ok"
    assert llm.generate(["a", "b"]) == "result"
    assert len(mon.events) == 2
    tools = {e.tool_name for e in mon.events}
    assert tools == {"langchain.invoke", "langchain.generate"}
    for ev in mon.events:
        assert ev.error is False
        assert ev.latency_ms is not None


def test_wrap_client_records_error_and_propagates():
    class _BoomLLM:
        def invoke(self, *a, **kw):
            raise RuntimeError("boom")

    mon = _SpyMonitor()
    llm = wrap_client(mon, _BoomLLM())
    raised = False
    try:
        llm.invoke("hi")
    except RuntimeError:
        raised = True
    assert raised
    assert len(mon.events) == 1
    assert mon.events[0].error is True


def test_instrument_langchain_without_sdk_is_safe_noop():
    mon = _SpyMonitor()
    assert instrument_langchain(mon) is False


def test_instrument_langchain_with_explicit_client():
    mon = _SpyMonitor()
    assert instrument_langchain(mon, client=_FakeLLM()) is True


def test_wrap_client_is_idempotent():
    # LangChain's wrap_client was the odd one out: it never set the
    # __snagline_wrapped__ sentinel at all, so every call stacked another
    # layer and emitted another event per call (issue #336).
    mon = _SpyMonitor()
    llm = wrap_client(mon, _FakeLLM())
    llm = wrap_client(mon, llm)
    assert llm.invoke("hello") == "ok"
    assert len(mon.events) == 1, "one event per call, not one per wrapper layer"
    assert getattr(llm.invoke, "__snagline_wrapped__", False) is True


def test_wrap_client_warns_when_nothing_patchable(caplog):
    class _Bare:
        pass

    with caplog.at_level("WARNING", logger="snagline"):
        wrap_client(_SpyMonitor(), _Bare())
    assert any("no langchain entrypoint" in r.message for r in caplog.records)


def test_instrument_langchain_global_is_idempotent(monkeypatch):
    """Global mode patched ``BaseLanguageModel`` / ``Chain`` through
    ``wrap_client``, which had no sentinel, so a second call double-wrapped
    the class and every subsequent call in the process emitted twice
    (issue #336). The classes are swapped for local fakes so the installed
    langchain_core is not mutated for the rest of the session."""
    import sys
    import types

    class _FakeBaseLanguageModel:
        def invoke(self, prompt, **kw):
            return "lm-ok"

    class _FakeChain:
        def invoke(self, prompt, **kw):
            return "chain-ok"

    # instrument_langchain imports Chain from langchain.chains.base, which
    # langchain 1.x no longer ships (issue #339); supply a stand-in.
    chains_mod = types.ModuleType("langchain.chains.base")
    chains_mod.Chain = _FakeChain  # type: ignore[attr-defined]
    for name in ("langchain", "langchain.chains", "langchain.chains.base"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "langchain.chains.base", chains_mod)
    import langchain_core.language_models as lm

    monkeypatch.setattr(lm, "BaseLanguageModel", _FakeBaseLanguageModel)

    mon = _SpyMonitor()
    assert instrument_langchain(mon) is True
    assert instrument_langchain(_SpyMonitor()) is True, "second call is idempotent"

    out = _FakeBaseLanguageModel().invoke("hi")
    assert out == "lm-ok"
    assert len(mon.events) == 1, "single event per call after two global installs"
