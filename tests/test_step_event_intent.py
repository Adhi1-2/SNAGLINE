"""Schema and adapter pass-through tests for ``StepEvent.intent`` (#603).

The field is host-declared: adapters forward it when given, and it defaults
to None so backwards-compatible payloads and callers are unchanged.
"""

from __future__ import annotations

from typing import Any

from snagline.adapters.raw import watch
from snagline.events import StepEvent, make_signature


class _RecordingMonitor:
    def __init__(self) -> None:
        self.events: list[StepEvent] = []

    def ingest(self, event: StepEvent) -> None:
        self.events.append(event)


def test_intent_defaults_to_none():
    event = StepEvent(
        step_id="0",
        episode_id="ep",
        timestamp=0.0,
        action_type="tool_call",
        action_signature="abc",
    )
    assert event.intent is None


def test_step_event_loads_old_payloads_without_intent():
    payload: dict[str, Any] = {
        "step_id": "s1",
        "episode_id": "ep",
        "timestamp": 1.0,
        "action_type": "tool_call",
        "action_signature": "abc123",
    }
    assert StepEvent(**payload).intent is None


def test_raw_adapter_passes_intent_through():
    mon = _RecordingMonitor()
    with watch(mon, "ep") as step:
        read = step("tool_call", tool_name="search", args="q")
        research = step(
            "tool_call",
            tool_name="search",
            args="finance",
            intent="research financial records",
        )
    assert read.intent is None
    assert research.intent == "research financial records"
    assert [e.intent for e in mon.events] == [None, "research financial records"]


def test_signature_helper_ignores_intent():
    """Signatures for loop detection reason on action types and tool arguments,
    not intent strings."""
    a = make_signature("tool_call", "search", "q")
    b = make_signature("tool_call", "search", "q")
    assert a == b


# --- SDK observation functions (issue #773) ----------------------------------
# watch() was updated for #603 but observe_openai_call/observe_anthropic_call
# were not: neither accepted intent, so hosts observing LLM calls through the
# SDK adapters could not declare step intent at all and GoalDriftDetector never
# saw one from that path. The pass-through contract here is the same one the
# raw adapter obeys: forward exactly what the host declared, nothing derived.


def test_openai_observe_passes_intent_through():
    from snagline.adapters.openai import observe_openai_call

    mon = _RecordingMonitor()
    event = observe_openai_call(
        mon,
        episode_id="ep",
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        intent="research financial records",
    )
    assert event.intent == "research financial records"
    assert mon.events == [event]


def test_anthropic_observe_passes_intent_through():
    from snagline.adapters.anthropic import observe_anthropic_call

    mon = _RecordingMonitor()
    event = observe_anthropic_call(
        mon,
        episode_id="ep",
        model="claude-3-5-sonnet-20241022",
        messages=[{"role": "user", "content": "hi"}],
        intent="research financial records",
    )
    assert event.intent == "research financial records"
    assert mon.events == [event]


def test_sdk_observe_intent_defaults_to_none():
    # Callers that do not declare an intent must be unchanged: the field
    # defaults to None, matching pre-#773 behaviour exactly.
    from snagline.adapters.anthropic import observe_anthropic_call
    from snagline.adapters.openai import observe_openai_call

    mon = _RecordingMonitor()
    openai_event = observe_openai_call(
        mon,
        episode_id="ep",
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
    )
    anthropic_event = observe_anthropic_call(
        mon,
        episode_id="ep",
        model="claude-3-5-sonnet-20241022",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert openai_event.intent is None
    assert anthropic_event.intent is None
