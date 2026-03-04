"""Core ChaosFS implementation that injects eventual-consistency chaos."""

from __future__ import annotations

import errno
import os
import random
import time
from collections import defaultdict
from typing import Dict, List, Tuple

from fuse import FuseOSError, LoggingMixIn, Operations

CACHE_FIELDS = (
    "st_atime",
    "st_ctime",
    "st_gid",
    "st_ino",
    "st_mode",
    "st_mtime",
    "st_nlink",
    "st_rdev",
    "st_size",
    "st_uid",
)


class ChaosFS(LoggingMixIn, Operations):
    def __init__(
        self,
        root: str,
        meta_ttl: float,
        write_delay: float,
        rename_delay: float,
        drop_prob: float,
        seed: int | None,
        client_id: str,
    ) -> None:
        self.root = os.path.abspath(root)
        self.meta_ttl = max(0.0, meta_ttl)
        self.write_delay = max(0.0, write_delay)
        self.rename_delay = max(0.0, rename_delay)
        self.drop_prob = min(max(0.0, drop_prob), 1.0)
        self.client_id = client_id
        self.rand = random.Random(seed)
        self.stale_stats: Dict[str, dict] = {}
        self.meta_global_release: Dict[str, float] = {}
        self.dir_snapshots: Dict[str, List[str]] = {}
        self.dir_global_release: Dict[str, float] = {}
        self.client_meta_cache: Dict[str, Dict[str, Tuple[dict, float]]] = defaultdict(dict)
        self.client_dir_cache: Dict[str, Dict[str, Tuple[List[str], float]]] = defaultdict(dict)
        self.client_content_release: Dict[str, Dict[str, float]] = defaultdict(dict)
        self.content_snapshots: Dict[str, bytes] = {}

    def getattr(self, path: str, fh=None) -> dict:
        self._maybe_drop()
        now = time.monotonic()
        global_due = self.meta_global_release.get(path, 0.0)
        if global_due:
            if now < global_due:
                cached = self.stale_stats.get(path)
                if cached:
                    return cached
            else:
                self.meta_global_release.pop(path, None)
        cache = self.client_meta_cache[self.client_id].get(path)
        if cache and now < cache[1]:
            return cache[0]
        full = self._full_path(path)
        try:
            st = os.lstat(full)
        except OSError as exc:
            raise FuseOSError(exc.errno)
        attr = self._stat_dict(st)
        expires = now + self.meta_ttl
        self.client_meta_cache[self.client_id][path] = (attr, expires)
        self.stale_stats[path] = attr
        return attr

    def readdir(self, path: str, fh) -> List[str]:
        self._maybe_drop()
        now = time.monotonic()
        global_due = self.dir_global_release.get(path, 0.0)
        if global_due:
            if now < global_due:
                snapshot = self.dir_snapshots.get(path)
                if snapshot:
                    return snapshot
            else:
                self.dir_global_release.pop(path, None)
        cache = self.client_dir_cache[self.client_id].get(path)
        if cache and now < cache[1]:
            return cache[0]
        full = self._full_path(path)
        try:
            children = os.listdir(full)
        except OSError as exc:
            raise FuseOSError(exc.errno)
        listing = [".", ".."] + sorted(children)
        expires = now + self.meta_ttl
        self.client_dir_cache[self.client_id][path] = (listing, expires)
        self.dir_snapshots[path] = listing
        return listing

    def open(self, path: str, flags: int) -> int:
        self._maybe_drop()
        full = self._full_path(path)
        try:
            return os.open(full, flags)
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def create(self, path: str, mode: int, fi=None) -> int:
        self._maybe_drop()
        self._record_meta_staleness(path, self.rename_delay)
        self._record_dir_staleness(os.path.dirname(path) or "/", self.rename_delay)
        full = self._full_path(path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        try:
            fd = os.open(full, flags, mode)
        except OSError as exc:
            raise FuseOSError(exc.errno)
        self._record_content_staleness(path)
        return fd

    def read(self, path: str, size: int, offset: int, fh: int) -> bytes:
        self._maybe_drop()
        full = self._full_path(path)
        self._release_after_delay(self.client_content_release, path)
        release = self._effective_release(self.client_content_release, path)
        now = time.monotonic()
        if release and now < release:
            snapshot = self.content_snapshots.get(path, b"")
            return snapshot[offset : offset + size]
        try:
            with open(full, "rb") as fh_obj:
                data = fh_obj.read()
        except OSError as exc:
            raise FuseOSError(exc.errno)
        self.content_snapshots[path] = data
        return data[offset : offset + size]

    def write(self, path: str, data: bytes, offset: int, fh: int) -> int:
        self._maybe_drop()
        self._record_content_staleness(path)
        os.lseek(fh, offset, os.SEEK_SET)
        try:
            written = os.write(fh, data)
        except OSError as exc:
            raise FuseOSError(exc.errno)
        self._schedule_write_release(path)
        return written

    def truncate(self, path: str, length: int, fh: int | None = None) -> None:
        self._maybe_drop()
        full = self._full_path(path)
        self._record_content_staleness(path)
        try:
            with open(full, "r+b") as handle:
                handle.truncate(length)
        except OSError as exc:
            raise FuseOSError(exc.errno)
        self._schedule_write_release(path)

    def unlink(self, path: str) -> None:
        self._maybe_drop()
        self._record_meta_staleness(path, self.rename_delay)
        self._record_dir_staleness(os.path.dirname(path) or "/", self.rename_delay)
        try:
            os.unlink(self._full_path(path))
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def mkdir(self, path: str, mode: int) -> None:
        self._maybe_drop()
        self._record_dir_staleness(path, self.rename_delay)
        self._record_dir_staleness(os.path.dirname(path) or "/", self.rename_delay)
        try:
            os.mkdir(self._full_path(path), mode)
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def rmdir(self, path: str) -> None:
        self._maybe_drop()
        self._record_dir_staleness(path, self.rename_delay)
        self._record_dir_staleness(os.path.dirname(path) or "/", self.rename_delay)
        try:
            os.rmdir(self._full_path(path))
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def rename(self, old: str, new: str) -> None:
        self._maybe_drop()
        old_stat = self._capture_stat(old)
        self._record_meta_staleness(old, self.rename_delay, stale_override=old_stat)
        self._record_meta_staleness(new, self.rename_delay, stale_override=old_stat)
        self._record_dir_staleness(os.path.dirname(old) or "/", self.rename_delay)
        self._record_dir_staleness(os.path.dirname(new) or "/", self.rename_delay)
        try:
            os.rename(self._full_path(old), self._full_path(new))
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def chmod(self, path: str, mode: int, fh: int | None = None) -> None:
        self._maybe_drop()
        self._record_meta_staleness(path, self.rename_delay)
        try:
            os.chmod(self._full_path(path), mode)
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def chown(self, path: str, uid: int, gid: int, fh: int | None = None) -> None:
        self._maybe_drop()
        self._record_meta_staleness(path, self.rename_delay)
        try:
            os.chown(self._full_path(path), uid, gid)
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def utimens(self, path: str, times: Tuple[float, float]) -> None:
        self._maybe_drop()
        self._record_meta_staleness(path, self.rename_delay)
        try:
            os.utime(self._full_path(path), times)
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def symlink(self, target: str, source: str) -> None:
        self._maybe_drop()
        self._record_meta_staleness(source, self.rename_delay)
        self._record_dir_staleness(os.path.dirname(source) or "/", self.rename_delay)
        try:
            os.symlink(target, self._full_path(source))
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def readlink(self, path: str) -> str:
        self._maybe_drop()
        try:
            return os.readlink(self._full_path(path))
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def flush(self, path: str, fh: int) -> None:
        self._maybe_drop()
        try:
            os.fsync(fh)
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def release(self, path: str, fh: int) -> None:
        self._maybe_drop()
        try:
            os.close(fh)
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def fsync(self, path: str, fdatasync: bool, fh: int) -> None:
        self._maybe_drop()
        try:
            if fdatasync:
                os.fdatasync(fh)
            else:
                os.fsync(fh)
        except OSError as exc:
            raise FuseOSError(exc.errno)

    # Internal helpers --------------------------------------------------
    def _full_path(self, path: str) -> str:
        return os.path.join(self.root, path.lstrip("/"))

    def _stat_dict(self, st: os.stat_result) -> dict:
        return {field: getattr(st, field) for field in CACHE_FIELDS}

    def _maybe_drop(self) -> None:
        if self.drop_prob <= 0:
            return
        if self.rand.random() < self.drop_prob:
            raise FuseOSError(errno.EIO)

    def _effective_release(self, store: Dict[str, Dict[str, float]], path: str) -> float:
        entry = store.get(path)
        if not entry:
            return 0.0
        return max(entry.get(self.client_id, 0.0), entry.get("global", 0.0))

    def _capture_directory(self, path: str) -> Tuple[List[str], float]:
        try:
            entries = [".", ".."] + sorted(os.listdir(self._full_path(path)))
        except OSError:
            entries = [".", ".."]
        return entries, time.monotonic()

    def _record_dir_staleness(self, path: str, delay: float) -> None:
        if delay <= 0:
            return
        entries, _ = self._capture_directory(path)
        due = time.monotonic() + delay
        self.dir_snapshots[path] = entries
        self.dir_global_release[path] = max(self.dir_global_release.get(path, 0.0), due)
        for cache in self.client_dir_cache.values():
            if path in cache:
                cached, expiry = cache[path]
                cache[path] = (cached, max(expiry, due))

    def _capture_stat(self, path: str) -> dict | None:
        try:
            st = os.lstat(self._full_path(path))
        except FileNotFoundError:
            return None
        return self._stat_dict(st)

    def _record_meta_staleness(
        self, path: str, delay: float, stale_override: dict | None = None
    ) -> None:
        if delay <= 0:
            return
        stale = stale_override if stale_override is not None else self._capture_stat(path)
        if stale:
            self.stale_stats[path] = stale
        due = time.monotonic() + delay
        self.meta_global_release[path] = max(self.meta_global_release.get(path, 0.0), due)
        for cache in self.client_meta_cache.values():
            if path in cache:
                cached, expiry = cache[path]
                cache[path] = (cached, max(expiry, due))

    def _record_content_staleness(self, path: str) -> None:
        try:
            with open(self._full_path(path), "rb") as handle:
                self.content_snapshots[path] = handle.read()
        except OSError:
            self.content_snapshots[path] = b""
        if self.write_delay > 0:
            now = time.monotonic()
            release = self.client_content_release.setdefault(path, {})
            release["global"] = max(release.get("global", 0.0), now + self.write_delay)
            release[self.client_id] = now

    def _release_after_delay(self, store: Dict[str, Dict[str, float]], path: str) -> None:
        entry = store.get(path)
        if not entry:
            return
        now = time.monotonic()
        if entry.get("global", 0.0) <= now:
            entry.pop("global", None)
        if entry.get(self.client_id, 0.0) <= now:
            entry.pop(self.client_id, None)
        if not entry:
            store.pop(path, None)

    def _schedule_write_release(self, path: str) -> None:
        if self.write_delay <= 0:
            return
        now = time.monotonic()
        release = self.client_content_release.setdefault(path, {})
        release["global"] = max(release.get("global", 0.0), now + self.write_delay)
        release[self.client_id] = now
