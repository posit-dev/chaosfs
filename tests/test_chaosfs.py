#!/usr/bin/env python3
"""Regression tests for the local chaosfs behavior."""

from __future__ import annotations

import errno
import os
import tempfile
import time
import unittest
from pathlib import Path

from chaosfs.filesystem import ChaosFS, FuseOSError


class ChaosFSTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _new_fs(
        self, *, meta_ttl: float = 0.1, write_delay: float = 0.0, rename_delay: float = 0.0
    ) -> ChaosFS:
        return ChaosFS(
            root=str(self.root),
            meta_ttl=meta_ttl,
            write_delay=write_delay,
            rename_delay=rename_delay,
            drop_prob=0.0,
            seed=42,
            client_id="test-client",
        )

    def test_readdir_uses_client_cache_without_crashing(self) -> None:
        (self.root / "demo").mkdir()
        (self.root / "demo" / "first.txt").write_text("first", encoding="utf-8")
        fs = self._new_fs(meta_ttl=0.2)

        first_listing = fs.readdir("/demo", None)
        self.assertIn("first.txt", first_listing)

        (self.root / "demo" / "second.txt").write_text("second", encoding="utf-8")
        stale_listing = fs.readdir("/demo", None)
        self.assertNotIn("second.txt", stale_listing)

        time.sleep(0.25)
        refreshed_listing = fs.readdir("/demo", None)
        self.assertIn("second.txt", refreshed_listing)

    def test_rename_delay_keeps_old_path_visible_temporarily(self) -> None:
        (self.root / "demo").mkdir()
        (self.root / "demo" / "old.txt").write_text("payload", encoding="utf-8")
        fs = self._new_fs(rename_delay=0.2)

        fs.getattr("/demo/old.txt")
        fs.rename("/demo/old.txt", "/demo/new.txt")

        still_visible = fs.getattr("/demo/old.txt")
        self.assertGreater(still_visible["st_size"], 0)

        time.sleep(0.25)
        with self.assertRaises(FuseOSError) as context:
            fs.getattr("/demo/old.txt")
        self.assertEqual(context.exception.errno, errno.ENOENT)

    def test_write_delay_returns_stale_read_until_release(self) -> None:
        target = self.root / "demo.txt"
        target.write_text("old", encoding="utf-8")
        fs = self._new_fs(write_delay=0.2)
        fd = os.open(target, os.O_RDWR)
        self.addCleanup(lambda: os.close(fd))

        stale_read = fs.read("/demo.txt", 128, 0, fd).decode("utf-8")
        self.assertEqual(stale_read, "old")

        fs.write("/demo.txt", b"new", 0, fd)
        during_delay = fs.read("/demo.txt", 128, 0, fd).decode("utf-8")
        self.assertEqual(during_delay, "old")

        time.sleep(0.25)
        after_delay = fs.read("/demo.txt", 128, 0, fd).decode("utf-8")
        self.assertEqual(after_delay[:3], "new")


if __name__ == "__main__":
    unittest.main()
