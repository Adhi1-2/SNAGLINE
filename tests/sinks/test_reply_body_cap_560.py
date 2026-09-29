"""Issue #560: the network sinks read an uncapped reply body into memory.

``bounded_post`` documents its ``max_bytes`` argument as the guard that stops
"a malicious or broken endpoint from making the exchange unbounded by
streaming an endless body", but all three network sinks -- and the
``snagline hook --url`` forward -- passed nothing, so the cap never applied.
The sinks discard the reply, so the whole body was buffered only to be thrown
away. The wall-clock deadline bounds *time*, not *memory*: a fast endless or
chunked stream allocates without bound well inside a 2 s budget, and the URL
is operator-supplied, so a misconfigured or hostile escalation endpoint was
driving allocation in the monitor's own process.

The halt webhook already passed its own cap (``_MAX_HALT_RESPONSE_BYTES``);
the sinks are the outlier. The fix shares one ceiling
(``_MAX_SINK_RESPONSE_BYTES``) across every sink call site.
"""

from __future__ import annotations

import threading
import time
import tracemalloc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Literal
from unittest import mock

import pytest

from snagline.risk import FailureRisk
from snagline.sinks.base import _MAX_SINK_RESPONSE_BYTES
from snagline.sinks.pagerduty import PagerDutySink
from snagline.sinks.slack import SlackSink
from snagline.sinks.webhook import WebhookSink

# Big enough that an uncapped read is unmistakable in tracemalloc, small
# enough to cross loopback without slowing the suite.
_BIG_BODY = b"x" * (32 * 1024 * 1024)


def _risk() -> FailureRisk:
    return FailureRisk(
        episode_id="ep-1",
        step_id="3",
        score=0.9,
        trigger="loop",
        detail="action repeated 3x in last 4 steps",
        timestamp=1718300000.0,
    )


class _BigBodyHandler(BaseHTTPRequestHandler):
    """Answer every POST with a body far past the cap."""

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        self.send_response(200)
        self.send_header("Content-Length", str(len(_BIG_BODY)))
        self.end_headers()
        self.wfile.write(_BIG_BODY)

    def log_message(self, *args: object) -> None:
        pass  # the server-side broken pipe from the early close is expected


@pytest.fixture(scope="module")
def big_body_server() -> object:
    """A local server whose replies dwarf the cap."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _BigBodyHandler)
    srv.daemon_threads = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.3)
    yield srv  # type: ignore[misc]
    srv.shutdown()
    srv.server_close()


def test_webhook_emit_does_not_buffer_the_reply_body(big_body_server: object) -> None:
    """The behavioural claim: one emit must not allocate the whole reply."""
    port = big_body_server.server_port  # type: ignore[attr-defined]
    sink = WebhookSink(url=f"http://127.0.0.1:{port}/hook", timeout=5.0)
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        sink.emit(_risk())
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    # The reply is 32 MiB; the cap holds peak allocation to a handful of KiB.
    # A generous bar keeps slow CI green while still failing the old behaviour
    # by three orders of magnitude.
    assert peak - before < 1024 * 1024, (
        f"emit buffered {(peak - before) / 1024 / 1024:.1f} MiB of a reply it discards"
    )


class _RecordingResponse:
    """A fake response whose ``read`` records the cap it was handed."""

    def __init__(self) -> None:
        self.read_args: list[int | None] = []

    def __enter__(self) -> _RecordingResponse:
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        return False

    def read(self, max_bytes: int | None = None) -> bytes:
        self.read_args.append(max_bytes)
        return b""


@pytest.mark.parametrize(
    "sink",
    [
        lambda: WebhookSink(url="http://hooks.example/alerts"),
        lambda: SlackSink(webhook_url="http://hooks.example/slack"),
        lambda: PagerDutySink(routing_key="deadbeef"),
    ],
)
def test_every_network_sink_caps_the_reply_read(sink) -> None:
    """No sink may hand ``bounded_post`` an unbounded read."""
    response = _RecordingResponse()
    with mock.patch("snagline.sinks.base._opener.open", return_value=response):
        sink().emit(_risk())
    assert response.read_args, "the reply was never read"
    caps = [arg for arg in response.read_args if arg is not None]
    assert len(caps) == len(response.read_args), (
        "a sink read its reply with no byte cap at all"
    )
    assert all(cap <= _MAX_SINK_RESPONSE_BYTES for cap in caps)


def test_snagline_hook_forward_caps_the_reply_read(monkeypatch) -> None:
    """The CLI's ``hook --url`` forward shares the sink contract."""
    import argparse
    import io

    from snagline.cli import _cmd_hook

    response = _RecordingResponse()
    payload = (
        '{"step_id":"1","episode_id":"ep","timestamp":1.0,'
        '"action_type":"tool_call","action_signature":"s","tool_name":"t"}'
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    args = argparse.Namespace(
        url="http://hooks.example/forward",
        out=None,
        timeout=1.0,
    )
    with mock.patch("snagline.sinks.base._opener.open", return_value=response):
        _cmd_hook(args)
    assert response.read_args and response.read_args[0] is not None, (
        "the hook forwarded the event and read the reply with no byte cap"
    )
