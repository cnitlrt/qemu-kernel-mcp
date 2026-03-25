from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from qemu_kernel_mcp.service import KernelPwnService


class _FakeProxy:
    def __init__(self, *args, **kwargs) -> None:
        self.ready = SimpleNamespace(wait=lambda timeout=None: True)
        self.closed = False

    def start(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class ServiceSerialLoggingTests(unittest.TestCase):
    def test_run_qemu_returns_serial_log_metadata(self) -> None:
        service = KernelPwnService()
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("qemu_kernel_mcp.service.STATE_DIR", Path(tmpdir)),
            patch(
                "qemu_kernel_mcp.service.allocate_port",
                side_effect=[30001, 30002, 30003],
            ),
            patch("qemu_kernel_mcp.service.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(service, "_list_tmux_sessions_by_prefix", return_value=[]),
            patch(
                "qemu_kernel_mcp.service.run_cmd",
                return_value={"ok": True, "stdout": "", "stderr": "", "returncode": 0},
            ),
            patch("qemu_kernel_mcp.service.SerialLogProxy", _FakeProxy),
        ):
            result = service.run_qemu("mitigation-v4-6.6")

        self.assertTrue(result["ok"])
        self.assertEqual(result["session"]["serial_port"], 30002)
        self.assertEqual(result["session"]["qemu_serial_backend_port"], 30003)
        self.assertTrue(result["session"]["serial_log_path"].endswith(".serial.log"))
        self.assertEqual(
            result["tips"]["serial_log_file"], result["session"]["serial_log_path"]
        )

    def test_stop_qemu_closes_proxy(self) -> None:
        service = KernelPwnService()
        proxy = _FakeProxy()
        with tempfile.TemporaryDirectory() as tmpdir:
            session = SimpleNamespace(
                session_id="sess1234",
                release_name="mitigation-v4-6.6",
                gdb_port=31001,
                serial_port=31002,
                qemu_serial_backend_port=31003,
                log_path=Path(tmpdir) / "launcher.log",
                serial_log_path=Path(tmpdir) / "serial.log",
                proxy=proxy,
                tmux_session="qemu_kernel_mcp_sess1234",
                is_running=True,
                started_at=SimpleNamespace(
                    isoformat=lambda: "2026-03-26T00:00:00+00:00"
                ),
            )
            service.state.qemu_sessions[session.session_id] = session
            service.state.active_session_id = session.session_id
            with (
                patch.object(service, "_resolve_session", return_value=session),
                patch.object(
                    service, "_tmux_session_exists", side_effect=[True, False]
                ),
                patch(
                    "qemu_kernel_mcp.service.run_cmd",
                    return_value={
                        "ok": True,
                        "stdout": "",
                        "stderr": "",
                        "returncode": 0,
                    },
                ),
            ):
                result = service.stop_qemu(session.session_id)

        self.assertTrue(result["ok"])
        self.assertTrue(proxy.closed)


if __name__ == "__main__":
    unittest.main()
