"""Issue #155 regression: every adapter clock defaults to ``time.perf_counter``.

``time.time()`` advances in ~15.6 ms ticks on Windows, so any tool/LLM call
shorter than one tick recorded ``latency_ms == 0.0`` and fast operations were
indistinguishable from instant failures. That starves the CUSUM latency
detector of usable samples.

These tests reuse the deterministic scripted-clock pattern introduced in
PR #139 (commit b42d47c), but pointed at the DEFAULT path: instead of
injecting ``clock=``, we script ``time.perf_counter`` itself. If an adapter
ever falls back to ``time.time``, the scripted readings are never consumed and
the assertions fail, so a regression cannot hide behind platform timing.
"""

from __future__ import annotations

import time
from typing import Any, cast

import pytest

from snagline.adapters.anthropic import observe_anthropic_call
from snagline.adapters.autogen import SnaglineAutogenHandler
from snagline.adapters.crewai import observe_crewai_step
from snagline.adapters.langchain_adapter import SnaglineCallbackHandler
from snagline.adapters.openai import observe_openai_call
from snagline.monitor import Monitor


class RecordingSink:
    def __init__(self) -> None:
        self.risks: list[Any] = []

    def emit(self, risk: Any) -> None:
        self.risks.append(risk)


class RecMonitor(Monitor):
    """Monitor that also records every ingested event for assertions."""

    def __init__(
        self, detectors: list[Any], sinks: list[Any], fail_open: bool = True
    ) -> None:
        super().__init__(detectors, sinks, fail_open=fail_open)
        self.events: list[Any] = []

    def ingest(self, event: Any) -> None:
        self.events.append(event)
        super().ingest(event)


def _monitor() -> RecMonitor:
    return cast(RecMonitor, RecMonitor.default(sinks=[RecordingSink()]))


def _script(*values: float):
    """Scripted clock: one value per expected reading, then fail loudly."""
    reads = iter(values)
    return lambda: next(reads)


# Sub-millisecond deltas built from binary-exact floats: 2**-10 seconds is
# exactly representable and its difference from the base reading is exact, so
# the asserted latency_ms values hold bit-for-bit on every platform.
T0 = 1024.0
SUB_MS_S = 2**-10  # 0.9765625 ms


def test_langchain_default_clock_preserves_sub_ms_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Default path: NO injected clock. Read order per successful tool call:
    # on_tool_start captures one reading, on_tool_end reads twice more (once
    # in _latency_from, once for the event timestamp).
    monkeypatch.setattr(
        time, "perf_counter", _script(T0, T0 + SUB_MS_S, T0 + 2 * SUB_MS_S)
    )
    mon = _monitor()
    h = SnaglineCallbackHandler(mon, "ep-155")

    h.on_tool_start({"name": "search"}, "query=cat", run_id="r1")
    h.on_tool_end("result", run_id="r1")

    e = mon.events[0]
    assert e.latency_ms == pytest.approx(SUB_MS_S * 1000.0, rel=1e-12)
    assert e.latency_ms > 0.0  # exactly what the Windows tick quantized to zero


def test_autogen_default_clock_reads_perf_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripted = T0 + SUB_MS_S
    monkeypatch.setattr(time, "perf_counter", _script(scripted))
    mon = _monitor()
    h = SnaglineAutogenHandler(mon, "ep-155")

    events = h.observe({"type": "TextMessage", "content": "hello"})

    assert len(events) == 1
    assert events[0].timestamp == scripted


def test_crewai_default_clock_reads_perf_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripted = T0 + SUB_MS_S
    monkeypatch.setattr(time, "perf_counter", _script(scripted))
    mon = _monitor()

    e = observe_crewai_step(mon, "ep-155", {"tool": "search", "output": "hits"})

    assert e.timestamp == scripted


def test_openai_observe_reads_perf_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    scripted = T0 + SUB_MS_S
    monkeypatch.setattr(time, "perf_counter", _script(scripted))
    mon = _monitor()

    e = observe_openai_call(mon, episode_id="ep-155", model="gpt-4o")

    assert e.timestamp == scripted


def test_anthropic_observe_reads_perf_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    scripted = T0 + SUB_MS_S
    monkeypatch.setattr(time, "perf_counter", _script(scripted))
    mon = _monitor()

    e = observe_anthropic_call(mon, episode_id="ep-155", model="claude")

    assert e.timestamp == scripted


def test_raw_default_clock_reads_perf_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default path: NO injected clock. raw.watch stamps the event timestamp
    # directly, so exactly one scripted reading is consumed per step.
    scripted = T0 + SUB_MS_S
    monkeypatch.setattr(time, "perf_counter", _script(scripted))
    mon = _monitor()

    from snagline.adapters.raw import watch

    with watch(mon, "ep-155") as step:
        e = step("tool_call", tool_name="search")

    assert e.timestamp == scripted


def test_langgraph_default_clock_reads_perf_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # watch_graph computes latency AND the event timestamp from its clock, so
    # both must come from perf_counter on the default path. Read order per
    # yielded update: one for the latency/timestamp pair, then the timestamp
    # is reused (a stray time.time() here would leave timestamp != the value
    # the latency was measured from).
    t0, t1 = T0, T0 + SUB_MS_S
    monkeypatch.setattr(time, "perf_counter", _script(t0, t1))
    mon = _monitor()

    from snagline.adapters.langgraph_adapter import watch_graph

    events: list[Any] = []
    mon.ingest = events.append  # type: ignore[method-assign, assignment]

    list(watch_graph(mon, "ep-155", iter([{"node": {"x": 1}}])))

    assert len(events) == 1
    assert events[0].timestamp == t1
    assert events[0].latency_ms == pytest.approx(SUB_MS_S * 1000.0, rel=1e-12)


def test_claude_code_default_clock_reads_perf_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A payload carries no timestamp, so payload_to_event stamps one itself.
    scripted = T0 + SUB_MS_S
    monkeypatch.setattr(time, "perf_counter", _script(scripted))

    from snagline.adapters.claude_code import payload_to_event

    e = payload_to_event(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s",
            "tool_use_id": "u",
            "tool_name": "Bash",
            "tool_input": {"cmd": "ls"},
        }
    )

    assert e is not None
    assert e.timestamp == scripted
