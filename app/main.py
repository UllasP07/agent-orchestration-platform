import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.graphql_api import graphql_router
from app.api.rest import router as rest_router
from app.core.bootstrap import bootstrap
from app.core.config import settings
from app.db.init_db import init_db
from app.execution.worker import RunWorker
from app.mcp.server import router as mcp_router

init_db()
bootstrap()


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker = RunWorker()
    worker_task: asyncio.Task | None = None
    if settings.worker_enabled:
        worker_task = asyncio.create_task(worker.run_forever())
    try:
        yield
    finally:
        if worker_task:
            worker.request_stop()
            await worker_task


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.include_router(rest_router)
app.include_router(graphql_router, prefix="/graphql")
app.include_router(mcp_router)
