from __future__ import annotations

import socket
import tempfile
import threading
import unittest
from pathlib import Path

from qemu_kernel_mcp.serial_proxy import SerialLogProxy


class SerialLogProxyTests(unittest.TestCase):
    def _allocate_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def test_proxy_forwards_bytes_and_logs_backend_output(self) -> None:
        backend_port = self._allocate_port()
        public_port = self._allocate_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "serial.log"
            backend_ready = threading.Event()
            backend_done = threading.Event()
            received_from_client: list[bytes] = []

            def backend_server() -> None:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    server.bind(("127.0.0.1", backend_port))
                    server.listen(1)
                    backend_ready.set()
                    conn, _ = server.accept()
                    with conn:
                        received_from_client.append(conn.recv(4096))
                        conn.sendall(b"boot line 1\n")
                        conn.sendall(b"panic line 2\n")
                    backend_done.set()

            thread = threading.Thread(target=backend_server, daemon=True)
            thread.start()
            self.assertTrue(backend_ready.wait(timeout=1.0))

            proxy = SerialLogProxy(
                listen_host="127.0.0.1",
                listen_port=public_port,
                backend_host="127.0.0.1",
                backend_port=backend_port,
                log_path=log_path,
            )
            proxy.start()
            self.assertTrue(proxy.ready.wait(timeout=1.0))

            with socket.create_connection(
                ("127.0.0.1", public_port), timeout=1.0
            ) as client:
                client.sendall(b"run /bin/exp\n")
                client.shutdown(socket.SHUT_WR)
                chunks: list[bytes] = []
                while True:
                    data = client.recv(4096)
                    if not data:
                        break
                    chunks.append(data)

            self.assertTrue(backend_done.wait(timeout=1.0))
            proxy.close()
            thread.join(timeout=1.0)

            self.assertEqual(received_from_client, [b"run /bin/exp\n"])
            self.assertEqual(b"".join(chunks), b"boot line 1\npanic line 2\n")
            self.assertEqual(log_path.read_bytes(), b"boot line 1\npanic line 2\n")

    def test_close_stops_listener_and_releases_port(self) -> None:
        backend_port = self._allocate_port()
        public_port = self._allocate_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "serial.log"
            proxy = SerialLogProxy(
                listen_host="127.0.0.1",
                listen_port=public_port,
                backend_host="127.0.0.1",
                backend_port=backend_port,
                log_path=log_path,
            )
            proxy.start()
            self.assertTrue(proxy.ready.wait(timeout=1.0))
            proxy.close()

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", public_port))

    def test_close_disconnects_active_client(self) -> None:
        backend_port = self._allocate_port()
        public_port = self._allocate_port()
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "serial.log"
            backend_ready = threading.Event()
            release_backend = threading.Event()
            backend_connected = threading.Event()

            def backend_server() -> None:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    server.bind(("127.0.0.1", backend_port))
                    server.listen(1)
                    backend_ready.set()
                    conn, _ = server.accept()
                    backend_connected.set()
                    with conn:
                        release_backend.wait(timeout=2.0)

            thread = threading.Thread(target=backend_server, daemon=True)
            thread.start()
            self.assertTrue(backend_ready.wait(timeout=1.0))

            proxy = SerialLogProxy(
                listen_host="127.0.0.1",
                listen_port=public_port,
                backend_host="127.0.0.1",
                backend_port=backend_port,
                log_path=log_path,
            )
            proxy.start()
            self.assertTrue(proxy.ready.wait(timeout=1.0))

            with socket.create_connection(
                ("127.0.0.1", public_port), timeout=1.0
            ) as client:
                self.assertTrue(backend_connected.wait(timeout=1.0))
                client.settimeout(0.5)
                proxy.close()
                self.assertEqual(client.recv(1), b"")

            release_backend.set()
            thread.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
