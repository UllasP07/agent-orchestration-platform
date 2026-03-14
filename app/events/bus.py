from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from app.models.schemas import PlatformEvent


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[PlatformEvent]] = set()

    async def publish(self, event: PlatformEvent) -> None:
        for queue in list(self._subscribers):
            await queue.put(event)

    async def subscribe(self) -> AsyncGenerator[PlatformEvent, None]:
        queue: asyncio.Queue[PlatformEvent] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)


event_bus = EventBus()