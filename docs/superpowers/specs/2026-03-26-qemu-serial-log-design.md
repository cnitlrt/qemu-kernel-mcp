# QEMU Serial Full-Log Preservation Design

**Date:** 2026-03-26

## Problem

Current `run_qemu` logging only captures the host-side launcher output produced by `get_root.sh` and related shell scripts. It does **not** capture the guest's full serial console stream, because `qemu_v3.sh` routes guest console I/O through `-serial tcp:127.0.0.1:${QEMU_SERIAL_PORT},server=on,...` while the existing log file is produced from `... | tee .state/qemu_<session>.log` on the launcher process.

As a result, after scenarios like:
- booting QEMU,
- running `/bin/exp`,
- attaching GDB,
- modifying `rip`,
- continuing execution,

operators can lose visibility into the guest's final serial output in the existing per-session log file.

## Goals

- Preserve **all guest serial output** after QEMU startup in a per-session file.
- Keep the existing `run_qemu` host-side log behavior unchanged.
- Keep existing tool behavior unchanged for:
  - `run_command`
  - `run_poc`
  - manual serial connections using the returned `serial_port`
- Avoid requiring callers to change their workflow.

## Non-goals

- Redesigning the guest command protocol.
- Replacing tmux-based QEMU session management.
- Converting logging to a structured/event format.
- Supporting multiple simultaneous independent serial clients beyond current practical behavior.

## Approaches Considered

### 1. Keep current `tee` logging only

**Pros:** no code change.

**Cons:** does not solve the problem because guest serial data never reaches launcher stdout/stderr.

### 2. Use tmux pane capture / pipe-pane

**Pros:** relatively small change.

**Cons:** captures pane-visible output rather than the actual serial byte stream; less reliable for crash scenarios and protocol-level debugging.

### 3. Insert a transparent serial proxy/logger between callers and QEMU (**recommended**)

**Pros:**
- captures the real guest serial stream,
- preserves current client-facing `serial_port` workflow,
- keeps existing `.state/qemu_<session>.log` intact,
- records output even when `run_command` parsing fails due to crashes or GDB intervention.

**Cons:** adds a small amount of socket/threading complexity.

## Recommended Design

### High-level architecture

For each QEMU session, allocate two ports instead of one:

- **qemu_serial_backend_port**: QEMU binds its `-serial tcp:...` listener here
- **serial_port**: the existing externally exposed port returned to callers

A new background serial proxy is started by `run_qemu`:

- listens on `serial_port`
- connects through to `qemu_serial_backend_port`
- forwards traffic bidirectionally
- appends all bytes received from the QEMU side into a new per-session file:
  - `.state/qemu_<session>.serial.log`

This preserves the current public contract: callers continue using `serial_port`, but now all guest serial output is durably recorded.

### Logging model

Keep both logs:

1. **Host launcher log** (existing)
   - `.state/qemu_<session>.log`
   - contains shell/bootstrap output and port announcements

2. **Guest serial log** (new)
   - `.state/qemu_<session>.serial.log`
   - contains the continuous guest serial stream after QEMU startup

The new serial log should be written in binary-safe append mode so that crashes or partial lines are still preserved.

### Service/model changes

Extend session state to track:
- backend serial port used by QEMU
- serial log file path
- proxy lifecycle handle(s) needed for cleanup

Expose the new serial log path in:
- session payload
- `run_qemu` tips/output

### Cleanup behavior

When a new QEMU session is launched or an existing one is stopped:
- stop the serial proxy for that session,
- close listener/client sockets,
- keep log files on disk.

Cleanup failures should not prevent best-effort QEMU teardown, but should avoid leaving live proxy threads around.

### Failure handling

- If proxy startup fails, `run_qemu` should fail fast and avoid publishing a broken session.
- If the guest or client disconnects later, the proxy may terminate gracefully while leaving the serial log intact.
- `run_command` should continue to behave as it does today; this change only improves observability.

## Files Expected To Change

- `src/qemu_kernel_mcp/models.py`
  - store backend serial port, serial log path, and proxy handles/state
- `src/qemu_kernel_mcp/service.py`
  - allocate two serial-related ports
  - launch/stop proxy
  - return serial log metadata
  - include proxy cleanup in teardown
- `README.md`
  - document the two-log behavior and the new serial log artifact

## Testing Strategy

1. Unit-level / focused behavioral tests for proxy lifecycle and session payloads.
2. Integration-style test at service level to verify:
   - session creation returns both log paths,
   - serial bytes forwarded through the exposed port are appended to the serial log,
   - stopping session cleans up proxy resources.
3. Regression verification that existing host-side log path remains unchanged.

## Risks

- Thread/socket leaks if cleanup paths are incomplete.
- Port confusion if public vs backend serial ports are not clearly named.
- Tests can become flaky if they rely on long sleeps rather than deterministic socket synchronization.

## Mitigations

- Keep proxy implementation minimal and explicitly closable.
- Use distinct field names for public and backend ports.
- Prefer short local socket tests with explicit readiness signaling.
