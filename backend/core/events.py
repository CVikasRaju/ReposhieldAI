"""In-process event bus for the dashboard WebSocket."""

from __future__ import annotations

import asyncio
from typing import Any


class EventBus:
    """Fan-out of gateway events to WebSocket subscribers."""

    def __init__(self, queue_size: int = 1000) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._queue_size = queue_size
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register a subscriber and return its queue."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber."""
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(self, message_type: str, payload: dict[str, Any]) -> None:
        """Send one message to every subscriber, dropping the oldest if full."""
        message = {"type": message_type, "payload": payload}
        async with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:  # pragma: no cover - raced with another producer
                pass

    @property
    def subscriber_count(self) -> int:
        """Number of connected subscribers."""
        return len(self._subscribers)
