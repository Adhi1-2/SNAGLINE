# Telemetry Diagnostics

The `DiagnosticMetrics` collector provides lightweight runtime telemetry with bounded memory usage:

- Fixed ring-buffer capacity per metric series (`DEFAULT_MAX_SAMPLES = 1000`).
- Exact count accumulation for step events.
- Quantile/percentile interpolation for latency analysis.
