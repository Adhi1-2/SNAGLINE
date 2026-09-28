"""Tests for the console sink (issue #19: fire-and-forget on a broken stream)."""

from __future__ import annotations

import logging

from snagline.risk import FailureRisk
from snagline.sinks.console import ConsoleSink


def _risk() -> FailureRisk:
    return FailureRisk("ep-1", "step-1", 0.8, "loop", "test detail", 1.0)


def test_console_sink_writes_json_line_to_stream():
    import io

    buf = io.StringIO()
    sink = ConsoleSink(stream=buf)
    sink.emit(_risk())
    line = buf.getvalue().strip()
    assert line.startswith("{") and '"trigger": "loop"' in line


def test_console_sink_routes_through_logger():
    records: list[logging.LogRecord] = []

    class _Cap(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("snagline.test.console")
    cap = _Cap()
    logger.addHandler(cap)
    try:
        sink = ConsoleSink(logger=logger, level=logging.WARNING)
        sink.emit(_risk())
    finally:
        logger.removeHandler(cap)
    assert len(records) == 1
    assert '"trigger": "loop"' in records[0].getMessage()


def test_console_sink_is_fire_and_forget_on_broken_stream() -> None:
    # Issue #19: a broken stream must not raise out of emit(); the sink is
    # part of the fail-open ingest path.
    class BrokenStream:
        def write(self, s: str) -> int:
            raise OSError("stream broken")

        def flush(self) -> None:
            raise OSError("stream broken")

    sink = ConsoleSink(stream=BrokenStream())  # type: ignore[arg-type]
    # Must not raise.
    sink.emit(_risk())


def test_console_sink_min_severity_filters_below_threshold() -> None:
    # The console is the default escalation target, so --min-severity must
    # filter it like the webhook/slack/pagerduty sinks do (issue #248 wired
    # only those three). A critical-only console must drop warning/info risks
    # and keep critical ones.
    import io

    buf = io.StringIO()
    sink = ConsoleSink(stream=buf, min_severity="critical")
    sink.emit(FailureRisk("ep", "s-warn", 0.6, "loop", "warn", 1.0))  # warning
    sink.emit(FailureRisk("ep", "s-info", 0.3, "loop", "info", 2.0))  # info
    assert buf.getvalue() == "", "below-threshold risks must be dropped"
    sink.emit(FailureRisk("ep", "s-crit", 0.9, "loop", "crit", 3.0))  # critical
    out = buf.getvalue()
    assert '"step_id": "s-crit"' in out
    assert "s-warn" not in out and "s-info" not in out


def test_console_sink_no_min_severity_emits_everything() -> None:
    # Default (no filter) keeps the pre-#248 behaviour: every risk prints.
    import io

    buf = io.StringIO()
    sink = ConsoleSink(stream=buf)
    sink.emit(FailureRisk("ep", "s-info", 0.1, "loop", "info", 1.0))
    assert '"step_id": "s-info"' in buf.getvalue()
