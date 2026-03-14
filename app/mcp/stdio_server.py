from __future__ import annotations

import asyncio
import json
import sys

from app.models.schemas import MCPRequest
from app.mcp.server import handle_mcp


async def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = MCPRequest.model_validate(json.loads(line))
        response = await handle_mcp(request)
        sys.stdout.write(response.model_dump_json() + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())