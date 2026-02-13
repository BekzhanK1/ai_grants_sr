"""
Run from ai-admin directory as:
    python3 -m app.services.access_request.test
"""

import asyncio
import json

from app.core.database import close_pool, init_pool
from app.services.db_service import (
    get_all_grants_by_module,
    get_all_modules,
    search_users_by_fios,
)


async def test_get_all_modules():
    await init_pool()
    try:
        modules = await get_all_modules()
        data = [m.model_dump(mode="json") for m in modules]
        print(json.dumps(data, indent=2, ensure_ascii=False))
    finally:
        await close_pool()


async def test_get_all_grants_by_module():
    await init_pool()
    try:
        grants = await get_all_grants_by_module(1)
        # GrantDto.model_dump(mode="json") gives JSON-serializable dicts (datetimes → ISO)
        data = [g.model_dump(mode="json") for g in grants]
        print(json.dumps(data, indent=2, ensure_ascii=False))
    finally:
        await close_pool()


async def test_search_users_by_fios():
    await init_pool()
    try:
        users = await search_users_by_fios(["Бекжан Кимад", "Адлет"])
        data = [u.model_dump(mode="json") for u in users]
        print(json.dumps(data, indent=2, ensure_ascii=False))
    finally:
        await close_pool()


async def test_search_users_by_fios_empty():
    await init_pool()
    try:
        users = await search_users_by_fios([])
        data = [u.model_dump(mode="json") for u in users]
        print(json.dumps(data, indent=2, ensure_ascii=False))
    finally:
        await close_pool()


async def test_search_users_by_fios_none():
    await init_pool()
    try:
        users = await search_users_by_fios(None)
        data = [u.model_dump(mode="json") for u in users]
        print(json.dumps(data, indent=2, ensure_ascii=False))
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(test_search_users_by_fios())
