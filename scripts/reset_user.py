
import asyncio
import logging
from app.core.database import init_pool, close_pool, get_pool

logging.basicConfig(level=logging.INFO)

async def reset_user(employee_id: int):
    await init_pool()
    pool = get_pool()
    async with pool.acquire() as conn:
        print(f"Resetting permissions for employee_id={employee_id}...")
        
        # 1. Clear groups
        await conn.execute("DELETE FROM admin.employee_group_tab WHERE employee_id = $1", employee_id)
        
        # 2. Clear menus
        await conn.execute("DELETE FROM admin.employee_menu_tab WHERE employee_id = $1", employee_id)
        
        # 3. Clear grants
        await conn.execute("DELETE FROM admin.employee_grant_tab WHERE employee_id = $1", employee_id)
        
        # 4. Clear modules
        await conn.execute("DELETE FROM admin.employee_module_tab WHERE employee_id = $1", employee_id)
        
        print("Done.")
    await close_pool()

if __name__ == "__main__":
    asyncio.run(reset_user(1842))
