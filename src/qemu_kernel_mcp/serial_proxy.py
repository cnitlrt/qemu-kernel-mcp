from __future__ import annotations

import contextlib
import socket
import threading
import time
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
        self._backend_thread: threading.Thread | None = None
        self._workers: list[threading.Thread] = []
        self._active_sockets: set[socket.socket] = set()
        self._sockets_lock = threading.Lock()
        self._backend_socket: socket.socket | None = None
        self._backend_lock = threading.Lock()
        self._backend_ready = threading.Event()
        self._client_socket: socket.socket | None = None
        self._client_lock = threading.Lock()

    def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.listen_host, self.listen_port))
        listener.listen(5)
        listener.settimeout(0.2)
        self._listener = listener
        self._register_socket(listener)
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._backend_thread = threading.Thread(target=self._backend_loop, daemon=True)
        self._accept_thread.start()
        self._backend_thread.start()
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
        if self._backend_thread is not None:
            self._backend_thread.join(timeout=1.0)
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

    def _backend_loop(self) -> None:
        while not self._stop.is_set():
            try:
                backend = socket.create_connection(
                    (self.backend_host, self.backend_port),
                    timeout=0.5,
                )
            except OSError:
                time.sleep(0.1)
                continue

            backend.settimeout(0.2)
            self._register_socket(backend)
            with self._backend_lock:
                self._backend_socket = backend
                self._backend_ready.set()

            try:
                self._read_backend(backend)
            finally:
                with self._backend_lock:
                    if self._backend_socket is backend:
                        self._backend_socket = None
                        self._backend_ready.clear()
                self._unregister_socket(backend)
                with contextlib.suppress(OSError):
                    backend.close()
                self._close_client()
                if not self._stop.is_set():
                    time.sleep(0.1)

    def _read_backend(self, backend: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                chunk = backend.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            self._append_log(chunk)
            client = self._current_client()
            if client is None:
                continue
            try:
                client.sendall(chunk)
            except OSError:
                self._close_client(client)

    def _handle_client(self, client: socket.socket) -> None:
        client.settimeout(0.2)
        previous = self._swap_client(client)
        if previous is not None:
            self._close_socket(previous)
        try:
            with client:
                while not self._stop.is_set():
                    try:
                        chunk = client.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
                    backend = self._wait_for_backend()
                    if backend is None:
                        break
                    try:
                        backend.sendall(chunk)
                    except OSError:
                        break
        finally:
            self._clear_client(client)
            self._close_socket(client)

    def _swap_client(self, client: socket.socket) -> socket.socket | None:
        with self._client_lock:
            previous = self._client_socket
            self._client_socket = client
            return previous

    def _clear_client(self, client: socket.socket) -> None:
        with self._client_lock:
            if self._client_socket is client:
                self._client_socket = None

    def _current_client(self) -> socket.socket | None:
        with self._client_lock:
            return self._client_socket

    def _close_client(self, client: socket.socket | None = None) -> None:
        target = client or self._current_client()
        if target is None:
            return
        self._clear_client(target)
        self._close_socket(target)

    def _close_socket(self, sock: socket.socket) -> None:
        self._unregister_socket(sock)
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            sock.close()

    def _wait_for_backend(self) -> socket.socket | None:
        while not self._stop.is_set():
            if not self._backend_ready.wait(timeout=0.2):
                continue
            with self._backend_lock:
                backend = self._backend_socket
            if backend is not None:
                return backend
        return None

    def _append_log(self, chunk: bytes) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("ab") as fh:
            fh.write(chunk)
            fh.flush()
