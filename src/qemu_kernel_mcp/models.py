from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from subprocess import Popen


@dataclass
class QemuSession:
    session_id: str
    release_name: str
    gdb_port: int
    serial_port: int
    log_path: Path
    process: Popen[str] | None = None
    tmux_session: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_running(self) -> bool:
        if self.process is None:
            return True
        return self.process.poll() is None


@dataclass
class ServerState:
    poc_path: Path | None = None
    qemu_sessions: dict[str, QemuSession] = field(default_factory=dict)
    active_session_id: str | None = None
