# FAKE_NFS chaos harness

FAKE_NFS is a local FUSE harness for reproducing NFS-style consistency issues without using production systems. It lets you mount multiple ChaosFS clients over one backing directory and inject delayed visibility, stale metadata/listing views, and operation failures so concurrent read/write workflows can be tested under realistic race conditions.

## Project layout

- `src/chaosfs/`: installable ChaosFS package that exposes `ChaosFS` and the `chaosfs` CLI.
- `demo/`: optional walkthroughs and validators that run against mounted ChaosFS clients.
- `tests/`: regression tests for the ChaosFS implementation.

## Prerequisites

- Python 3.10 or newer.
- [`fusepy`](https://github.com/fusepy/fusepy) installed (the package metadata already depends on it).
- A FUSE implementation available for unprivileged mounts (`fusermount`, `fusermount3`, or `umount`).

## Installation

Install from the repo root on any platform that needs ChaosFS:

```bash
pip install -e .
```

The installation exposes `chaosfs` as the primary user-facing command.

## CLI usage

`chaosfs mount <backing> <mountpoint>` starts a FUSE client that surfaces a delayed, write-late, cache-incoherent view of `<backing>` at `<mountpoint>`. The command reads sensible defaults for the chaos knobs from environment variables (see the *Chaos knobs* section) but lets you override them as flags.
By default, logs are written to the terminal; use `--log-file` or `--log-dir` to persist them.

`chaosfs umount` can tear down a single mount point with `--mount <path>` or sweep an entire directory tree with `--mount-base <dir>`.

### Preparing backing and mount paths

Create the backing and mount directories before calling `chaosfs mount`. The command intentionally fails if:
- the backing directory does not exist
- the target mount directory does not exist
- the target mount directory is not empty

Once you have a backing tree you care about, create one mountpoint per client:

```bash
BACKING_DIR=/tmp/fake-nfs/backing
MOUNT_BASE=/tmp/fake-nfs/mnt
LOG_DIR=/tmp/fake-nfs/logs

mkdir -p "$BACKING_DIR" "$MOUNT_BASE" "$LOG_DIR"

chaosfs mount "$BACKING_DIR" "$MOUNT_BASE/clientA" \
  --client-id clientA --log-dir "$LOG_DIR" --background
chaosfs mount "$BACKING_DIR" "$MOUNT_BASE/clientB" \
  --client-id clientB --log-dir "$LOG_DIR" --background
```

Both mounts point at the same backing data. You can run as many clients as you like against a single backing directory (there is no requirement to create more than one mount point, single-client workloads are also valid).

Use `--background` if you want the CLI command to return immediately; omit it when running one mount in a dedicated foreground terminal for debugging.
When running in background without `--log-dir`/`--log-file`, `chaosfs` still runs but prints a warning because logs may not be visible.
In foreground mode, when the process exits, `chaosfs` performs a best-effort unmount of that mountpoint.

To unmount everything later:

```bash
chaosfs umount --mount-base "$MOUNT_BASE"
```

Or target a single mount:

```bash
chaosfs umount --mount "$MOUNT_BASE/clientA"
```

### Chaos knobs

| Env var | Default | Description |
| --- | --- | --- |
| `CHAOS_META_TTL_MS` | `1500` | Metadata and directory listing TTL per client. |
| `CHAOS_WRITE_DELAY_MS` | `800` | How long writes take to become globally visible. |
| `CHAOS_RENAME_DELAY_MS` | `1200` | Delay for rename/directory visibility and cache invalidation. |
| `CHAOS_DROP_PROB` | `0.0` | Probability that each operation fails with `EIO`. |
| `CHAOS_SEED` | unset | Seed for deterministic randomness. |
| `CLIENT_ID` | `client` | Namespace used for per-client caches. |

You can also provide these values as CLI flags (`--meta-ttl`, `--write-delay`, etc.) in milliseconds.

## Demos & use cases

See `demo/README.md` for the demo catalog, including a generic race visualizer and a concurrent-venv reproduction scenario.
Demo scripts use `LOG_DIR` when provided.

## Testing

```bash
ruff format --check .
ruff check .
python -m unittest tests/test_chaosfs.py
python -m chaosfs.cli --help
```
