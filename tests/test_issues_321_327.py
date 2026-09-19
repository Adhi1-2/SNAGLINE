"""Regression tests for issues #321-#327.

Each of these was a silent contract violation -- a function that promised one
thing (a boolean, fail-open, atomicity, mutual exclusion) and did another
with no error anywhere. The tests pin the promised behaviour.
"""

from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
import types

import pytest

from snagline.config import Config
from snagline.risk import FailureRisk
from snagline.sinks.console import ConsoleSink
from snagline.sinks.heartbeat import HeartbeatSink


class _SpyMonitor:
    def __init__(self):
        self.events: list = []

    def ingest(self, event) -> None:
        self.events.append(event)


def _risk() -> FailureRisk:
    return FailureRisk("ep", "1", 0.9, "loop", "detail", 0.0)


# --- #321: instrument_*(monitor, client) must not claim success unpatched ---


class _BogusClient:
    """No patchable method on any of the three SDKs."""


class _OpenAIClient:
    class chat:
        class completions:
            def create(self, **kw):
                return "ok"


class _AnthropicClient:
    class messages:
        def create(self, **kw):
            return "ok"


class _LangChainClient:
    def invoke(self, s):
        return "ok"


@pytest.mark.parametrize(
    ("module_name", "fn_name", "good_client"),
    [
        ("snagline.auto.openai", "instrument_openai", _OpenAIClient),
        ("snagline.auto.anthropic", "instrument_anthropic", _AnthropicClient),
        ("snagline.auto.langchain", "instrument_langchain", _LangChainClient),
    ],
)
def test_instrument_returns_false_when_client_has_nothing_to_patch(
    module_name, fn_name, good_client
):
    """The explicit-client path must honour its documented boolean (issue #321).

    The global path already returned False on a no-op patch with a test
    asserting it; the explicit-client path returned True unconditionally, so a
    host gating monitoring setup on the return value believed LLM calls were
    observed when none were.
    """
    import importlib

    mod = importlib.import_module(module_name)
    assert getattr(mod, fn_name)(_SpyMonitor(), _BogusClient()) is False
    # A client with a real patchable surface still reports success.
    assert getattr(mod, fn_name)(_SpyMonitor(), good_client()) is True


@pytest.mark.parametrize(
    "module_name",
    ["snagline.auto.openai", "snagline.auto.anthropic", "snagline.auto.langchain"],
)
def test_wrap_client_still_returns_the_client_for_chaining(module_name):
    """The fix must not change wrap_client's signature (issue #321 notes)."""
    import importlib

    mod = importlib.import_module(module_name)
    client = _BogusClient()
    assert mod.wrap_client(_SpyMonitor(), client) is client


# --- #322: a 0 threshold must be a config error, not a dead detector -------


@pytest.mark.parametrize(
    "knob",
    [
        "cascade_consecutive_threshold",
        "cascade_error_threshold",
        "loop_repeat_threshold",
        "loop_stall_steps",
    ],
)
def test_zero_divisor_threshold_is_rejected_at_config_time(knob):
    """0 divides the score by zero at ingest; fail-open then kills the detector
    for the rest of the run with no further symptom (issue #322)."""
    with pytest.raises(ValueError, match="must be >= 1"):
        Config(**{knob: 0})  # type: ignore[arg-type]


def test_zero_divisor_threshold_rejected_through_env(monkeypatch):
    """Every config layer rejects it, not just the constructor."""
    monkeypatch.setenv("SNAGLINE_CASCADE_ERROR_THRESHOLD", "0")
    with pytest.raises(ValueError, match="must be >= 1"):
        Config.resolve()


def test_default_config_still_accepts_the_shipped_thresholds():
    """The validator must not reject any default value."""
    cfg = Config()
    assert cfg.loop_repeat_threshold >= 1
    assert cfg.cascade_error_threshold >= 1


