"""Guard against future SHIPPED_TRIGGERS drift (issue #181).

Enumerates every trigger string the codebase can emit and asserts it
appears in SHIPPED_TRIGGERS or an explicit INTENTIONALLY_UNGATED list.
Otherwise a new detector would silently stay outside the honesty gate.
"""

from __future__ import annotations

import re
import typing
from pathlib import Path

from benchmarks.detection_accuracy import INTENTIONALLY_UNGATED, SHIPPED_TRIGGERS

from snagline.risk import TriggerType


def _collect_emitted_triggers() -> set[str]:
    """Scan src/snagline for trigger strings that reach FailureRisk."""
    root = Path("src/snagline")
    # Collect from risk.py Literal and from detectors via TRIGGER_ constants
    # and direct trigger="..." assignments.
    triggers: set[str] = set()
    # Pattern for trigger strings in detectors: "loop", "cycle", etc., as
    # trigger arguments to FailureRisk or TRIGGER_* constants.
    text_pat = re.compile(r'trigger\s*=\s*["\']([^"\']+)["\']')
    # TRIGGER_* constants: the literal form (issue #304) and the historical
    # cast(TriggerType, ...) form, so a revert to casting is still caught.
    const_pat = re.compile(
        r'TRIGGER_[A-Z_]+\s*(?::\s*TriggerType\s*)?=\s*(?:cast\(TriggerType,\s*)?["\']([^"\']+)["\']'
    )
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        # TRIGGER_* constants
        for m in const_pat.finditer(text):
            triggers.add(m.group(1))
        # Direct trigger="..." in FailureRisk
        for m in text_pat.finditer(text):
            # Filter to known detector trigger style (lowercase, underscores)
            val = m.group(1)
            if re.fullmatch(r"[a-z_]+", val):
                triggers.add(val)
    # The TriggerType literal is the closed set of trigger strings that can
    # reach FailureRisk, so read it directly rather than regex-scanning
    # risk.py (a regex silently missed the loop-hardening / side-effect
    # triggers for as long as they were cast()d in, issue #304).
    triggers.update(typing.get_args(TriggerType))
    # Normalize meltdown to its label-space split, as harness does.
    if "meltdown" in triggers:
        triggers.remove("meltdown")
        triggers.update({"meltdown_low", "meltdown_high"})
    return triggers


def test_all_emitted_triggers_are_gated_or_explicitly_ungated():
    emitted = _collect_emitted_triggers()
    allowed = set(SHIPPED_TRIGGERS) | set(INTENTIONALLY_UNGATED)
    missing = emitted - allowed
    assert not missing, (
        f"Newly emitted triggers not in SHIPPED_TRIGGERS or INTENTIONALLY_UNGATED: {sorted(missing)}; "
        "add them to SHIPPED_TRIGGERS or document why they are intentionally ungated"
    )
    # Also ensure SHIPPED entries are not stale (no trigger that no longer exists)
    extra = set(SHIPPED_TRIGGERS) - emitted
    assert not extra, f"SHIPPED_TRIGGERS contains unknown triggers: {sorted(extra)}"


def test_shipped_triggers_covers_all_label_fixtures():
    """Every labeled fixture's trigger must be in SHIPPED_TRIGGERS."""
    from benchmarks.detection_accuracy import iter_fixtures

    fixtures_dir = Path("benchmarks/fixtures")
    for ep in iter_fixtures(fixtures_dir):
        if ep.label_triggers is not None:
            for trig in ep.label_triggers:
                assert trig in SHIPPED_TRIGGERS, (
                    f"fixture {ep.episode_id} label {trig!r} not in SHIPPED_TRIGGERS"
                )
