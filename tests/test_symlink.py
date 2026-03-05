"""Regression tests for ChaosFS symlink operations.

The FUSE symlink() callback receives parameters in a non-obvious order:
  symlink(target, source)  where target=link path, source=what it points to.
This is the reverse of os.symlink(src, dst).  A previous bug swapped these,
so these tests exist to prevent regression.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from chaosfs.filesystem import ChaosFS


class SymlinkTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.fs = ChaosFS(
            root=str(self.root),
            meta_ttl=0,
            write_delay=0,
            rename_delay=0,
            drop_prob=0.0,
            seed=42,
            client_id="test",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_symlink_creates_link_at_correct_path(self) -> None:
        """Symlink is created where FUSE's 'target' says, pointing to 'source'."""
        (self.root / "real_file").write_text("hello")

        # FUSE: symlink(link_path, points_to)
        self.fs.symlink("/mylink", "real_file")

        link = self.root / "mylink"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "real_file")

    def test_symlink_relative(self) -> None:
        (self.root / "subdir").mkdir()
        (self.root / "subdir" / "lib").mkdir()

        self.fs.symlink("/subdir/lib64", "lib")

        link = self.root / "subdir" / "lib64"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "lib")

    def test_symlink_dangling(self) -> None:
        """A symlink to a non-existent target is valid (dangling link)."""
        self.fs.symlink("/dangling", "no_such_file")

        link = self.root / "dangling"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "no_such_file")

    def test_readlink_roundtrip(self) -> None:
        self.fs.symlink("/mylink", "target_value")
        self.assertEqual(self.fs.readlink("/mylink"), "target_value")


if __name__ == "__main__":
    unittest.main()
