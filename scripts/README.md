# scripts/README

This directory mainly uses the two entry scripts below.

## How Root Is Obtained In QEMU

There are two root-shell paths:

1. Direct root shell mode:
   - Run `./local_runner.sh <release-name> --root`.
   - This sets `INIT_FN=/bin/bash`.
   - `local_runner.sh` calls `qemu_v3.sh ... "$INIT_FN"`.
   - `qemu_v3.sh` passes `init=/bin/bash` in kernel cmdline, so QEMU boots directly into a root shell.

2. Prepared exploit mode (used by `get_root.sh`):
   - `get_root.sh` unpacks the initramfs into `core/`, creates `core/test.sh`, and injects commands into `core/init`.
   - Injected init commands copy payloads into the real rootfs before handoff:
     - `exp` -> `/root/chroot/bin` and `/root/bin/exp`
     - `test.sh` -> `/root/home/user/run.sh`
   - In generated `test.sh`, the `kctf_drop_privs nsjail ...` line is commented out and replaced by `/bin/bash`.
   - After repacking to `rootfs.cpio`, `get_root.sh` calls `local_runner.sh <release-name>` (default init is `/home/user/run.sh`).
   - Since `/home/user/run.sh` is replaced by that `test.sh`, the guest ends in a root shell instead of dropping privileges.

## `local_runner.sh`

- Usage: `./local_runner.sh <release-name> [--root]`
- Purpose: prepare required runtime files and launch the selected kernel release through `qemu_v3.sh`.
- Behavior:
  - If `releases/<release-name>/bzImage` is missing, it is downloaded from kernelCTF prebuilt releases.
  - By default, it uses `/home/user/run.sh` as the guest init command; with `--root`, it uses `/bin/bash`.

## `get_root.sh`

- Usage: `./get_root.sh <release-name>`
- Purpose: prepare rootfs/ramdisk and debugging setup, inject `exp` and root-shell `test.sh`, rebuild `rootfs.cpio`, then call `local_runner.sh`.
- Behavior:
  - Auto-prepares dependencies such as `qemu_v3.sh`, `rootfs_v3.img`, `ramdisk_v1.img`, and `flag` when missing.
  - Supports custom debug ports via `QEMU_GDB_PORT` and `QEMU_SERIAL_PORT`.
