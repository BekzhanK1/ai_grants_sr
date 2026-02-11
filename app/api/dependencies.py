
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

from app.core.config import settings

api_key_header = APIKeyHeader(
    name="X-API-KEY",
    auto_error=True,
    scheme_name="API Key",
    description="Enter your API key (same value as API_KEY in .env)",
)


async def verify_api_key(api_key: str = Security(api_key_header)):
    """
    Validate the X-API-KEY header against the configured secret.
    """
    if api_key != settings.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API Key",
        )
    return api_key
