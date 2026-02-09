"""
In-memory reference data loaded once at application startup (singleton).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.data.normalize import normalize_rows

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ReferenceData:
    """Holds menus, groups and grants exported from Smart Remont DB."""

    menus: list[dict[str, Any]] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    grants: list[dict[str, Any]] = field(default_factory=list)


def _load_json(file_path: Path) -> list[dict[str, Any]]:
    if not file_path.exists():
        logger.warning("Reference file not found: %s", file_path)
        return []
    with file_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        logger.warning("Expected list in %s, got %s", file_path, type(data).__name__)
        return []
    return normalize_rows(data)


_singleton: ReferenceData | None = None


async def load_reference_data(base_dir: Path) -> ReferenceData:
    """Load reference JSON files into memory (singleton, called once at startup)."""
    global _singleton  # noqa: PLW0603
    if _singleton is not None:
        return _singleton

    menus = _load_json(base_dir / "admin_menu_tab.json")
    groups = _load_json(base_dir / "admin_group_tab.json")
    grants = _load_json(base_dir / "admin_grant_tab.json")

    _singleton = ReferenceData(menus=menus, groups=groups, grants=grants)

    logger.info(
        "Reference data loaded: %d menus, %d groups, %d grants",
        len(menus),
        len(groups),
        len(grants),
    )
    return _singleton
