from __future__ import annotations

import argparse

from .server import create_app


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="qemu-kernel-mcp")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http", "stream-http"],
        help="MCP transport mode",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host for HTTP-based transports")
    parser.add_argument("--port", type=int, default=8000, help="bind port for HTTP-based transports")
    parser.add_argument("--mount-path", default="/", help="mount path for HTTP-based transports")
    parser.add_argument("--sse-path", default="/sse", help="SSE endpoint path")
    parser.add_argument("--message-path", default="/messages/", help="SSE message endpoint path")
    parser.add_argument("--streamable-http-path", default="/mcp", help="streamable-http endpoint path")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    transport = "streamable-http" if args.transport == "stream-http" else args.transport
    app = create_app(
        host=args.host,
        port=args.port,
        mount_path=args.mount_path,
        sse_path=args.sse_path,
        message_path=args.message_path,
        streamable_http_path=args.streamable_http_path,
    )
    app.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
