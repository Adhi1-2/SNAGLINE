"""Tests for the token-runaway detector (issue #84)."""

from __future__ import annotations

from typing import Any, cast

import pytest

from snagline.detectors.token_runaway import TokenRunawayDetector
from snagline.events import StepEvent


def _event(
    step_id: int,
    tokens: int | None,
    episode: str = "ep",
    **kwargs: Any,
) -> StepEvent:
    return StepEvent(
        step_id=str(step_id),
        episode_id=episode,
        timestamp=float(step_id),
        action_type="tool_call",
        action_signature=f"s{step_id}",
        tokens_in=tokens,
        error=False,
        **kwargs,
    )


def _run(d: TokenRunawayDetector, events: list[StepEvent]) -> list:
    return [r for e in events if (r := d.observe(e)) is not None]


def test_sustained_burn_fires_after_warmup():
    d = TokenRunawayDetector(min_samples=10)
    warm = [_event(i, 100) for i in range(10)]
    assert _run(d, warm) == [], "warm-up must stay silent"
    hot = [_event(i, 400) for i in range(10, 15)]
    risks = _run(d, hot)
    assert risks, "sustained 4x burn must fire"
    assert risks[0].trigger == "token_runaway"


def test_stable_high_volume_no_false_positive():
    d = TokenRunawayDetector(min_samples=5)
    risks = _run(d, [_event(i, 5000) for i in range(40)])
    assert risks == [], f"stable volume false-positive: {risks}"


def test_envelope_warns_once_then_breaches_once():
    d = TokenRunawayDetector(budget_total_tokens=1000, warn_fraction=0.8)
    risks = []
    step = 0
    for expected_total in (300, 600, 900, 1200, 1500):
        risks.extend(_run(d, [_event(step, 300)]))
        step += 1
    triggers = [(r.trigger, r.score) for r in risks]
    # Step 3 (total 900 >= 80% of 1000): one warning. Step 4 (total 1200):
    # one breach. Step 5: silence -- envelope emits at most once per threshold.
    assert ("token_runaway", 0.8) in triggers
    assert ("budget_breach", 1.0) in triggers
    assert triggers.count(("budget_breach", 1.0)) == 1
    assert triggers.index(("budget_breach", 1.0)) > triggers.index(
        ("token_runaway", 0.8)
    )


def test_events_without_tokens_are_ignored():
    d = TokenRunawayDetector(budget_total_tokens=100)
    assert d.observe(_event(0, None)) is None
    assert d._totals == {}, "no-token events must not accumulate"


def test_reset_clears_envelope_and_cusum():
    d = TokenRunawayDetector(min_samples=2, budget_total_tokens=400)
    _run(d, [_event(0, 150), _event(1, 150), _event(2, 150)])  # crosses 80% (360)
    d.reset("ep")
    assert d._totals == {}
    risks = _run(d, [_event(3, 350)])
    assert [r.trigger for r in risks] == ["token_runaway"], (
        "after reset the warning must be able to fire again"
    )


def test_state_round_trip_preserves_behavior():
    d1 = TokenRunawayDetector(min_samples=3, budget_total_tokens=2000)
    _run(d1, [_event(i, 500) for i in range(4)])  # partial progress, warned at 2000*0.8
    d2 = TokenRunawayDetector(min_samples=3, budget_total_tokens=2000)
    d2.load_state(d1.dump_state())
    rest = [_event(i, 500) for i in range(4, 6)]  # 2000 -> 2500: breach
    assert [(r.trigger, r.step_id) for r in _run(d1, rest)] == [
        (r.trigger, r.step_id) for r in _run(d2, rest)
    ], "restored detector must behave identically"


def test_zero_budget_does_not_page_at_critical_on_the_first_step():
    """Issue #317 end-to-end: ``SNAGLINE_EPISODE_TOKEN_BUDGET=0`` used to reach
    the detector and emit a score-1.0 budget_breach on the *first* token-bearing
    step (``total >= budget`` was ``10 >= 0``). The bad value is now refused at
    configuration time, so a monitor built from it never exists and no critical
    risk can be dispatched."""
    from snagline import Monitor
    from snagline.config import Config
    from snagline.sinks.base import AlertSink

    class Collect(AlertSink):
        def __init__(self) -> None:
            self.risks: list = []

        def emit(self, risk) -> None:
            self.risks.append(risk)

    sink = Collect()
    with pytest.raises(ValueError, match="episode_token_budget"):
        Monitor.default(
            config=Config(token_runaway_enabled=True, episode_token_budget=0),
            sinks=[sink],
        )
    assert sink.risks == [], "no risk may be dispatched from a rejected config"


def test_nonpositive_budget_is_rejected():
    """Issue #317: direct construction with a non-positive budget is a
    configuration error, mirroring the StagnationDetector precedent
    (issue #132): direct kwargs skip the Config check, so the detector guards
    itself."""
    for budget in (0, -1, -1000):
        with pytest.raises(ValueError, match="budget_total_tokens"):
            TokenRunawayDetector(budget_total_tokens=budget)


