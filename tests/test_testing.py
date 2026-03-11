#!/usr/bin/env python3
"""Tests for the chaosfs testing API with actual FUSE mounts."""

from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

# Check if fusermount is available for FUSE operations
_has_fusermount = shutil.which("fusermount") or shutil.which("fusermount3")


@unittest.skipUnless(_has_fusermount, "fusermount not available")
class TestMount(unittest.TestCase):
    """Tests for the mount() context manager."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_mount_creates_live_filesystem(self) -> None:
        """Create backing dir with a file, mount it, verify the file is readable through mount point."""
        from chaosfs import mount

        # Set up backing directory with test file
        backing_dir = self.root / "backing"
        backing_dir.mkdir()
        test_file = backing_dir / "test.txt"
        test_file.write_text("hello world", encoding="utf-8")

        mount_point = self.root / "mount"
        mount_point.mkdir()

        # Mount and verify file is accessible
        with mount(
            backing_dir=str(backing_dir),
            mount_point=str(mount_point),
            client_id="test-client",
            meta_ttl=0.5,
        ) as mp:
            # Mount point should be a Path object
            self.assertIsInstance(mp, Path)
            self.assertEqual(mp, mount_point)

            # Check mount is active
            self.assertTrue(mount_point.is_mount())

            # File should be readable through mount
            mounted_file = mount_point / "test.txt"
            self.assertTrue(mounted_file.exists())
            content = mounted_file.read_text(encoding="utf-8")
            self.assertEqual(content, "hello world")

        # After context exit, mount should be cleaned up
        self.assertFalse(mount_point.is_mount())

    def test_mount_cleanup_on_exception(self) -> None:
        """Mount, raise exception inside context, verify mount is cleaned up."""
        from chaosfs import mount

        backing_dir = self.root / "backing"
        backing_dir.mkdir()
        mount_point = self.root / "mount"
        mount_point.mkdir()

        with self.assertRaises(ValueError):
            with mount(
                backing_dir=str(backing_dir),
                mount_point=str(mount_point),
                client_id="test-client",
                meta_ttl=0.5,
            ):
                # Verify mount is active
                self.assertTrue(mount_point.is_mount())
                # Raise exception
                raise ValueError("test exception")

        # Mount should be cleaned up despite exception
        self.assertFalse(mount_point.is_mount())

    def test_mount_write_through(self) -> None:
        """Mount, write a file through mount point, verify it appears in backing dir."""
        from chaosfs import mount

        backing_dir = self.root / "backing"
        backing_dir.mkdir()
        mount_point = self.root / "mount"
        mount_point.mkdir()

        with mount(
            backing_dir=str(backing_dir),
            mount_point=str(mount_point),
            client_id="test-client",
            meta_ttl=0.5,
            write_delay=0.1,
        ):
            # Write file through mount point
            mounted_file = mount_point / "new_file.txt"
            mounted_file.write_text("test content", encoding="utf-8")

            # Wait for write delay to pass
            time.sleep(0.15)

            # File should appear in backing directory
            backing_file = backing_dir / "new_file.txt"
            self.assertTrue(backing_file.exists())
            content = backing_file.read_text(encoding="utf-8")
            self.assertEqual(content, "test content")


@unittest.skipUnless(_has_fusermount, "fusermount not available")
class TestDualMount(unittest.TestCase):
    """Tests for the dual_mount() context manager."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_dual_mount_sets_up_two_views(self) -> None:
        """Use dual_mount, verify both writer and reader paths are mounted and are directories."""
        from chaosfs import dual_mount

        base_path = self.root / "test_base"
        base_path.mkdir()

        with dual_mount(
            base_path=str(base_path),
            writer_meta_ttl=0.5,
            writer_rename_delay=0.1,
            reader_meta_ttl=0.5,
            reader_rename_delay=0.1,
        ) as (writer_path, reader_path):
            # Both should be Path objects
            self.assertIsInstance(writer_path, Path)
            self.assertIsInstance(reader_path, Path)

            # Both should be directories
            self.assertTrue(writer_path.is_dir())
            self.assertTrue(reader_path.is_dir())

            # Both should be mount points
            self.assertTrue(writer_path.is_mount())
            self.assertTrue(reader_path.is_mount())

            # Paths should be different
            self.assertNotEqual(writer_path, reader_path)

        # After context exit, both mounts should be cleaned up
        self.assertFalse((base_path / "writer").is_mount())
        self.assertFalse((base_path / "reader").is_mount())

    def test_dual_mount_file_eventually_visible(self) -> None:
        """Write a file through writer mount, wait for delays, verify it's readable through reader mount."""
        from chaosfs import dual_mount

        base_path = self.root / "test_base"
        base_path.mkdir()

        with dual_mount(
            base_path=str(base_path),
            writer_meta_ttl=0.5,
            writer_rename_delay=0.1,
            reader_meta_ttl=0.5,
            reader_rename_delay=0.1,
        ) as (writer_path, reader_path):
            # Write file through writer mount
            writer_file = writer_path / "shared.txt"
            writer_file.write_text("shared data", encoding="utf-8")

            # Wait for write delay and metadata TTL to expire
            time.sleep(0.6)

            # File should be visible through reader mount
            reader_file = reader_path / "shared.txt"
            self.assertTrue(reader_file.exists())
            content = reader_file.read_text(encoding="utf-8")
            self.assertEqual(content, "shared data")

    def test_dual_mount_cleanup_on_exception(self) -> None:
        """Like mount cleanup test but for dual_mount - both mounts cleaned up after exception."""
        from chaosfs import dual_mount

        base_path = self.root / "test_base"
        base_path.mkdir()

        with self.assertRaises(RuntimeError):
            with dual_mount(
                base_path=str(base_path),
                writer_meta_ttl=0.5,
                reader_meta_ttl=0.5,
            ) as (writer_path, reader_path):
                # Verify both mounts are active
                self.assertTrue(writer_path.is_mount())
                self.assertTrue(reader_path.is_mount())
                # Raise exception
                raise RuntimeError("test error")

        # Both mounts should be cleaned up despite exception
        self.assertFalse((base_path / "writer").is_mount())
        self.assertFalse((base_path / "reader").is_mount())


if __name__ == "__main__":
    unittest.main()