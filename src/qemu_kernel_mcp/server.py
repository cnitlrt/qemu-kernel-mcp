from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .service import KernelPwnService

def create_app(
    host: str = "127.0.0.1",
    port: int = 8000,
    mount_path: str = "/",
    sse_path: str = "/sse",
    message_path: str = "/messages/",
    streamable_http_path: str = "/mcp",
) -> FastMCP:
    mcp = FastMCP(
        "qemu-kernel-mcp",
        host=host,
        port=port,
        mount_path=mount_path,
        sse_path=sse_path,
        message_path=message_path,
        streamable_http_path=streamable_http_path,
    )
    svc = KernelPwnService()

    @mcp.tool()
    def set_poc(poc_file: str) -> dict[str, Any]:
        """Set PoC binary/script to be used inside QEMU."""
        return svc.set_poc(poc_file)

    @mcp.tool()
    def run_qemu(release_name: str = "mitigation-v4-6.6") -> dict[str, Any]:
        """Start a QEMU session with an auto-assigned gdb port."""
        return svc.run_qemu(release_name)

    @mcp.tool()
    def run_command(command: str, timeout: int = 15, session_id: str = "") -> dict[str, Any]:
        """Execute a shell command in guest through tmux-managed serial."""
        return svc.run_command(command, timeout=timeout, session_id=session_id or None)

    @mcp.tool()
    def run_poc(command: str = "/bin/exp", timeout: int = 20, session_id: str = "") -> dict[str, Any]:
        """Run PoC command in guest."""
        return svc.run_poc(cmd=command, timeout=timeout, session_id=session_id or None)

    @mcp.tool()
    def list_sessions() -> dict[str, Any]:
        """List all QEMU sessions."""
        return svc.list_sessions()

    @mcp.tool()
    def stop_qemu(session_id: str = "", force: bool = False) -> dict[str, Any]:
        """Stop one QEMU session by id (or stop active session if id is empty)."""
        return svc.stop_qemu(session_id=session_id or None, force=force)

    return mcp


mcp = create_app()
