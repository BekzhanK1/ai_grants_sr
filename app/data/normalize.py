"""
Utilities for normalizing raw JSON exports from the database.

The CSVs exported through pgAdmin / DBeaver often have:
- BOM characters in the first column key  (\ufeff)
- Extra wrapping quotes around key names  ("\"menu_id\"")
- Numeric IDs stored as strings           ("123")
- Literal "null" instead of JSON null
"""

from __future__ import annotations

from typing import Any


def clean_key(key: str) -> str:
    """Strip BOM prefix and wrapping double-quotes from a key name."""
    key = key.lstrip("\ufeff")
    if key.startswith('"') and key.endswith('"'):
        key = key[1:-1]
    return key


def clean_value(key: str, value: Any) -> Any:
    """Convert string 'null' → None; cast *_id strings to int."""
    if isinstance(value, str) and value == "null":
        return None
    if isinstance(value, str) and key.endswith("_id"):
        try:
            return int(value)
        except ValueError:
            return value
    return value


def normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply key + value normalization to every row in a list."""
    result: list[dict[str, Any]] = []
    for row in rows:
        cleaned = {clean_key(k): clean_value(clean_key(k), v) for k, v in row.items()}
        result.append(cleaned)
    return result
