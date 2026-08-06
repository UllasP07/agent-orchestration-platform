from __future__ import annotations

import asyncio

from app.core.bootstrap import bootstrap
from app.db.init_db import init_db
from app.execution.worker import RunWorker


async def serve() -> None:
    init_db()
    bootstrap()
    await RunWorker().run_forever()


def main() -> None:
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
