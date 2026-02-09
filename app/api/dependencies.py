"""
FastAPI dependency functions.
"""

from fastapi import Request

from app.data.reference import ReferenceData


def get_reference_data(request: Request) -> ReferenceData:
    """Retrieve in-memory reference data loaded at startup."""
    return request.app.state.reference_data
