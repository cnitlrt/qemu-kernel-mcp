from __future__ import annotations

import shlex
import socket
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def run_cmd(cmd: Sequence[str], cwd: Path | None = None, timeout: int = 600) -> dict[str, Any]:
    proc = subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "cmd": " ".join(shlex.quote(x) for x in cmd),
    }


def allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])

