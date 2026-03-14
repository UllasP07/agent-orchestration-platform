from fastapi import Header, HTTPException, status

from app.db.repository import get_api_key_record
from app.models.schemas import APIKeyRecord


async def require_api_key(x_api_key: str | None = Header(default=None)) -> APIKeyRecord:
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )

    record = get_api_key_record(x_api_key)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return record