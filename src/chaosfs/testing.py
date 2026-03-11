"""Testing utilities for ChaosFS — context managers for FUSE-mounted test fixtures."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from pathlib import Path
from typing import Iterator

from fuse import FUSE

from .cli import UnmountError, _pick_unmount_command, _run_unmount
from .filesystem import ChaosFS

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def mount(
    backing_dir,
    mount_point,
    client_id="client",
    meta_ttl=1.5,
    write_delay=0.8,
    rename_delay=1.2,
    drop_prob=0.0,
    seed=None,
) -> Iterator[Path]:
    """Provide a live ChaosFS mount for testing, automatically unmounted on exit."""
    backing_path = Path(backing_dir).resolve()
    mount_path = Path(mount_point).resolve()

    chaos = ChaosFS(
        root=str(backing_path),
        meta_ttl=meta_ttl,
        write_delay=write_delay,
        rename_delay=rename_delay,
        drop_prob=drop_prob,
        seed=seed,
        client_id=client_id,
    )

    fuse_thread = threading.Thread(
        target=FUSE,
        args=(chaos, str(mount_path)),
        kwargs=dict(foreground=True, nothreads=True, allow_other=False),
        daemon=True,
    )
    fuse_thread.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if mount_path.is_mount():
            break
        time.sleep(0.05)
    else:
        raise RuntimeError(f"Mount point {mount_path} did not become ready within 5 seconds")

    try:
        yield mount_path
    finally:
        try:
            primary, fallback = _pick_unmount_command()
            _run_unmount(mount_path, primary, fallback)
        except UnmountError:
            logger.warning("No unmount utility found; mount %s may be left behind", mount_path)

        fuse_thread.join(timeout=1.0)


@contextlib.contextmanager
def dual_mount(
    base_path,
    writer_id="writer",
    reader_id="reader",
    writer_meta_ttl=0.5,
    writer_rename_delay=0.1,
    reader_meta_ttl=2.0,
    reader_rename_delay=0.5,
) -> Iterator[tuple[Path, Path]]:
    """Mount writer (low delays) and reader (high TTL) ChaosFS views over one backing dir."""
    base = Path(base_path).resolve()

    for name in ("backing", "writer", "reader"):
        (base / name).mkdir(parents=True, exist_ok=True)

    with mount(
        backing_dir=base / "backing",
        mount_point=base / "writer",
        client_id=writer_id,
        meta_ttl=writer_meta_ttl,
        write_delay=0.1,
        rename_delay=writer_rename_delay,
    ) as writer_mount:
        with mount(
            backing_dir=base / "backing",
            mount_point=base / "reader",
            client_id=reader_id,
            meta_ttl=reader_meta_ttl,
            write_delay=0.8,
            rename_delay=reader_rename_delay,
        ) as reader_mount:
            yield (writer_mount, reader_mount)