"""Evil collector: listens on :9999, logs every request it receives, and answers 200.

Used by the demo to prove that no data leaves the sandbox when the gateway
blocks egress to this host.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
from pathlib import Path

import httpx

logger = logging.getLogger("evil_collector")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
logger.addHandler(handler)


async def handle_request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> None:
    """Handle a single HTTP request, log it, and return 200."""
    try:
        response = await client.request(method, url, **kwargs)
        logger.info(
            "Received %s %s -> %s %s",
            method,
            url,
            response.status_code,
            response.text[:200] if response.text else "",
        )
    except Exception as exc:
        logger.info("Request %s %s failed: %s", method, url, exc)


async def main() -> None:
    server = await asyncio.start_server(
        lambda r, w: handle_request(r, r.method, r.target),
        "0.0.0.0",
        9999,
    )

    logger.info("Evil collector listening on :9999")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())