def test_monitor_ingest_survives_a_mutated_zero_threshold():
    """Belt-and-braces: Config is a plain mutable dataclass, so a host can set
    0 after construction. The detector must score, not raise."""
    from snagline.events import StepEvent, make_signature
    from snagline.monitor import Monitor

    cfg = Config()
    cfg.loop_repeat_threshold = 0
    mon = Monitor.default(config=cfg)
    evt = StepEvent(
        step_id="0",
        episode_id="ep",
        timestamp=1.0,
        action_type="tool_call",
        action_signature=make_signature("tool_call", "t", "a"),
        tool_name="t",
        latency_ms=10.0,
    )
    mon.ingest(evt)  # must not raise
    mon.ingest(evt)
    assert mon._metrics.detector_errors == 0


# --- #323: HeartbeatSink.touch must never raise for any path --------------


def test_heartbeat_nul_path_stays_fail_open(caplog):
    """An embedded NUL makes os.utime raise ValueError, which the old
    `except OSError` let escape into the watch loop (issue #323)."""
    sink = HeartbeatSink(os.path.join(tempfile.mkdtemp(), "live\0beat"))
    with caplog.at_level(logging.WARNING):
        sink.touch()
    assert "unavailable" in caplog.text


def test_heartbeat_logs_a_dead_path_only_once(caplog):
    sink = HeartbeatSink(os.path.join(tempfile.mkdtemp(), "dead\0beat"))
    with caplog.at_level(logging.WARNING):
        sink.touch()
        sink.touch()
        sink.touch()
    assert caplog.text.count("unavailable") == 1


# --- #324: a malformed snapshot entry must not half-restore ---------------


def test_restore_dict_skips_malformed_detector_entry(caplog):
    """One bad entry used to crash after earlier detectors had already loaded,
    breaking the method's "leaves the monitor untouched" promise (issue #324)."""
    from snagline.monitor import Monitor

    mon = Monitor.default()
    snap = mon.snapshot_dict()
    snap["detectors"]["1:error_cascade"] = "not-a-dict"
    with caplog.at_level(logging.WARNING):
        mon.restore_dict(snap)  # must not raise
    assert "malformed snapshot entry" in caplog.text
    # The detectors after the malformed one still loaded: the first slot's
    # state is a dict snapshot, so a successful restore of it is observable.
    assert isinstance(snap["detectors"].get("0:loop"), dict)


def test_restore_dict_skips_a_sink_that_raises_on_load(caplog):
    """A sink whose load_state raises (a version-skewed state shape, a corrupt
    payload the sink itself does not defend) must not abort the restore."""
    from snagline.monitor import Monitor
    from snagline.sinks.base import AlertSink

    class _BoomSink(AlertSink):
        name = "boom"

        def dump_state(self):
            return {"ok": True}

        def load_state(self, state):
            raise RuntimeError("boom: cannot deserialize this version")

        def emit(self, risk):
            pass

    class _FineSink(AlertSink):
        name = "fine"
        loaded = None

        def dump_state(self):
            return {"ok": True}

        def load_state(self, state):
            _FineSink.loaded = state

        def emit(self, risk):
            pass

    mon = Monitor.default(sinks=[_BoomSink(), _FineSink()])
    snap = mon.snapshot_dict()
    with caplog.at_level(logging.WARNING):
        mon.restore_dict(snap)  # must not raise
    assert "malformed snapshot entry" in caplog.text
    # The entry *after* the failing one still loaded.
    assert _FineSink.loaded == {"ok": True}


def test_restore_dict_roundtrip_still_works():
    """The guard must not swallow legitimate restores."""
    from snagline.monitor import Monitor
    from snagline.sinks.dedup import DedupSink

    mon = Monitor.default(sinks=[DedupSink(ConsoleSink(), cooldown_seconds=10)])
    snap = mon.snapshot_dict()
    mon2 = Monitor.default(sinks=[DedupSink(ConsoleSink(), cooldown_seconds=10)])
    mon2.restore_dict(snap)  # no warning path, no raise


# --- #325 / #326: the Redis episode lock must actually lock ---------------


class _LockTimeout:
    """redis-py returns False (does not raise) on blocking_timeout."""

    def __init__(self, name, timeout=None, **kw):
        self.name, self.timeout = name, timeout

    def acquire(self, blocking=None, blocking_timeout=None, token=None):
        return False

    def release(self):
        return True


