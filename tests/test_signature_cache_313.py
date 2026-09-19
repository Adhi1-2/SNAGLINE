"""Regression tests for issue #313: make_signature memoization.

The cache is a pure-function memoization -- it must never change the digest
produced for a given argument tuple, it must be bounded so a hostile
high-cardinality stream cannot grow it without limit, and a cache miss must
still return the canonical-JSON digest (i.e. the wire format is untouched).
"""

from __future__ import annotations

import json
import random

import pytest

from snagline.events import _clear_signature_cache, _make_signature, make_signature


@pytest.fixture(autouse=True)
def _clean_signature_cache():
    # Each test starts and ends with an empty cache so assertions about hits
    # and misses are not contaminated by ordering across the suite.
    _clear_signature_cache()
    yield
    _clear_signature_cache()


def _canonical_digest(action_type, tool_name, *parts):
    """The pre-memoization reference implementation (issue #15 wire format)."""
    raw = json.dumps(
        [action_type, tool_name or "", *parts],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    import hashlib

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_cached_output_matches_canonical_wire_format():
    # The memo must be byte-identical to the uncached canonical encoding.
    rng = random.Random(1234)
    alphabet = "ab|:cd "
    for _ in range(5000):
        n = rng.randint(0, 4)
        parts = [
            "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 4)))
            for _ in range(n)
        ]
        tool = rng.choice(["bash", None, "read_file"])
        got = make_signature("tool_call", tool, *parts)
        assert got == _canonical_digest("tool_call", tool, *parts)


def test_repeat_call_hits_cache_without_recomputing():
    # A hot signature is served from the cache: the underlying function is not
    # invoked again for the same arguments.
    make_signature("tool_call", "bash", "deploy.sh")
    info_before = _make_signature.cache_info()
    for _ in range(50):
        make_signature("tool_call", "bash", "deploy.sh")
    info_after = _make_signature.cache_info()
    assert info_after.hits - info_before.hits == 50
    assert info_after.misses == info_before.misses  # no extra computations


def test_distinct_signatures_still_miss():
    # Different arguments are not conflated.
    a = make_signature("tool_call", "bash", "one")
    b = make_signature("tool_call", "bash", "two")
    assert a != b
    assert _make_signature.cache_info().misses == 2


def test_cache_is_bounded_under_unbounded_cardinality():
    # Issue #313's stated risk: a stream of unique signatures must not grow the
    # cache without limit. maxsize caps it regardless of input diversity.
    for i in range(5000):
        make_signature("tool_call", "bash", f"unique-{i}")
    info = _make_signature.cache_info()
    assert info.maxsize == 1024
    assert info.currsize <= info.maxsize


def test_none_tool_name_is_stable_across_the_boundary():
    # tool_name=None is normalized inside the function; the cache key must not
    # distinguish it in a way that changes the digest.
    assert make_signature("message", None, "body") == _canonical_digest(
        "message", None, "body"
    )
    assert make_signature("message", None, "body") == make_signature(
        "message", None, "body"
    )


@pytest.mark.parametrize(
    "parts_a,parts_b",
    [
        (("a", "b"), ("a|b",)),  # the ambiguity issue #15 fixed -- still distinct
        (("a",), ("a", "")),
        ((), ("",)),
    ],
)
def test_ambiguity_cases_remain_distinct_when_cached(parts_a, parts_b):
    # The JSON-array guarantee survives memoization.
    x = make_signature("tool_call", "t", *parts_a)
    y = make_signature("tool_call", "t", *parts_b)
    assert x != y
