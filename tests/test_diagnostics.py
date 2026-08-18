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
