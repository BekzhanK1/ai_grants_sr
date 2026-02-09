"""
Application-wide custom exceptions.
"""


class AIServiceError(Exception):
    """Raised when the OpenAI API call fails or returns an unexpected result."""


class DatabaseError(Exception):
    """Raised when a database operation fails."""
