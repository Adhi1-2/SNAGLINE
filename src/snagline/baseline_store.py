"""Versioned, per-tenant baseline store (ATTACH_ANY_SYSTEM P1, item 6).

The `goal_drift` detector compares live traffic against a *healthy* reference
profile. In production that reference must be (a) scoped per tenant/deployment
(because "healthy" latency differs across customers and environments) and
(b) versioned, so a retrained baseline can be rolled back if it regresses.

This module is the storage layer: a file-backed ``BaselineStore`` that keeps
the latest profile plus a bounded history of past versions under a root
directory. Heavier backends (DB, object store) can implement the same shape
later; the core stays stdlib-only.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import IO

from snagline.baseline import BaselineProfile, fit_baseline_from_jsonl

# A version id becomes a filename (``<version>.json``), so it must not carry
# a path separator or traversal component. Anything outside this set -- most
# importantly ``/`` and ``\`` -- would let ``save(version=...)`` write outside
# the store's versions directory (or crash mid-write when the nested parent
# does not exist). The default id ``f"{time.time():.6f}"`` matches this set.
_SAFE_VERSION = re.compile(r"\A[A-Za-z0-9._-]+\Z")


def _validate_version(version: str) -> str:
    if version in (".", "..") or not _SAFE_VERSION.match(version):
        raise ValueError(
            "baseline version id must match [A-Za-z0-9._-]+ and not be "
            f"'.' or '..'; got {version!r} (it is used as an on-disk filename)"
        )
    return version


def _write_json(stream: IO[str], data: dict) -> None:
    json.dump(data, stream, indent=2, sort_keys=True)
    stream.write("\n")


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write ``data`` as JSON so readers see either the old or new file.

    The payload lands in a sibling temp file first, is flushed and fsynced,
    then moved into place with ``os.replace`` (atomic on POSIX and Windows).
    A crash mid-write can therefore never leave a torn or half-written JSON
    file behind; issue #102 relies on this for the version bump.
    """
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        _write_json(fh, data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class BaselineStore:
    """File-backed, versioned baseline store keyed by (tenant, deployment)."""

    def __init__(self, root_dir: str, max_versions: int = 10) -> None:
        self._root = Path(root_dir)
        self._max_versions = max_versions

    # --- paths ---------------------------------------------------------------
    def _scope_dir(self, tenant: str, deployment: str) -> Path:
        return self._root / tenant / deployment

    def _version_path(self, tenant: str, deployment: str, version: str) -> Path:
        return self._scope_dir(tenant, deployment) / "versions" / f"{version}.json"

    # --- write ---------------------------------------------------------------
    def save(
        self,
        profile: BaselineProfile,
        tenant: str = "default",
        deployment: str = "default",
        version: str | None = None,
        max_versions: int | None = None,
    ) -> str:
        """Persist ``profile`` and return the version id used.

        Writes both a timestamped history entry and a ``latest.json`` pointer.
        Both files are written atomically (temp + fsync + rename): the history
        entry first, then the pointer flip, so a reader of ``latest.json``
        always sees one complete profile, never a partial write. Old versions
        beyond ``max_versions`` (this call, else the store default) are pruned
        oldest-first.
        """
        version = (
            _validate_version(version) if version is not None else f"{time.time():.6f}"
        )
        scope = self._scope_dir(tenant, deployment)
        versions_dir = scope / "versions"
        versions_dir.mkdir(parents=True, exist_ok=True)

        # History entry first: durable even if the process dies before the
        # pointer flip, in which case latest.json still resolves to the
        # previous complete version (issue #102).
        _atomic_write_json(
            self._version_path(tenant, deployment, version), profile.to_dict()
        )
        # Atomic pointer flip to the new version.
        _atomic_write_json(scope / "latest.json", profile.to_dict())

        limit = max_versions if max_versions is not None else self._max_versions
        self._prune(tenant, deployment, limit, keep=version)
        return version

    def _version_paths_by_write_time(self, versions_dir: Path) -> list[Path]:
        """Version files oldest-first by write time.

        Retention prunes the oldest *write*, but a lexicographic name sort only
        matches write order for fixed-width ids like the default timestamp.
        Custom ids ("9", "10", "11") sort "10" < "9", so a name sort would
        prune the newest, and at ``max_versions=1`` delete the entry just
        written. Sorting by ``st_mtime_ns`` orders by write time for any id
        shape: an *integer* nanosecond stamp, so (unlike the float ``st_mtime``)
        it does not lose sub-microsecond resolution at epoch scale, and distinct
        writes stay distinguishable on every filesystem with sub-second mtime
        granularity (all mainstream ones).

        The ``name`` tiebreak only decides order for files sharing one mtime
        tick -- possible on coarse-granularity filesystems (FAT, HFS+, ext3) --
        and there it degrades to the lexicographic order this method exists to
        avoid. That residual cannot mis-order the write just made, though:
        ``_prune`` is told which version was just written and never deletes it
        (so ``latest.json`` is never orphaned), and the default timestamp id is
        unaffected regardless.
        """
        return sorted(
            versions_dir.glob("*.json"),
            key=lambda p: (p.stat().st_mtime_ns, p.name),
        )

    def _prune(
        self, tenant: str, deployment: str, limit: int, keep: str | None = None
    ) -> None:
        """Delete oldest-written versions beyond ``limit``.

        ``keep`` (the version just written) is never deleted, so even when a
        coarse-mtime tiebreak mis-orders same-tick writes the newest version --
        the one ``latest.json`` points at -- always survives.
        """
        versions_dir = self._scope_dir(tenant, deployment) / "versions"
        if not versions_dir.exists():
            return
        existing = self._version_paths_by_write_time(versions_dir)
        keep_name = f"{keep}.json" if keep is not None else None
        to_delete = max(0, len(existing) - limit)
        deleted = 0
        for path in existing:
            if deleted >= to_delete:
                break
            if path.name == keep_name:
                continue
            path.unlink(missing_ok=True)
            deleted += 1

    # --- read ----------------------------------------------------------------
    def load(
        self, tenant: str = "default", deployment: str = "default"
    ) -> BaselineProfile | None:
        """Return the latest profile, or None if nothing has been stored."""
        latest = self._scope_dir(tenant, deployment) / "latest.json"
        if not latest.exists():
            return None
        return BaselineProfile.from_dict(json.loads(latest.read_text(encoding="utf-8")))

    def load_version(
        self, tenant: str, deployment: str, version: str
    ) -> BaselineProfile | None:
        path = self._version_path(tenant, deployment, version)
        if not path.exists():
            return None
        return BaselineProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_versions(
        self, tenant: str = "default", deployment: str = "default"
    ) -> list[str]:
        versions_dir = self._scope_dir(tenant, deployment) / "versions"
        if not versions_dir.exists():
            return []
        return [p.name[:-5] for p in self._version_paths_by_write_time(versions_dir)]


def capture_from_jsonl(
    store: BaselineStore,
    trajectory_path: str,
    tenant: str = "default",
    deployment: str = "default",
    version: str | None = None,
    max_versions: int | None = None,
) -> str:
    """Fit a baseline from a healthy-run JSONL trajectory and store it.

    Returns the stored version id. ``max_versions`` overrides the store's
    retention for this write.
    """
    return store.save(
        fit_baseline_from_jsonl(trajectory_path),
        tenant=tenant,
        deployment=deployment,
        version=version,
        max_versions=max_versions,
    )


def retrain_from_jsonl(
    store: BaselineStore,
    window_path: str,
    tenant: str = "default",
    deployment: str = "default",
    max_versions: int | None = None,
) -> str:
    """Refit a baseline from a JSONL window and atomically bump the store.

    This is the library-side primitive behind ``snagline baseline retrain``
    (issue #102): fit a fresh ``BaselineProfile`` from the newest healthy-run
    window, then persist it as a new timestamped version whose pointer flip is
    atomic (see ``BaselineStore.save``). Returns the new version id; the
    previous version stays loadable for rollback.
    """
    return store.save(
        fit_baseline_from_jsonl(window_path),
        tenant=tenant,
        deployment=deployment,
        max_versions=max_versions,
    )


class BaselineCollector:
    """Live auto-capture building block for P1 item 6.

    A host feeds it every ``StepEvent`` during a *known-healthy* run; whenever
    it decides the run is a good reference (a cadence it owns -- e.g. nightly,
    or after N steps), it calls ``commit()`` to persist a versioned baseline.

    Fail-open: a ``commit`` with no store configured is a no-op rather than an
    error, so the collector is safe to drop into any pipeline.
    """

    def __init__(
        self,
        store: BaselineStore | None = None,
        tenant: str = "default",
        deployment: str = "default",
        max_versions: int | None = None,
    ) -> None:
        self._store = store
        self._tenant = tenant
        self._deployment = deployment
        self._max_versions = max_versions
        self._profile = BaselineProfile()

    def observe(self, event) -> None:
        self._profile.add_event(event)

    def snapshot(self) -> BaselineProfile:
        return self._profile

    def commit(self, version: str | None = None) -> str | None:
        if self._store is None:
            return None
        # Record fit time so --max-age works even with custom version ids.
        self._profile.fitted_at = time.time()
        return self._store.save(
            self._profile,
            tenant=self._tenant,
            deployment=self._deployment,
            version=version,
            max_versions=self._max_versions,
        )
