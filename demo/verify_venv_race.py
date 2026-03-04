#!/usr/bin/env python3
"""Run concurrent virtualenv creations to detect stale metadata or missing files."""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RaceResult:
    client_id: str
    target: Path
    iteration: int
    returncode: int
    stdout: str
    stderr: str


def main() -> None:
    args = _parse_args()
    if len(args.mount) < 2:
        print("Provide at least two --mount arguments so that races can appear", file=sys.stderr)
        sys.exit(1)
    if args.iterations < 1:
        print("--iterations must be >= 1", file=sys.stderr)
        sys.exit(1)

    for mount_point in args.mount:
        if not Path(mount_point).exists():
            print(
                f"Mount point {mount_point} does not exist; mount a ChaosFS client with `chaosfs mount ...`",
                file=sys.stderr,
            )
            sys.exit(1)

    results: list[RaceResult] = []
    for iteration in range(1, args.iterations + 1):
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(args.mount)) as executor:
            futures = [
                executor.submit(_run_venv, f"client{idx}", mount_point, args.target_rel, iteration)
                for idx, mount_point in enumerate(args.mount, start=1)
            ]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())

    anomalies = []
    for result in results:
        issues = _verify_result(result)
        if issues:
            anomalies.append((result, issues))

    if anomalies:
        print("Race verifier detected anomalies:")
        for result, issues in anomalies:
            print(
                f"- iteration {result.iteration} {result.client_id} ({result.target}): {', '.join(issues)}"
            )
            if result.stderr.strip():
                print(f"  stderr: {result.stderr.strip()}")
        sys.exit(1)

    print(
        f"Race verifier completed successfully after {args.iterations} rounds; shared virtualenv path stayed consistent."
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify eventual consistency for Python virtualenv creation."
    )
    parser.add_argument(
        "-m",
        "--mount",
        action="append",
        required=True,
        help="Mount point that should point to a chaosfs client (repeat twice).",
    )
    parser.add_argument(
        "--target-rel",
        default="venvcache/shared-venv",
        help="Relative venv path used by all clients to force contention.",
    )
    parser.add_argument(
        "--iterations", type=int, default=3, help="How many concurrent rounds to run."
    )
    return parser.parse_args()


def _run_venv(client_id: str, mount_point: str, target_rel: str, iteration: int) -> RaceResult:
    target = Path(mount_point) / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CHAOSFS_CLIENT_ID"] = client_id
    proc = subprocess.run(
        [sys.executable, "-m", "venv", "--clear", str(target)],
        capture_output=True,
        text=True,
        env=env,
    )
    return RaceResult(client_id, target, iteration, proc.returncode, proc.stdout, proc.stderr)


def _verify_result(result: RaceResult) -> list[str]:
    issues = []
    if result.returncode != 0:
        issues.append(f"venv command failed ({result.returncode})")
    if not (result.target / "pyvenv.cfg").exists():
        issues.append("pyvenv.cfg missing")
    if not (result.target / "bin" / "python").exists():
        issues.append("python binary missing")
    return issues


if __name__ == "__main__":
    main()
