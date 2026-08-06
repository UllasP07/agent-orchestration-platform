from __future__ import annotations

import asyncio
import json
import sys

from app.core.bootstrap import bootstrap
from app.core.config import settings
from app.db.init_db import init_db
from app.db.repository import get_api_key_record
from app.models.schemas import MCPRequest
from app.mcp.server import process_mcp


async def serve() -> None:
    init_db()
    bootstrap()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = MCPRequest.model_validate(json.loads(line))
        api_key = get_api_key_record(settings.api_key) if settings.api_key else None
        if not api_key:
            response = {
                "jsonrpc": "2.0",
                "id": request.id,
                "error": {
                    "code": -32001,
                    "message": "Set DEVPLATFORM_API_KEY to an active app API key",
                },
            }
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            continue
        response = await process_mcp(request, api_key)
        sys.stdout.write(response.model_dump_json() + "\n")
        sys.stdout.flush()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
