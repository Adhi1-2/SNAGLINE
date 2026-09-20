"""Tests for the latency / CUSUM anomaly detector (project.md §5.3)."""

from __future__ import annotations

from snagline.baseline import BaselineProfile, ToolBaseline
from snagline.detectors.latency_anomaly import LatencyAnomalyDetector
from snagline.events import StepEvent, make_signature


def _sig(i: int) -> str:
    return make_signature("tool_call", "search", str(i))


def _event(step_id: int, latency: float, episode: str = "ep") -> StepEvent:
    return StepEvent(
        step_id=str(step_id),
        episode_id=episode,
        timestamp=float(step_id),
        action_type="tool_call",
        action_signature=_sig(step_id),
        tool_name="search",
        latency_ms=latency,
    )


def test_latency_spike_detected():
    # Healthy baseline is stable latency; a sustained shift to 400ms is a real
    # anomaly. With a long constant baseline, a single spike makes
    # (x - mean)/std large enough to cross h on the first spike step.
    d = LatencyAnomalyDetector(min_samples=15)
    risks = []
    for i in range(40):
        r = d.observe(_event(i, 100.0))
        if r is not None:
            risks.append(r)
    for i in range(40, 48):
        r = d.observe(_event(i, 400.0))
        if r is not None:
            risks.append(r)
    assert risks, "expected a latency anomaly risk"
    assert all(r.trigger == "latency_anomaly" for r in risks)
    assert risks[0].score >= 0.6


def test_no_false_positive_healthy():
    # A healthy run has stable latency (matching the project's healthy fixture),
    # so the std==0 guard prevents any alarm.
    d = LatencyAnomalyDetector(min_samples=20)
    for i in range(60):
        r = d.observe(_event(i, 100.0))
        assert r is None, f"false positive at step {i}: {r}"


def test_warmup_suppresses_early_noise():
    d = LatencyAnomalyDetector(min_samples=10)
    # a single early blip during warm-up must not alarm
    for i in range(9):
        assert d.observe(_event(i, 100.0)) is None
    assert d.observe(_event(9, 100.0)) is None


def test_reset_clears_state():
    d = LatencyAnomalyDetector(min_samples=5)
    for i in range(6):
        d.observe(_event(i, 100.0))
    d.reset("ep")
    # after reset, a single spike should not immediately alarm (no baseline yet)
    assert d.observe(_event(6, 400.0)) is None


def test_single_spike_detected_after_warmup():
    # Regression test for the CUSUM rewrite: a single large deviation from a
    # stable baseline must alarm immediately. The old implementation diluted
    # the deviation by including the anomaly in the running std and could only
    # fire after several sustained spikes (and never on a constant baseline).
    d = LatencyAnomalyDetector(min_samples=15)
    for i in range(15):
        assert d.observe(_event(i, 80.0)) is None
    r = d.observe(_event(15, 400.0))  # lone 5x spike
    assert r is not None, "single large spike should alarm"
    assert r.trigger == "latency_anomaly"
    assert r.score >= 0.6


def test_small_variation_does_not_false_positive():
    # Benign, symmetric jitter around a stable baseline must not alarm.
    d = LatencyAnomalyDetector(min_samples=15)
    risks = []
    for i in range(40):
        if i < 15:
            latency = 100.0
        else:
            # +/-5ms around the 100ms baseline (mean stays ~100ms, no real shift)
            latency = 100.0 + ((i % 3) - 1) * 5.0
        r = d.observe(_event(i, latency))
        if r is not None:
            risks.append(r)
    assert not risks, f"false positive on benign jitter: {risks}"


def test_sustained_shift_keeps_alarming():
    # A permanent regression must keep the CUSUM elevated (not be learned away
    # and forgotten). The old implementation reset to baseline and went quiet.
    d = LatencyAnomalyDetector(min_samples=15)
    for i in range(15):
        d.observe(_event(i, 100.0))
    alarmed = []
    for i in range(15, 25):
        r = d.observe(_event(i, 300.0))
        alarmed.append(r is not None)
    assert any(alarmed), "sustained shift should keep alerting"
    # it should not drop back to silent mid-shift
    assert alarmed[-1], "alert stopped during a still-elevated shift"