def test_warn_fraction_out_of_range_is_rejected():
    """Issue #317: ``warn_fraction <= 0`` put the warning threshold at or below
    zero, so it fired on the first step; ``> 1.0`` put it above the budget, so
    the breach silenced it and the warning could never fire. Both are
    configuration errors."""
    for fraction in (0.0, -0.5, 1.5):
        with pytest.raises(ValueError, match="warn_fraction"):
            TokenRunawayDetector(budget_total_tokens=1000, warn_fraction=fraction)


def test_none_budget_still_disables_the_envelope():
    """Issue #317 regression guard: the new range check must not tighten the
    documented ``None disables envelope`` contract. The CUSUM path keeps its
    own state either way; only the envelope bookkeeping is budget-gated."""
    d = TokenRunawayDetector(budget_total_tokens=None, min_samples=5)
    _run(d, [_event(i, 100) for i in range(10)])
    assert d._totals == {}, "no budget means the envelope tracks nothing"


def _usage_event(step_id: int, tokens_in: float, tokens_out: float = 0.0) -> StepEvent:
    # Raw ``usage``-derived values, as adapters decode them: floats, possibly
    # non-finite (a malformed provider blob) -- the int() conversion happens
    # inside observe(), and before issue #349 it happened *before* validation.
    # StepEvent's field is int|None, so a raw float needs the cast the adapter
    # would normally perform; the point here is the pre-conversion value.
    return StepEvent(
        step_id=str(step_id),
        episode_id="ep",
        timestamp=float(step_id),
        action_type="tool_call",
        action_signature=f"u{step_id}",
        tool_name="search",
        tokens_in=cast("int | None", tokens_in),
        tokens_out=cast("int | None", tokens_out),
    )


@pytest.mark.parametrize(
    "tokens_in,tokens_out",
    [
        (float("nan"), 0.0),
        (0.0, float("nan")),
        (float("inf"), 0.0),
        (0.0, float("-inf")),
        (float("nan"), float("inf")),
        (-50.0, 0.0),
        (10.0, -100.0),
    ],
)
def test_non_measurement_token_counts_are_dropped_not_fatal(
    tokens_in: float, tokens_out: float
):
    """Issue #349: a token count that is not a usable measurement must be
    treated as "no signal for this step", exactly like an event carrying
    neither field. Before the fix ``int(nan)`` raised ValueError and
    ``int(inf)`` OverflowError out of ``observe()`` -- both are swallowed by
    Monitor.ingest's fail-open guard, so the detector stayed installed and
    reported nothing for the rest of the run. A negative count is not a
    measurement either, and silently *reducing* the running budget total
    would let a bad adapter hide a real breach."""
    d = TokenRunawayDetector(budget_total_tokens=1000)
    assert d.observe(_usage_event(0, tokens_in, tokens_out)) is None
    assert d._totals.get("ep", 0) == 0, "a non-measurement must not accumulate"


def test_detector_stays_live_after_a_malformed_usage_blob():
    """Issue #349 regression: the point of dropping the bad sample is that the
    detector keeps working for the rest of the run. A NaN on step 0 must not
    cost the envelope its warning or its breach."""
    d = TokenRunawayDetector(budget_total_tokens=1000)
    assert d.observe(_usage_event(0, float("nan"))) is None
    assert d.observe(_usage_event(1, -9999.0)) is None
    assert d._totals.get("ep", 0) == 0

    warned = d.observe(_usage_event(2, 850.0))
    assert warned is not None and warned.trigger == "token_runaway"
    breach = d.observe(_usage_event(3, 200.0))
    assert breach is not None and breach.trigger == "budget_breach"


def test_negative_count_cannot_hide_a_breach():
    """Issue #349: the envelope total is cumulative, so a negative count used
    to offset real spend (``-900 + 1100 = 200`` -- under budget). It is now
    dropped, so 1100 alone breaches as it should."""
    d = TokenRunawayDetector(budget_total_tokens=1000)
    assert d.observe(_usage_event(0, -900.0)) is None
    r = d.observe(_usage_event(1, 1100.0))
    assert r is not None
    assert r.trigger == "budget_breach"


def test_nan_mid_run_leaves_the_cusum_baseline_intact():
    """Issue #349: the CUSUM path shares the poisoning risk with the latency
    detector (issue #350). A NaN must not reach the Welford learner or the
    CUSUM accumulator; a healthy baseline keeps scoring normally after it."""
    d = TokenRunawayDetector(budget_total_tokens=None, min_samples=3)
    for i in range(3):
        assert d.observe(_usage_event(i, 100.0)) is None
    state = d._states["ep"]
    assert state.frozen and state.mu0 == 100.0

    assert d.observe(_usage_event(3, float("nan"))) is None
    assert state.mu0 == 100.0, "baseline must be unchanged by a NaN"
    assert state.cusum == 0.0, "accumulated drift must not be zeroed by a NaN"

    alarmed = [d.observe(_usage_event(i, 10000.0)) is not None for i in range(4, 10)]
    assert any(alarmed), "detector must still alarm after the bad sample"
