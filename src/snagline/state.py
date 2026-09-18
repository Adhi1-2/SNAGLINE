"""Pluggable state backend (ATTACH_ANY_SYSTEM P1, item 4).

The Monitor's per-episode detector state (loop windows, cascade counters,
CUSUM baselines) is naturally keyed by ``episode_id``. This module provides a
``StateBackend`` that owns the concurrency primitive, so the ingest path can
shard its lock by episode instead of serializing all episodes behind one
global lock (the verified bottleneck in monitor.py).

The default ``MemoryStateBackend`` is process-local. ``RedisStateBackend``
(optional, behind the ``redis`` extra) provides a shared lock across workers
so a horizontally-scaled deployment does not double-count; it is imported
lazily so the core stays zero-dependency.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Literal, Protocol

logger = logging.getLogger("snagline")


class StateBackend(Protocol):
    """Owns the lock used to serialize a single episode's ingest path.

    ``episode_lock`` returns any context manager, not specifically a
    generator: the memory backend measures ~0.34 us/step cheaper as a plain
    object (issue #299), and a caller only ever enters and exits. A backend
    that prefers to ``yield`` still type-checks, because
    ``contextlib._GeneratorContextManager`` satisfies this shape.
    """

    def episode_lock(self, episode_id: str) -> AbstractContextManager[None]:
        """Hold the lock for ``episode_id`` for the duration of a ``with``."""
        ...


class ReleasableStateBackend(StateBackend, Protocol):
    """A ``StateBackend`` that can drop what it holds for a finished episode.

    Optional capability: ``Monitor.end_episode`` probes for ``release`` and
    skips backends that do not expose it, so a backend written against the
    narrower ``StateBackend`` above keeps working unchanged.
    """

    def release(self, episode_id: str) -> None:
        """Discard any per-episode state held for ``episode_id``."""
        ...


class _LockEntry:
    """One episode's lock plus the bookkeeping that keeps it safe to drop.

    ``waiters`` counts threads that have fetched this entry -- holding its
    lock or parked waiting for it. ``released`` marks an episode whose lock
    the backend has been asked to drop. The entry leaves the table only
    once every waiter has drained, so a thread parked on the lock when
    ``release()`` ran can never end up inside the critical section beside
    a fetcher that arrived afterwards: both serialize on this same entry
    until the last one exits and the entry is finally removed (issue #238).
    """

    __slots__ = ("lock", "waiters", "released")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.waiters: int = 0
        self.released: bool = False


class _HeldLock(AbstractContextManager[None]):
    """The live half of ``MemoryStateBackend.episode_lock`` (issue #299).

    ``episode_lock`` used to be a ``@contextmanager``, so every ingest paid
    for a generator frame -- create, resume into the ``with``, throw on exit
    -- on top of the locks themselves. Measured, that machinery alone was
    ~0.34 us of the ~1.9 us default per-step budget, roughly a fifth of it,
    and it bought nothing: the caller only ever enters and exits.

    Splitting the protocol's context manager into a cheap object avoids the
    generator entirely while keeping ``with backend.episode_lock(eid):`` and
    the issue #238 waiter semantics byte-for-byte. The entry is resolved and
    the waiter counted eagerly, in ``episode_lock`` itself, so a waiter that
    blocks on the RLock is already accounted for before it parks -- that is
    what makes ``release()`` safe against a thread parked mid-acquire.
    """

    __slots__ = ("_backend", "_entry", "_episode_id")

    def __init__(
        self, backend: MemoryStateBackend, entry: _LockEntry, episode_id: str
    ) -> None:
        self._backend = backend
        self._entry = entry
        self._episode_id = episode_id

    def __enter__(self) -> None:
        self._entry.lock.acquire()

    def __exit__(self, *exc_info: object) -> Literal[False]:
        self._entry.lock.release()
        # Mirror the generator's original finally block: this entry is only
        # safe to forget once nothing is left parked on it. Fetched under
        # _meta so a concurrent release() cannot observe a half-decremented
        # waiter count. The lookup can come back None if release() already
        # drained this entry -- the waiter count then has nothing left to
        # adjust, and there is nothing to pop.
        backend = self._backend
        with backend._meta:
            entry = backend._locks.get(self._episode_id)
            if entry is None:
                return False
            entry.waiters -= 1
            if entry.released and entry.waiters == 0:
                backend._locks.pop(self._episode_id, None)
        return False


class MemoryStateBackend:
    """Process-local backend: one re-entrant lock per episode id."""

    def __init__(self) -> None:
        self._meta = threading.Lock()
        self._locks: dict[str, _LockEntry] = {}

    def episode_lock(self, episode_id: str) -> _HeldLock:
        """A context manager holding the lock for ``episode_id``.

        Returns a cheap object rather than yielding from a generator: the
        generator frame was ~0.34 us of every ingest, about a fifth of the
        default per-step budget, and the caller only ever enters and exits
        (issue #299). Resolving the entry and counting this caller as a
        waiter under ``_meta`` before the RLock is acquired is what makes a
        concurrent ``release()`` safe against a waiter parked mid-acquire.
        """
        with self._meta:
            entry = self._locks.get(episode_id)
            if entry is None:
                entry = _LockEntry()
                self._locks[episode_id] = entry
            entry.waiters += 1
        return _HeldLock(self, entry, episode_id)

    def release(self, episode_id: str) -> None:
        """Drop the lock allocated for a finished episode.

        Without this the dict grows by one entry per episode id and never
        shrinks, so a long-lived Monitor watching many short runs retains a
        lock for every episode it has ever seen.

        ``Monitor.end_episode`` calls this while holding the episode lock.
        Other threads may still be *parked* on that lock -- they fetched it
        before the release and are waiting for the holder to finish -- so
        the entry is only removed once every such waiter has drained
        (issue #238). Until then a parked waiter and any fetcher that
        arrives later share this same entry, keeping the episode's critical
        section mutually exclusive; a thread that fetches after the entry
        is finally removed allocates a fresh one, which is correct because
        an ended episode has no detector state left to serialize.
        """
        with self._meta:
            entry = self._locks.get(episode_id)
            if entry is None:
                return
            entry.released = True
            if entry.waiters == 0:
                self._locks.pop(episode_id, None)


class RedisStateBackend:  # pragma: no cover - optional, requires redis
    """Shared backend: a Redis lock so workers coordinate across processes."""

    def __init__(self, url: str, prefix: str = "snagline:") -> None:
        import redis

        self._r = redis.Redis.from_url(url)
        self._prefix = prefix

    @contextmanager
    def episode_lock(self, episode_id: str) -> Iterator[None]:
        lock = self._r.lock(self._prefix + "lock:" + episode_id, timeout=30)
        acquired = lock.acquire(blocking=True, blocking_timeout=30)
        try:
            yield
        finally:
            if acquired:
                lock.release()

    def release(self, episode_id: str) -> None:
        """No-op: Redis locks are per-acquisition and expire on their own.

        Nothing is retained between ``episode_lock`` calls, so a finished
        episode leaves nothing behind to discard.
        """


def default_state_backend() -> StateBackend:
    """Pick a backend from ``SNAGLINE_STATE_BACKEND`` env (memory|redis)."""
    kind = os.environ.get("SNAGLINE_STATE_BACKEND", "memory").lower()
    if kind == "redis":
        url = os.environ.get("SNAGLINE_STATE_REDIS_URL")
        if url:
            try:
                return RedisStateBackend(url)
            except ImportError:
                logger.warning(
                    "snagline: redis backend requested but redis not installed; "
                    "falling back to in-memory state"
                )
    return MemoryStateBackend()
