"""Command-line interface that wires ChaosFS into mount/unmount workflows."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from fuse import FUSE

from .filesystem import ChaosFS

DEFAULT_META_TTL_MS = 1500
DEFAULT_WRITE_DELAY_MS = 800
DEFAULT_RENAME_DELAY_MS = 1200
DEFAULT_DROP_PROB = 0.0
ENV_LOG_DIR = "LOG_DIR"
ENV_CLIENT_ID = "CHAOSFS_CLIENT_ID"
ENV_META_TTL = "CHAOSFS_META_TTL_MS"
ENV_WRITE_DELAY = "CHAOSFS_WRITE_DELAY_MS"
ENV_RENAME_DELAY = "CHAOSFS_RENAME_DELAY_MS"
ENV_DROP_PROB = "CHAOSFS_DROP_PROB"
ENV_SEED = "CHAOSFS_SEED"
ENV_MOUNT_BASE = "MOUNT_BASE"


class MountError(ValueError):
    pass


class UnmountError(ValueError):
    pass


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="chaosfs", description="Manage ChaosFS mounts")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    mount_parser = subparsers.add_parser("mount", help="Mount a ChaosFS client")
    mount_parser.add_argument("backing", help="Backing directory that stores the real files")
    mount_parser.add_argument("mountpoint", help="Destination to mount the ChaosFS client")
    mount_parser.add_argument("--meta-ttl", type=int, help="Metadata TTL in milliseconds")
    mount_parser.add_argument(
        "--write-delay", type=int, help="Write visibility delay in milliseconds"
    )
    mount_parser.add_argument(
        "--rename-delay", type=int, help="Rename/directory delay in milliseconds"
    )
    mount_parser.add_argument("--drop-prob", type=float, help="Probabilistic drop rate (0-1)")
    mount_parser.add_argument("--seed", type=int, help="Deterministic chaos seed")
    mount_parser.add_argument("--client-id", help="Namespace for client caches")
    mount_parser.add_argument("--log-file", help="Path for FUSE logs")
    mount_parser.add_argument(
        "--log-dir", help="Directory to write log file named after the client"
    )
    mount_parser.add_argument("--debug", action="store_true", help="Enable fusepy debug output")
    mount_parser.add_argument(
        "--background", action="store_true", help="Mount in background (FUSE daemon) and return"
    )

    umount_parser = subparsers.add_parser("umount", help="Unmount ChaosFS clients")
    umount_parser.add_argument("-m", "--mount", action="append", help="Mount point to unmount")
    umount_parser.add_argument("--mount-base", help="Directory whose entries should be unmounted")

    args = parser.parse_args(argv)
    try:
        if args.command == "mount":
            mount_command(args)
        elif args.command == "umount":
            umount_command(args)
        else:
            parser.print_help()
            raise SystemExit(1)
    except (MountError, UnmountError) as exc:
        parser.error(str(exc))


def mount_command(args: argparse.Namespace) -> None:
    backing = Path(args.backing).resolve()
    mountpoint = Path(args.mountpoint).resolve()

    if not backing.exists():
        raise MountError(f"Backing directory {backing} does not exist")
    if not backing.is_dir():
        raise MountError(f"Backing path {backing} is not a directory")

    if not mountpoint.exists():
        raise MountError(f"Mount target {mountpoint} does not exist")
    if not mountpoint.is_dir():
        raise MountError(f"Mount target {mountpoint} is not a directory")
    if mountpoint.is_mount():
        raise MountError(f"Mount target {mountpoint} is already mounted")
    try:
        if any(mountpoint.iterdir()):
            raise MountError(f"Mount target {mountpoint} is not empty")
    except OSError as exc:
        raise MountError(f"Cannot inspect mount target {mountpoint}: {exc}") from exc

    client_id = args.client_id or os.environ.get(ENV_CLIENT_ID) or "client"

    if args.log_file:
        Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
        log_file = args.log_file
    else:
        log_dir = args.log_dir or os.environ.get(ENV_LOG_DIR)
        if log_dir:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            log_file = str(Path(log_dir) / f"{client_id}.log")
        else:
            log_file = None

    if args.background and log_file is None:
        print(
            "WARNING: running in --background mode without --log-dir/--log-file; logs may not be visible.",
            file=sys.stderr,
        )

    handler = logging.FileHandler(log_file) if log_file else logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if args.debug else logging.INFO)

    if args.meta_ttl is not None:
        meta_ttl = max(args.meta_ttl, 0) / 1000.0
    else:
        try:
            meta_ttl = max(int(os.environ.get(ENV_META_TTL, DEFAULT_META_TTL_MS)), 0) / 1000.0
        except ValueError:
            meta_ttl = DEFAULT_META_TTL_MS / 1000.0

    if args.write_delay is not None:
        write_delay = max(args.write_delay, 0) / 1000.0
    else:
        try:
            write_delay = (
                max(int(os.environ.get(ENV_WRITE_DELAY, DEFAULT_WRITE_DELAY_MS)), 0) / 1000.0
            )
        except ValueError:
            write_delay = DEFAULT_WRITE_DELAY_MS / 1000.0

    if args.rename_delay is not None:
        rename_delay = max(args.rename_delay, 0) / 1000.0
    else:
        try:
            rename_delay = (
                max(
                    int(os.environ.get(ENV_RENAME_DELAY, DEFAULT_RENAME_DELAY_MS)),
                    0,
                )
                / 1000.0
            )
        except ValueError:
            rename_delay = DEFAULT_RENAME_DELAY_MS / 1000.0

    if args.drop_prob is not None:
        drop_prob = args.drop_prob
    else:
        try:
            drop_prob = float(os.environ.get(ENV_DROP_PROB, str(DEFAULT_DROP_PROB)))
        except ValueError:
            drop_prob = DEFAULT_DROP_PROB
    drop_prob = min(max(0.0, drop_prob), 1.0)

    if args.seed is not None:
        seed = args.seed
    else:
        try:
            seed = int(os.environ[ENV_SEED]) if ENV_SEED in os.environ else None
        except ValueError:
            seed = None

    chaos = ChaosFS(
        root=str(backing),
        meta_ttl=meta_ttl,
        write_delay=write_delay,
        rename_delay=rename_delay,
        drop_prob=drop_prob,
        seed=seed,
        client_id=client_id,
    )
    logging.info(
        "Starting chaosfs (client=%s) meta=%sms write=%sms rename=%sms drop=%s",
        client_id,
        int(meta_ttl * 1000),
        int(write_delay * 1000),
        int(rename_delay * 1000),
        drop_prob,
    )
    try:
        FUSE(
            chaos,
            str(mountpoint),
            foreground=not args.background,
            nothreads=True,
            allow_other=False,
            debug=args.debug,
        )
    finally:
        if not args.background:
            try:
                should_unmount = mountpoint.is_mount()
            except OSError:
                should_unmount = True
            if should_unmount:
                try:
                    primary, fallback = _pick_unmount_command()
                except UnmountError:
                    pass
                else:
                    _run_unmount(mountpoint, primary, fallback)


def umount_command(args: argparse.Namespace) -> None:
    primary, fallback = _pick_unmount_command()
    targets = [Path(m).resolve() for m in args.mount] if args.mount else []
    if not targets:
        base = args.mount_base or os.environ.get(ENV_MOUNT_BASE)
        if not base:
            raise UnmountError("Specify --mount or set MOUNT_BASE to target unmounts")
        base_path = Path(base)
        if not base_path.exists():
            raise UnmountError(f"Mount base {base_path} does not exist")
        targets = sorted(base_path.iterdir())
    if not targets:
        raise UnmountError("No mount targets found")

    for target in targets:
        if not target.exists():
            print(f"Skipping {target}: path does not exist")
            continue
        name, success = _run_unmount(target, primary, fallback)
        print(f"{target}: {'unmounted' if success else 'failed'} ({name})")


def _pick_unmount_command() -> tuple[list[str], list[str]]:
    for binary in ("fusermount", "fusermount3"):
        path = shutil.which(binary)
        if path:
            return [path, "-u"], [path, "-uz"]

    path = shutil.which("umount")
    if path:
        return [path], [path, "-l"]
    raise UnmountError("No unmount utility available (fusermount/fusermount3/umount)")


def _run_unmount(target: Path, primary: list[str], fallback: list[str]) -> tuple[str, bool]:
    for name, command in (("primary", primary), ("fallback", fallback)):
        if subprocess.run([*command, str(target)]).returncode == 0:
            return name, True
    return "primary", False


if __name__ == "__main__":
    main()
