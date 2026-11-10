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


def test_diagnostic_metrics_invalid_max_samples():
    for invalid_val in [
        0,
        -1,
        -100,
        float("nan"),
        float("inf"),
        float("-inf"),
        3.14,
        True,
        False,
        "1000",
        None,
    ]:
        with pytest.raises(ValueError, match="max_samples must be a positive integer"):
            DiagnosticMetrics(max_samples=invalid_val)  # type: ignore


def test_diagnostic_metrics_valid_max_samples_boundary():
    metrics = DiagnosticMetrics(max_samples=1)
    metrics.record_timing("lat", 10.0)
    metrics.record_timing("lat", 20.0)
    assert metrics.timings["lat"] == [20.0]


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), float("-inf"), -1.0, -0.5, -1e-9]
)
def test_record_timing_rejects_non_finite_and_negative(bad: float):
    # Issue #774: a latency series feeds percentile() via sorted(), and a NaN
    # sorts to an arbitrary position -- making the interpolation return NaN --
    # while a negative sample (an out-of-order clock, an erroneous probe)
    # poisons every summary derived from the series. The bad sample must be
    # rejected at ingestion, and rejected whole: a rejected value must not
    # occupy a slot in the capped series either.
    metrics = DiagnosticMetrics(max_samples=3)
    with pytest.raises(ValueError, match="non-negative finite"):
        metrics.record_timing("lat", bad)
    assert metrics.timings == {}


def test_record_timing_accepts_zero_and_fractional_samples():
    # Zero latency is a legitimate (if alarming) measurement, not an invalid
    # one -- it must record like any other finite non-negative sample.
    metrics = DiagnosticMetrics()
    metrics.record_timing("lat", 0.0)
    metrics.record_timing("lat", 0.25)
    assert metrics.timings["lat"] == [0.0, 0.25]
    assert metrics.percentile("lat", 50.0) == 0.125


def test_recorded_series_stays_percentile_clean():
    # The NaN that gets in is the one that breaks percentile later; assert the
    # guard's payoff at the read site, not just at the raise.
    metrics = DiagnosticMetrics()
    for v in [10.0, 20.0, 30.0]:
        metrics.record_timing("lat", v)
    with pytest.raises(ValueError, match="non-negative finite"):
        metrics.record_timing("lat", float("nan"))
    assert metrics.percentile("lat", 50.0) == 20.0
