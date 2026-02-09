from __future__ import annotations

import contextlib
import functools
import http.server
import re
import shlex
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from pwnlib.context import context
from pwnlib.tubes.remote import remote

from .config import SCRIPTS_DIR, STATE_DIR
from .models import QemuSession, ServerState
from .utils import allocate_port, run_cmd


class KernelPwnService:
    def __init__(self) -> None:
        self.state = ServerState()
        self._lock = threading.Lock()

    def set_poc(self, poc_file: str, session_id: str | None = None) -> dict[str, Any]:
        source = Path(poc_file).expanduser().resolve()
        if not source.exists():
            return {"ok": False, "error": f"poc file not found: {source}"}
        if not source.is_file():
            return {"ok": False, "error": f"poc path is not a file: {source}"}
        static_check = self._check_static_binary(source)
        if not static_check["ok"]:
            return static_check

        with self._lock:
            session = self._resolve_session(session_id)
            if session is None:
                return {"ok": False, "error": "no running qemu session for set_poc"}

            http_port = allocate_port()
            poc_name = source.name
            guest_url = f"http://10.0.2.2:{http_port}/{poc_name}"
            cmd = (
                f"(wget -qO /bin/exp {shlex.quote(guest_url)} "
                f"|| busybox wget -qO /bin/exp {shlex.quote(guest_url)}) && "
                "chmod +x /bin/exp"
            )
            with self._temporary_http_server(source.parent, http_port):
                transfer = self._run_nc_command(session, cmd, timeout=30)
            method = "wget"
            if transfer["returncode"] != 0:
                fallback = self._upload_poc_via_serial_chunks(session, source)
                if not fallback["ok"]:
                    return {
                        "ok": False,
                        "error": "failed to transfer poc into guest via wget and serial fallback",
                        "wget_details": transfer,
                        "fallback_details": fallback,
                    }
                method = "serial-chunks"

            self.state.poc_path = source
            return {
                "ok": True,
                "poc_path": str(source),
                "is_static": True,
                "session_id": session.session_id,
                "download_url": guest_url,
                "transfer_method": method,
                "vm_path": "/bin/exp",
            }

    def run_qemu(self, release_name: str) -> dict[str, Any]:
        with self._lock:
            if shutil.which("tmux") is None:
                return {"ok": False, "error": "tmux not found on host"}

            cleanup_results: list[dict[str, Any]] = []
            for old_session in list(self.state.qemu_sessions.values()):
                was_running = self._tmux_session_exists(old_session.tmux_session)
                if old_session.tmux_session:
                    run_cmd(["tmux", "send-keys", "-t", old_session.tmux_session, "exit", "C-m"], timeout=3)
                    run_cmd(["tmux", "kill-session", "-t", old_session.tmux_session], timeout=5)
                with contextlib.suppress(Exception):
                    run_cmd(["pkill", "-f", f"qemu-system-x86_64.*{old_session.gdb_port}"], timeout=3)
                cleanup_results.append(
                    {
                        "session_id": old_session.session_id,
                        "was_running": was_running,
                        "still_running": self._tmux_session_exists(old_session.tmux_session),
                    }
                )

            self.state.qemu_sessions.clear()
            self.state.active_session_id = None

            gdb_port = allocate_port()
            serial_port = allocate_port()
            session_id = uuid4().hex[:8]
            tmux_name = f"qemu_kernel_mcp_{session_id}"

            STATE_DIR.mkdir(parents=True, exist_ok=True)
            log_path = STATE_DIR / f"qemu_{session_id}.log"

            run_cmd(["tmux", "kill-session", "-t", tmux_name], timeout=3)
            launch_cmd = (
                f"cd {shlex.quote(str(SCRIPTS_DIR))} && "
                f"QEMU_GDB_PORT={gdb_port} QEMU_SERIAL_PORT={serial_port} "
                f"bash get_root.sh {shlex.quote(release_name)} 2>&1 | tee {shlex.quote(str(log_path))}"
            )
            created = run_cmd(["tmux", "new-session", "-d", "-s", tmux_name, f"bash -lc {shlex.quote(launch_cmd)}"])
            if not created["ok"]:
                return {"ok": False, "error": "failed to start tmux qemu session", "details": created}

            session = QemuSession(
                session_id=session_id,
                release_name=release_name,
                gdb_port=gdb_port,
                serial_port=serial_port,
                log_path=log_path,
                tmux_session=tmux_name,
            )
            self.state.qemu_sessions[session_id] = session
            self.state.active_session_id = session_id

            return {
                "ok": True,
                "session_id": session_id,
                "cleanup": cleanup_results,
                "session": self._session_payload(session),
                "tips": {
                    "tmux_session": tmux_name,
                    "attach": f"tmux attach -t {tmux_name}",
                    "gdb_target": f"target remote 127.0.0.1:{gdb_port}",
                    "serial_connect": (
                        "python3 -c 'from pwn import remote; "
                        f"io=remote(\"127.0.0.1\", {serial_port}); "
                        "io.interactive()'"
                    ),
                    "log_file": str(log_path),
                },
            }

    def run_command(self, command: str, timeout: int = 20, session_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id)
            if session is None:
                return {"ok": False, "error": "no running qemu session"}
            if not self._tmux_session_exists(session.tmux_session):
                return {"ok": False, "error": "tmux session does not exist; qemu may have exited"}
            result = self._run_nc_command(session, command, timeout=timeout)
            return {
                "ok": result["returncode"] == 0,
                "session_id": session.session_id,
                "tmux_session": session.tmux_session,
                "serial_port": session.serial_port,
                "command": command,
                "exit_code": result["returncode"],
                "output": result["stdout"],
                "stderr": result["stderr"],
            }

    def run_poc(self, cmd: str = "/bin/exp", timeout: int = 30, session_id: str | None = None) -> dict[str, Any]:
        return self.run_command(cmd, timeout=timeout, session_id=session_id)

    def list_sessions(self) -> dict[str, Any]:
        with self._lock:
            sessions = [self._session_payload(s) for s in self.state.qemu_sessions.values()]
            return {"ok": True, "active_session_id": self.state.active_session_id, "sessions": sessions}

    def stop_qemu(self, session_id: str | None = None, force: bool = False) -> dict[str, Any]:
        del force
        with self._lock:
            session = self._resolve_session(session_id, require_running=False)
            if session is None:
                return {"ok": False, "error": "session not found"}

            was_running = self._tmux_session_exists(session.tmux_session)
            if session.tmux_session:
                run_cmd(["tmux", "send-keys", "-t", session.tmux_session, "exit", "C-m"], timeout=3)
                run_cmd(["tmux", "kill-session", "-t", session.tmux_session], timeout=5)

            try:
                run_cmd(["pkill", "-f", f"qemu-system-x86_64.*{session.gdb_port}"], timeout=3)
            except Exception:
                pass

            self.state.qemu_sessions.pop(session.session_id, None)
            if self.state.active_session_id == session.session_id:
                self.state.active_session_id = next(iter(self.state.qemu_sessions), None)

            still_running = self._tmux_session_exists(session.tmux_session)
            return {
                "ok": not still_running,
                "stopped_session_id": session.session_id,
                "was_running": was_running,
                "still_running": still_running,
            }

    def _run_nc_command(self, session: QemuSession, command: str, timeout: int) -> dict[str, Any]:
        begin = f"__MCP_BEGIN_{uuid4().hex[:8]}__"
        end = f"__MCP_END_{uuid4().hex[:8]}__"
        payload = f"echo {begin}; {command}; rc=$?; echo {end}:$rc\n"

        proc = self._run_nc_once(session.serial_port, payload, end_marker=end, timeout=timeout)
        if proc.returncode != 0:
            return {
                "returncode": 124,
                "stdout": proc.stdout or "",
                "stderr": (proc.stderr or "marker not found in serial output").strip(),
            }

        captured = proc.stdout or ""
        parsed = self._extract_marked_output(captured, begin, end)
        if not parsed["ok"]:
            return {
                "returncode": 124,
                "stdout": parsed.get("output", ""),
                "stderr": parsed["error"],
            }
        return {
            "returncode": parsed["exit_code"],
            "stdout": parsed["output"],
            "stderr": (proc.stderr or "").strip(),
        }

    @staticmethod
    def _run_nc_once(
        serial_port: int,
        payload: str,
        end_marker: str,
        timeout: int,
    ) -> Any:
        io = None
        connect_timeout = float(max(1, min(timeout, 3)))
        deadline = time.monotonic() + max(1.0, float(timeout))
        lines: list[str] = []
        saw_end = False
        try:
            with context.local(log_level="error"):
                io = remote("127.0.0.1", serial_port, timeout=connect_timeout)
                io.send(payload.encode())

                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    line_timeout = max(0.05, min(0.25, remaining))
                    try:
                        raw = io.recvline(timeout=line_timeout)
                    except EOFError:
                        break
                    if not raw:
                        continue
                    line = raw.decode(errors="replace").replace("\r", "").rstrip("\n")
                    lines.append(line)
                    if end_marker in line:
                        saw_end = True
                        break

            captured = "\n".join(lines).strip()
            if not saw_end:
                return SimpleNamespace(
                    returncode=124,
                    stdout=captured,
                    stderr="marker not found in serial output before timeout",
                )
            return SimpleNamespace(returncode=0, stdout=captured, stderr="")
        except Exception as exc:
            return SimpleNamespace(
                returncode=124,
                stdout="\n".join(lines).strip(),
                stderr=f"pwntools command failed: {exc}",
            )
        finally:
            if io is not None:
                with contextlib.suppress(Exception):
                    io.close()

    def _upload_poc_via_serial_chunks(self, session: QemuSession, source: Path) -> dict[str, Any]:
        data = source.read_bytes()
        start = self._run_nc_command(session, ": > /bin/exp", timeout=10)
        if start["returncode"] != 0:
            return {"ok": False, "stage": "truncate", "details": start}

        chunk_size = 128
        for idx in range(0, len(data), chunk_size):
            chunk = data[idx:idx + chunk_size]
            escaped = "".join(f"\\{byte:03o}" for byte in chunk)
            cmd = f"printf '{escaped}' >> /bin/exp"
            res = self._run_nc_command(session, cmd, timeout=20)
            if res["returncode"] != 0:
                return {
                    "ok": False,
                    "stage": "write-chunk",
                    "offset": idx,
                    "details": res,
                }

        chmod_res = self._run_nc_command(session, "chmod +x /bin/exp", timeout=10)
        if chmod_res["returncode"] != 0:
            return {"ok": False, "stage": "chmod", "details": chmod_res}

        verify = self._run_nc_command(session, "wc -c < /bin/exp", timeout=10)
        if verify["returncode"] != 0:
            return {"ok": False, "stage": "verify-size", "details": verify}

        size_str = (verify["stdout"] or "").strip()
        try:
            guest_size = int(size_str.splitlines()[-1].strip())
        except (ValueError, IndexError):
            return {"ok": False, "stage": "parse-size", "details": verify}
        if guest_size != len(data):
            return {
                "ok": False,
                "stage": "size-mismatch",
                "expected_size": len(data),
                "guest_size": guest_size,
            }

        return {"ok": True}

    @staticmethod
    def _check_static_binary(path: Path) -> dict[str, Any]:
        file_info = run_cmd(["file", "-b", str(path)], timeout=10)
        if not file_info["ok"]:
            return {"ok": False, "error": "failed to inspect poc binary", "details": file_info}
        desc = (file_info["stdout"] or "").lower()
        if "elf" not in desc:
            return {"ok": False, "error": "poc file is not an ELF binary", "file_output": file_info["stdout"]}
        if "statically linked" not in desc:
            return {
                "ok": False,
                "error": "poc binary is not statically linked",
                "file_output": file_info["stdout"],
            }
        return {"ok": True}

    @contextlib.contextmanager
    def _temporary_http_server(self, directory: Path, port: int):
        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

        handler = functools.partial(QuietHandler, directory=str(directory))
        server = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    @staticmethod
    def _extract_marked_output(captured: str, begin: str, end: str) -> dict[str, Any]:
        lines = [line.replace("\r", "") for line in captured.splitlines()]
        begin_idx = -1
        begin_col = -1
        end_idx = -1
        exit_code: int | None = None
        end_pattern = re.compile(rf"^{re.escape(end)}:(\d+)$")

        for idx, line in enumerate(lines):
            if begin_idx == -1:
                pos = line.find(begin)
                if pos != -1:
                    begin_idx = idx
                    begin_col = pos
                    continue
            if begin_idx != -1:
                match = end_pattern.match(line.strip())
                if match:
                    end_idx = idx
                    exit_code = int(match.group(1))
                    break

        if begin_idx == -1:
            return {"ok": False, "error": "begin marker not found", "output": captured.strip()}
        if end_idx == -1 or exit_code is None:
            partial = KernelPwnService._collect_payload_lines(lines, begin_idx, begin_col, len(lines))
            return {
                "ok": False,
                "error": "end marker with exit code not found (possible crash/timeout)",
                "output": partial,
            }

        output = KernelPwnService._collect_payload_lines(lines, begin_idx, begin_col, end_idx)

        return {"ok": True, "exit_code": exit_code, "output": output}

    @staticmethod
    def _collect_payload_lines(lines: list[str], begin_idx: int, begin_col: int, stop_idx: int) -> str:
        payload_lines: list[str] = []
        if begin_idx < stop_idx:
            first_line = lines[begin_idx]
            tail = first_line[begin_col + 1 :] if begin_col >= 0 else ""
            if tail.strip():
                payload_lines.append(tail)
        payload_lines.extend(lines[begin_idx + 1:stop_idx])

        cleaned: list[str] = []
        for line in payload_lines:
            stripped = line.strip()
            if not stripped:
                continue
            if re.search(r".+[#$] $", line):
                continue
            cleaned.append(line)
        return "\n".join(cleaned).strip()

    @staticmethod
    def _tmux_session_exists(name: str | None) -> bool:
        if not name:
            return False
        result = run_cmd(["tmux", "has-session", "-t", name], timeout=3)
        return result["ok"]

    def _resolve_session(self, session_id: str | None, require_running: bool = True) -> QemuSession | None:
        resolved_id = session_id or self.state.active_session_id
        if not resolved_id:
            return None
        session = self.state.qemu_sessions.get(resolved_id)
        if session is None:
            return None
        if require_running and not self._tmux_session_exists(session.tmux_session):
            return None
        return session

    @staticmethod
    def _session_payload(session: QemuSession) -> dict[str, Any]:
        return {
            "session_id": session.session_id,
            "release_name": session.release_name,
            "gdb_port": session.gdb_port,
            "serial_port": session.serial_port,
            "tmux_session": session.tmux_session,
            "is_running": session.is_running,
            "started_at": session.started_at.isoformat(),
            "log_path": str(session.log_path),
        }
