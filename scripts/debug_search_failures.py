
import asyncio
import logging
from app.services import db_service
from app.core.database import init_pool, close_pool

logging.basicConfig(level=logging.INFO)

async def main():
    await init_pool()
    try:
        # Scenario 7: "остатки на складе" -> expecting menu 388
        print("\n--- Scenario 7: Logistics ---")
        q = "остатки на складе"
        res = await db_service.search_menu(q)
        print(f"Query: '{q}' -> Found {len(res)} menus")
        for r in res:
            print(f"  {r['menu_name']} (ID: {r['menu_id']})")
            
        print("\n--- Scenario 9: Showroom ---")
        q = "забронировать комнату в шоуруме"
        res = await db_service.search_menu(q)
        print(f"Query: '{q}' -> Found {len(res)} menus")
        for r in res:
            print(f"  {r['menu_name']} (ID: {r['menu_id']})")
            
        # Try split words
        q = "шоурум"
        res = await db_service.search_menu(q)
        print(f"Query: '{q}' -> Found {len(res)} menus")
        for r in res:
            print(f"  {r['menu_name']} (ID: {r['menu_id']})")

        print("\n--- Scenario 15: Debt ---")
        q = "скорректировать долг"
        res = await db_service.search_menu(q)
        print(f"Query: '{q}' -> Found {len(res)} menus")
        for r in res:
            print(f"  {r['menu_name']} (ID: {r['menu_id']})")
            
        q = "корректировка долга"
        res = await db_service.search_menu(q)
        print(f"Query: '{q}' -> Found {len(res)} menus")
        for r in res:
            print(f"  {r['menu_name']} (ID: {r['menu_id']})")

    finally:
        await close_pool()

if __name__ == "__main__":
    asyncio.run(main())
