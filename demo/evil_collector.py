"""Evil collector: listens on :9999, logs every request it receives, answers 200.

The demo proves that nothing leaves the sandbox: after a full run the log this
collector writes (data/collector.log by default) must contain zero entries.

Usage:
    python demo/evil_collector.py            # standalone listener
    from demo.evil_collector import serve    # in-process, used by run_demo.py
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger("evil_collector")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 9999


def default_log_path() -> Path:
    """Return data/collector.log, following RS_DATA_DIR like the gateway does."""
    configured = os.environ.get("RS_DATA_DIR", "./data")
    path = Path(configured)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path / "collector.log"


async def _read_head(reader: asyncio.StreamReader) -> bytes:
    return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10.0)


async def handle_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, log_path: Path
) -> None:
    """Log one request and answer 200."""
    try:
        head = await _read_head(reader)
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, OSError):
        writer.close()
        return
    lines = head.decode("latin-1", errors="replace").split("\r\n")
    request_line = lines[0] if lines else ""
    length = 0
    for line in lines[1:]:
        if line.lower().startswith("content-length:"):
            try:
                length = int(line.split(":", 1)[1].strip())
            except ValueError:
                length = 0
    body = b""
    if length > 0:
        try:
            body = await asyncio.wait_for(reader.read(min(length, 65536)), timeout=10.0)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            body = b""
    entry = (
        f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
        f"{request_line} body_bytes={len(body)}"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(entry + "\n")
        handle.flush()
    logger.warning("RECEIVED: %s", entry)
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    try:
        await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    log_path: Path | None = None,
) -> asyncio.AbstractServer:
    """Start the collector and return the running server (caller keeps it open)."""
    target = log_path or default_log_path()
    server = await asyncio.start_server(
        lambda reader, writer: handle_client(reader, writer, target), host, port
    )
    logger.info("Evil collector listening on %s:%s, logging to %s", host, port, target)
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="Log every request it receives to a file.")
    parser.add_argument("--host", default=os.environ.get("RS_COLLECTOR_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("RS_COLLECTOR_PORT", str(DEFAULT_PORT)))
    )
    parser.add_argument("--log", default=os.environ.get("RS_COLLECTOR_LOG", ""))
    args = parser.parse_args()
    log_path = Path(args.log) if args.log else default_log_path()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def runner() -> None:
        server = await serve(args.host, args.port, log_path)
        async with server:
            await server.serve_forever()

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
