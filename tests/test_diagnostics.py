import pytest

from snagline.diagnostics import DiagnosticMetrics


def test_diagnostic_metrics_counters():
    metrics = DiagnosticMetrics()
    metrics.record_counter("step_count")
    metrics.record_counter("step_count", 2)
    assert metrics.counters["step_count"] == 3


def test_diagnostic_metrics_timings():
    metrics = DiagnosticMetrics()
    metrics.record_timing("step_latency_ms", 12.5)
    metrics.record_timing("step_latency_ms", 18.0)
    assert len(metrics.timings["step_latency_ms"]) == 2


def test_diagnostic_metrics_ring_buffer_cap():
    metrics = DiagnosticMetrics(max_samples=3)
    for i in range(5):
        metrics.record_timing("latency", float(i))
    assert metrics.timings["latency"] == [2.0, 3.0, 4.0]


def test_diagnostic_metrics_percentile():
    metrics = DiagnosticMetrics()
    for val in [10.0, 20.0, 30.0, 40.0, 50.0]:
        metrics.record_timing("lat", val)
    assert metrics.percentile("lat", 50.0) == 30.0
    assert metrics.percentile("lat", 0.0) == 10.0
    assert metrics.percentile("lat", 100.0) == 50.0
    assert metrics.percentile("missing", 50.0) is None


# Issue #728: max_samples is the capacity cap record_timing keeps against, so
# it must be validated like BoundedEventBuffer's capacity, not trusted.
def test_max_samples_zero_rejected():
    with pytest.raises(ValueError, match="max_samples"):
        DiagnosticMetrics(max_samples=0)


def test_max_samples_negative_rejected():
    with pytest.raises(ValueError, match="max_samples"):
        DiagnosticMetrics(max_samples=-5)


def test_max_samples_nan_rejected():
    # NaN disables the cap (len(series) >= NaN is always False), voiding the
    # "bounded memory usage" guarantee in docs/diagnostics.md.
    with pytest.raises(ValueError, match="max_samples"):
        DiagnosticMetrics(max_samples=float("nan"))  # type: ignore[arg-type]


def test_max_samples_one_keeps_only_newest():
    metrics = DiagnosticMetrics(max_samples=1)
    for value in (1.0, 2.0):
        metrics.record_timing("latency", value)
    assert metrics.timings["latency"] == [2.0]