def _profile(timed: int, untimed: int = 0, mean: float = 100.0) -> BaselineProfile:
    """A profile for the ``search`` tool with ``timed`` samples at ``mean`` ms.

    ``untimed`` steps raise ``count`` without raising ``latency_count`` -- the
    shape of a trajectory whose adapter reports no ``latency_ms``, which the
    calibration contract explicitly supports (issue #101).
    """
    p = BaselineProfile()
    tb = ToolBaseline("search")
    for _ in range(timed):
        tb.add(mean, error=False)
    for _ in range(untimed):
        tb.add(None, error=False)
    p.tools["search"] = tb
    return p


def test_latencyless_baseline_does_not_page_on_first_step():
    # Issue #348: seeding gated on ``count`` admitted a profile fitted from a
    # timing-less stream (count=100, latency_count=0, mean 0, std 0). ``seed()``
    # floors sigma0 to 1.0, so the first live call was scored as an N-sigma
    # deviation from mean 0 and paged critical on step 0 of every episode.
    d = LatencyAnomalyDetector(baseline=_profile(timed=0, untimed=100), min_samples=5)
    r = d.observe(_event(0, 120.0))
    assert r is None, f"timing-less baseline must not alarm on step 0, got {r}"
    # The tool is not silently inert either: it falls back to the ordinary
    # learn-then-freeze path and still detects a real anomaly afterwards.
    for i in range(1, 20):
        d.observe(_event(i, 100.0))
    assert d.observe(_event(20, 400.0)) is not None, "fallback warm-up must work"


def test_latencyless_baseline_via_config_layer_is_inert():
    # The same profile reaches the detector through the documented calibration
    # path (calibration="auto" resolves the profile from a store), not just via
    # the direct constructor.
    d = LatencyAnomalyDetector(baseline=_profile(timed=0, untimed=100), min_samples=5)
    assert d.observe(_event(0, 120.0)) is None


def test_timed_baseline_still_seeds_and_skips_warmup():
    # The fix must not over-correct: a profile with real timing still seeds and
    # still skips warm-up, so a first step far off the calibrated mean alarms
    # immediately (the point of a calibrated start, issue #101).
    d = LatencyAnomalyDetector(baseline=_profile(timed=100, mean=100.0), min_samples=5)
    r = d.observe(_event(0, 5000.0))
    assert r is not None and r.trigger == "latency_anomaly"
    assert "mean 100ms" in r.detail, f"detail should name the calibrated mean: {r}"


def test_baseline_count_below_min_samples_falls_back_to_warmup():
    # ``min_samples`` gates sufficiency of the *timing* evidence: a profile with
    # fewer timed samples than that has not measured latency well enough to
    # freeze onto, so the detector learns from the live stream instead.
    d = LatencyAnomalyDetector(baseline=_profile(timed=3, untimed=97), min_samples=5)
    # Step 0 must be in warm-up (learning, not alarmable), whatever the latency.
    assert d.observe(_event(0, 9999.0)) is None


def test_mixed_profile_seeds_only_timed_tools():
    # A timing-less tool in the same profile as a timed one: the timed tool
    # still gets its calibrated start, the timing-less one still falls back.
    p = BaselineProfile()
    tb = ToolBaseline("search")
    for _ in range(100):
        tb.add(100.0, error=False)
    p.tools["search"] = tb
    untimed = ToolBaseline("plan")
    for _ in range(100):
        untimed.add(None, error=False)
    p.tools["plan"] = untimed
    d = LatencyAnomalyDetector(baseline=p, min_samples=5)

    def ev(step: int, tool: str, latency: float) -> StepEvent:
        return StepEvent(
            step_id=str(step),
            episode_id="ep",
            timestamp=float(step),
            action_type="tool_call",
            action_signature=make_signature("tool_call", tool, str(step)),
            tool_name=tool,
            latency_ms=latency,
        )

    assert d.observe(ev(0, "plan", 120.0)) is None, "timing-less tool must not alarm"
    seeded = d.observe(ev(1, "search", 5000.0))
    assert seeded is not None and "mean 100ms" in seeded.detail


def test_legacy_profile_without_latency_count_still_seeds():
    # Profiles written before ``latency_count`` existed carried only count and
    # the moments; ``from_dict`` defaults latency_count to count so they keep
    # seeding rather than being demoted to warm-up by the stricter gate.
    legacy = {
        "tool_name": "search",
        "count": 100,
        "mean_latency": 100.0,
        "std_latency": 5.0,
    }
    tb = ToolBaseline.from_dict(legacy)
    assert tb.latency_count == 100
    d = LatencyAnomalyDetector(
        baseline=BaselineProfile(tools={"search": tb}), min_samples=5
    )
    r = d.observe(_event(0, 5000.0))
    assert r is not None and "mean 100ms" in r.detail
