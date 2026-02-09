import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ReferenceData:
    menus: list[dict[str, Any]]
    groups: list[dict[str, Any]]
    grants: list[dict[str, Any]]

    @staticmethod
    def _clean_key(key: str) -> str:
        """
        Normalize keys exported from the DB.

        In your JSON we have things like '\\ufeff\"menu_id\"' as keys.
        This function:
        - убирает BOM (\ufeff)
        - убирает обрамляющие кавычки
        """
        # Strip BOM
        key = key.lstrip("\ufeff")
        # Strip wrapping quotes, e.g. "\"menu_id\"" -> menu_id
        if key.startswith('"') and key.endswith('"'):
            key = key[1:-1]
        return key

    @classmethod
    def _normalize_list(cls, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Bring exported rows to a clean, consistent shape:
        - нормализуем ключи
        - конвертируем *_id из строк в int
        - "null" -> None
        """

        def convert_value(key: str, value: Any) -> Any:
            if isinstance(value, str) and value == "null":
                return None
            if isinstance(value, str) and key.endswith("_id"):
                # best-effort int conversion
                try:
                    return int(value)
                except ValueError:
                    return value
            return value

        normalized: list[dict[str, Any]] = []
        for row in data:
            cleaned: dict[str, Any] = {}
            for raw_key, raw_value in row.items():
                key = cls._clean_key(raw_key)
                cleaned[key] = convert_value(key, raw_value)
            normalized.append(cleaned)
        return normalized

    @classmethod
    def from_files(cls, base_dir: Path) -> "ReferenceData":
        def load_json(name: str) -> list[dict[str, Any]]:
            file_path = base_dir / name
            if not file_path.exists():
                return []
            with file_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return cls._normalize_list(data)
            return []

        return cls(
            # Reference data: directly from your /json exports
            menus=load_json("admin_menu_tab.json"),
            groups=load_json("admin_group_tab.json"),
            grants=load_json("admin_grant_tab.json"),
        )


_REFERENCE_DATA_SINGLETON: ReferenceData | None = None


async def load_reference_data(base_dir: Path) -> ReferenceData:
    """
    Load reference JSON data into memory (singleton).

    Even though filesystem IO is synchronous, this is exposed as async to
    integrate naturally with FastAPI lifespan hooks.
    """
    global _REFERENCE_DATA_SINGLETON
    if _REFERENCE_DATA_SINGLETON is None:
        _REFERENCE_DATA_SINGLETON = ReferenceData.from_files(base_dir)
    return _REFERENCE_DATA_SINGLETON

