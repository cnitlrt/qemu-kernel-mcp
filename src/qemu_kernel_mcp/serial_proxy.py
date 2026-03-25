from __future__ import annotations

import contextlib
import socket
import threading
from pathlib import Path


class SerialLogProxy:
    def __init__(
        self,
        listen_host: str,
        listen_port: int,
        backend_host: str,
        backend_port: int,
        log_path: Path,
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.log_path = Path(log_path)
        self.ready = threading.Event()
        self._stop = threading.Event()
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._workers: list[threading.Thread] = []
        self._active_sockets: set[socket.socket] = set()
        self._sockets_lock = threading.Lock()

    def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.listen_host, self.listen_port))
        listener.listen(5)
        listener.settimeout(0.2)
        self._listener = listener
        self._register_socket(listener)
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()
        self.ready.set()

    def close(self) -> None:
        self._stop.set()
        with self._sockets_lock:
            sockets = list(self._active_sockets)
        for sock in sockets:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                sock.close()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=1.0)
        for worker in self._workers:
            worker.join(timeout=1.0)

    def _register_socket(self, sock: socket.socket) -> None:
        with self._sockets_lock:
            self._active_sockets.add(sock)

    def _unregister_socket(self, sock: socket.socket) -> None:
        with self._sockets_lock:
            self._active_sockets.discard(sock)

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self._register_socket(client)
            worker = threading.Thread(
                target=self._handle_client, args=(client,), daemon=True
            )
            self._workers.append(worker)
            worker.start()

    def _handle_client(self, client: socket.socket) -> None:
        backend: socket.socket | None = None
        try:
            with client:
                backend = socket.create_connection(
                    (self.backend_host, self.backend_port), timeout=1.0
                )
                self._register_socket(backend)
                with backend:
                    to_backend = threading.Thread(
                        target=self._pipe,
                        args=(client, backend, None),
                        daemon=True,
                    )
                    from_backend = threading.Thread(
                        target=self._pipe,
                        args=(backend, client, self.log_path),
                        daemon=True,
                    )
                    to_backend.start()
                    from_backend.start()
                    to_backend.join()
                    with contextlib.suppress(OSError):
                        backend.shutdown(socket.SHUT_WR)
                    from_backend.join()
        finally:
            self._unregister_socket(client)
            if backend is not None:
                self._unregister_socket(backend)
                with contextlib.suppress(OSError):
                    backend.close()
            with contextlib.suppress(OSError):
                client.close()

    @staticmethod
    def _pipe(src: socket.socket, dst: socket.socket, log_path: Path | None) -> None:
        while True:
            try:
                chunk = src.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            if log_path is not None:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("ab") as fh:
                    fh.write(chunk)
                    fh.flush()
            try:
                dst.sendall(chunk)
            except OSError:
                break
