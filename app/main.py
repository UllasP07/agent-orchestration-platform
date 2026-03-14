from fastapi import FastAPI

from app.api.graphql_api import graphql_router
from app.api.rest import router as rest_router
from app.core.bootstrap import bootstrap
from app.core.config import settings
from app.db.init_db import init_db
from app.mcp.server import router as mcp_router

bootstrap()
init_db()

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.include_router(rest_router)
app.include_router(graphql_router, prefix="/graphql")
app.include_router(mcp_router)