class _LockNotOwned:
    """redis-py raises LockNotOwnedError from release() when the TTL expired."""

    def __init__(self, name, timeout=None, **kw):
        self.name, self.timeout = name, timeout

    def acquire(self, blocking=None, blocking_timeout=None, token=None):
        return True

    def release(self):
        raise LockNotOwnedError("Cannot release a lock that's no longer owned")


class LockNotOwnedError(Exception):
    pass


def _install_fake_redis(monkeypatch, lock_cls):
    fake = types.ModuleType("redis")
    lock_mod = types.ModuleType("redis.lock")
    lock_mod.Lock = lock_cls  # type: ignore[attr-defined]
    lock_mod.LockNotOwnedError = LockNotOwnedError  # type: ignore[attr-defined]

    class _R:
        def lock(self, name, timeout=None, **kw):
            return lock_cls(name, timeout)

    redis_cls = type("R", (), {"from_url": classmethod(lambda cls, u: _R())})
    fake.Redis = redis_cls  # type: ignore[attr-defined]
    fake.lock = lock_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis", fake)
    monkeypatch.setitem(sys.modules, "redis.lock", lock_mod)


def test_redis_lock_raises_when_acquisition_times_out(monkeypatch):
    """Yielding anyway ran the whole critical section with no lock held (issue
    #325) -- the exact guarantee this backend exists to provide, silently off."""
    _install_fake_redis(monkeypatch, _LockTimeout)
    from snagline.state import RedisStateBackend

    with pytest.raises(RuntimeError, match="could not acquire"):
        with RedisStateBackend("redis://example/0").episode_lock("ep-1"):
            pass  # must never run


def test_redis_lock_swallows_release_of_an_expired_lock(monkeypatch, caplog):
    """A TTL expiry made release() raise out of the finally, surfacing from the
    caller's ingest/end_episode looking like the episode's own work raised
    (issue #326). The lock is already gone; there is nothing to release."""
    _install_fake_redis(monkeypatch, _LockNotOwned)
    from snagline.state import RedisStateBackend

    backend = RedisStateBackend("redis://example/0")
    with caplog.at_level(logging.WARNING):
        with backend.episode_lock("ep-1"):
            assert True  # section ran and completed
    assert "was lost before release" in caplog.text


def test_redis_lock_timeout_is_configurable(monkeypatch):
    """Operators can size the TTL to their episodes (issue #326 fix)."""
    _install_fake_redis(monkeypatch, _LockNotOwned)
    from snagline.state import RedisStateBackend

    backend = RedisStateBackend("redis://example/0", lock_timeout=900.0)
    assert backend._lock_timeout == 900.0


# --- #327: a closed stream must not raise out of the default sink ---------


def test_console_sink_closed_stream_does_not_raise(caplog):
    """Closing raises ValueError, not OSError, so it escaped emit() into the
    host ingest path (issue #327). ConsoleSink is the default sink."""
    stream = io.StringIO()
    stream.close()
    sink = ConsoleSink(stream=stream)
    with caplog.at_level(logging.WARNING):
        sink.emit(_risk())
    assert "write to stream failed" in caplog.text


def test_console_sink_warns_only_once_for_a_dead_stream(caplog):
    """The comment said "logged once" but the warning fired per alert."""
    stream = io.StringIO()
    stream.close()
    sink = ConsoleSink(stream=stream)
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            sink.emit(_risk())
    assert caplog.text.count("write to stream failed") == 1


def test_console_sink_recovers_when_the_stream_works_again(caplog):
    """The latch must reset once writes succeed again, mirroring HeartbeatSink."""
    import io as io_mod

    stream = io_mod.StringIO()
    sink = ConsoleSink(stream=stream)
    sink._fault_logged = True  # pretend a prior failure
    with caplog.at_level(logging.WARNING):
        sink.emit(_risk())
    assert "write to stream failed" not in caplog.text
    assert sink._fault_logged is False
