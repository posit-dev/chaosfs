# Demo Catalog

This folder contains optional use-case demos for ChaosFS.
These demos are not required to use the core `chaosfs` CLI.

## Common prerequisites

Before running any demo:

1. Install and expose the CLI:

```bash
pip install -e .
```

2. Prepare one shared backing directory and at least two mountpoints:

```bash
BACKING_DIR=/tmp/fake-nfs/backing
MOUNT_BASE=/tmp/fake-nfs/mnt
LOG_DIR=/tmp/fake-nfs/logs

mkdir -p "$BACKING_DIR" "$MOUNT_BASE/clientA" "$MOUNT_BASE/clientB" "$LOG_DIR"
chaosfs mount "$BACKING_DIR" "$MOUNT_BASE/clientA" --client-id clientA --log-dir "$LOG_DIR" --background
chaosfs mount "$BACKING_DIR" "$MOUNT_BASE/clientB" --client-id clientB --log-dir "$LOG_DIR" --background
```

3. Unmount after the demo:

```bash
chaosfs umount --mount-base "$MOUNT_BASE"
```

## `demo_race.sh`

Purpose:
- Visual, quick sanity check that one client can observe stale directory state while another client mutates files.

Command:

```bash
MOUNT_BASE="$MOUNT_BASE" LOG_DIR=/tmp/fake-nfs/demo-logs bash demo/demo_race.sh
```

Expected behavior:
- The watcher log can temporarily show stale or missing entries while writes/renames propagate.

## `verify_venv_race.py`

Purpose:
- Reproduce a concrete concurrent-venv creation race (two clients writing to the same venv path).

Command:

```bash
python demo/verify_venv_race.py \
  --mount "$MOUNT_BASE/clientA" \
  --mount "$MOUNT_BASE/clientB" \
  --iterations 5
```

Expected behavior:
- Under aggressive chaos settings, failures are expected and useful; they indicate race windows.
