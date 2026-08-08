from __future__ import annotations

import asyncio

from app.core.bootstrap import bootstrap
from app.db.init_db import init_db
from app.execution.worker import RunWorker
from app.external.registry import external_backends


async def serve() -> None:
    init_db()
    bootstrap()
    try:
        await RunWorker().run_forever()
    finally:
        await external_backends.aclose()


def main() -> None:
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
