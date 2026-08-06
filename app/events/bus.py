from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from app.models.schemas import PlatformEvent


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[asyncio.Queue[PlatformEvent], str] = {}

    async def publish(self, event: PlatformEvent) -> None:
        for queue, app_id in list(self._subscribers.items()):
            if event.app_id == app_id:
                await queue.put(event)

    async def subscribe(self, app_id: str) -> AsyncGenerator[PlatformEvent, None]:
        queue: asyncio.Queue[PlatformEvent] = asyncio.Queue()
        self._subscribers[queue] = app_id
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.pop(queue, None)


event_bus = EventBus()
