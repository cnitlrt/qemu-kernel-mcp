from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import POC_TARGET, SCRIPTS_DIR, STATE_DIR
from .models import QemuSession, ServerState
from .utils import allocate_port, run_cmd


class KernelPwnService:
    def __init__(self) -> None:
        self.state = ServerState()
        self._lock = threading.Lock()

    def set_poc(self, poc_file: str) -> dict[str, Any]:
        source = Path(poc_file).expanduser().resolve()
        if not source.exists():
            return {"ok": False, "error": f"poc file not found: {source}"}
        if not source.is_file():
            return {"ok": False, "error": f"poc path is not a file: {source}"}

        with self._lock:
            POC_TARGET.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, POC_TARGET)
            os.chmod(POC_TARGET, 0o755)
            self.state.poc_path = source
            return {"ok": True, "poc_path": str(source), "vm_path": "/bin/exp"}

    def run_qemu(self, release_name: str) -> dict[str, Any]:
        with self._lock:
            if shutil.which("tmux") is None:
                return {"ok": False, "error": "tmux not found on host"}
            if shutil.which("nc") is None:
                return {"ok": False, "error": "nc not found on host"}

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
                "session": self._session_payload(session),
                "tips": {
                    "tmux_session": tmux_name,
                    "attach": f"tmux attach -t {tmux_name}",
                    "gdb_target": f"target remote 127.0.0.1:{gdb_port}",
                    "serial_connect": f"nc 127.0.0.1 {serial_port}",
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
        nc_idle = str(max(1, min(timeout, 10)))
        try:
            proc = subprocess.run(
                ["nc", "-w", nc_idle, "127.0.0.1", str(session.serial_port)],
                input=payload,
                text=True,
                capture_output=True,
                timeout=timeout + 2,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"returncode": 124, "stdout": "", "stderr": "nc command timeout"}

        captured = proc.stdout or ""
        if end not in captured:
            return {
                "returncode": 124,
                "stdout": "",
                "stderr": "marker not found in nc output",
            }

        match = re.search(rf"{re.escape(end)}:(\d+)", captured)
        rc = int(match.group(1)) if match else proc.returncode
        output = self._clean_nc_output(captured, begin, end)
        return {"returncode": rc, "stdout": output, "stderr": (proc.stderr or "").strip()}

    @staticmethod
    def _clean_nc_output(captured: str, begin: str, end: str) -> str:
        start = captured.rfind(begin)
        stop = captured.rfind(end)
        if start == -1 or stop == -1 or stop < start:
            return captured.strip()
        content = captured[start + len(begin):stop]
        lines = [ln for ln in content.splitlines() if ln.strip()]
        cleaned: list[str] = []
        for line in lines:
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
