"""
FastAPI dependency functions.
"""

from app.data.reference import ReferenceData, get_reference_data


async def get_ref_data() -> ReferenceData:
    """
    Return cached reference data, auto-refreshing from DB when TTL expires.
    """
    return await get_reference_data()
