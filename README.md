## qemu-kernel-mcp

MCP server for Linux kernel vulnerability research workflows.

### Features

- `set_poc`: verify PoC is a statically linked ELF, then push it into guest as `/bin/exp` (prefer `wget`, fallback to serial chunk upload).
- `run_qemu`: start QEMU and auto-assign gdb port.
- `run_command`: execute commands in guest through one-shot `nc` over serial (tmux kept for QEMU session management, `timeout` must be <= 60s).
- `run_poc`: run PoC command in guest (`timeout` must be <= 60s).
- `list_sessions`: list all active/stored QEMU sessions.
- `stop_qemu`: stop a QEMU session by `session_id` (or active session).

### Use uv

```bash
uv sync
uv run qemu-kernel-mcp
```

HTTP transports:

```bash
# SSE
uv run qemu-kernel-mcp --transport sse --host 0.0.0.0 --port 8000

# Streamable HTTP (alias: --transport stream-http)
uv run qemu-kernel-mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

### Notes

- `run_qemu` invokes `scripts/get_root.sh <release_name>`.
- `run_qemu` returns `session_id`; `run_command`/`run_poc` can pass `session_id`.
- `set_poc` requires a running QEMU session (uses active session by default or accepts `session_id`).
- Host dependencies for command execution: `tmux`, `nc`.
- Linux kernel images are pulled from kernelCTF prebuilt releases by default.
  - `scripts/local_runner.sh` downloads `releases/<release_name>/bzImage` from `https://storage.googleapis.com/kernelctf-build/releases/<release_name>/bzImage` when missing.
  - If you want to use your own kernel build, create a new folder under `scripts/releases/` and put your compiled `bzImage` into that folder.
  - When asking the agent to run QEMU (or passing `release_name`), use the new folder name as `release_name`.
- GDB remote target:
  - `target remote 127.0.0.1:<gdb_port>`
- `scripts/get_root.sh`:
  - prepares runtime dependencies (`qemu_v3.sh`, `rootfs_v3.img`, `ramdisk_v1.img`, `flag`) when missing.
  - unpacks/modifies initramfs (`core/init`, `core/test.sh`), rebuilds `rootfs.cpio`, then calls `local_runner.sh`.
- `scripts/local_runner.sh`:
  - ensures release kernel image (`releases/<release>/bzImage`) and required runtime files exist.
  - launches QEMU through `qemu_v3.sh` with the selected release and init entry.
