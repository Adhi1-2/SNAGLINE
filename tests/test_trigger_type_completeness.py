"""TriggerType is the closed set of strings detectors actually emit (issue #304).

``FailureRisk.trigger`` is annotated ``TriggerType``, and the trigger names are
API -- CONTINUUM's risk-policy proposal maps them to recovery modes by string.
Before #304, four shipped detectors emitted strings that were not in the
literal and reached ``FailureRisk`` through ``cast(TriggerType, "...")``
workarounds, so a downstream type checker could not see a quarter of the real
trigger space. This test makes that gap a CI failure instead of a silent
deferral: every trigger string emitted in ``src/`` must be in the literal, and
no emission site may need a ``cast`` to type-check.
"""

from __future__ import annotations

import ast
import typing
from pathlib import Path

import pytest

from snagline.risk import TriggerType

REPO_SRC = Path(__file__).resolve().parent.parent / "src"

# Module-level trigger constants named TRIGGER_* in detectors. These were the
# four cast() sites before #304; they are now plain annotated literals, and
# this test pins that.
EMITTED_TRIGGER_CONSTANTS = {
    ("snagline/detectors/loop.py", "TRIGGER_NEAR_DUPLICATE_LOOP"),
    ("snagline/detectors/loop.py", "TRIGGER_CYCLE"),
    ("snagline/detectors/loop.py", "TRIGGER_STALL"),
    ("snagline/detectors/side_effect_guard.py", "TRIGGER_SIDE_EFFECT_DUPLICATE"),
}


def _literal_members() -> set[str]:
    return set(typing.get_args(TriggerType))


def test_trigger_literal_is_not_empty() -> None:
    assert _literal_members(), "TriggerType has no members at all"


@pytest.mark.parametrize(
    ("relpath", "const"),
    sorted(EMITTED_TRIGGER_CONSTANTS),
    ids=lambda v: str(v),
)
def test_emitted_trigger_is_in_the_literal(relpath: str, const: str) -> None:
    """Each detector-emitted trigger must be a first-class TriggerType member."""
    path = REPO_SRC / relpath
    tree = ast.parse(path.read_text(encoding="utf-8"))
    value = next(
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == const
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )
    assert value in _literal_members(), (
        f"{relpath}::{const} emits trigger {value!r}, which is not in the "
        "TriggerType literal in risk.py -- add it there (the trigger name is "
        "API) rather than casting it"
    )


def test_no_src_file_casts_to_trigger_type() -> None:
    """A ``cast(TriggerType, ...)`` is the smell #304 removed.

    With the literal widened, the casts are unnecessary: if one reappears it
    means a trigger is being emitted that the literal does not admit.
    """
    offenders: list[str] = []
    for py in REPO_SRC.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name != "cast":
                continue
            first = node.args[0] if node.args else None
            target = first.id if isinstance(first, ast.Name) else None
            if target == "TriggerType":
                offenders.append(f"{py}:{node.lineno}")
    assert not offenders, (
        "cast(TriggerType, ...) found at " + ", ".join(offenders) + " -- widen "
        "the TriggerType literal in risk.py instead of casting around it"
    )
