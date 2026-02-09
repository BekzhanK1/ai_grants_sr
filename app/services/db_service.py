from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncpg

from app.core.config import settings


class DatabaseError(Exception):
    """Domain-level database error."""


class DB:
    """
    Thin asyncpg wrapper with connection pool.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def init(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(dsn=self._dsn, min_size=1, max_size=5)

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[asyncpg.Connection]:
        if self._pool is None:
            await self.init()
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            yield conn


db = DB(settings.DATABASE_URL)


async def _call_proc(  # pragma: no cover - stubbed in dev
    proc_name: str, *args: Any, connection: asyncpg.Connection | None = None
) -> Any:
    """
    Generic stored procedure caller.

    DEV/STUB MODE:
    --------------
    Сейчас реальные вызовы в БД отключены — мы только печатаем,
    что бы было вызвано. Это позволяет гонять весь AI‑workflow
    без поднятого PostgreSQL.

    Чтобы вернуть реальные вызовы, раскомментируй тело ниже
    и убери print.
    """

    print(f"[DB STUB] would call proc {proc_name} with args={args}")

    # Реальная версия (оставлена как комментарий):
    #
    # try:
    #     if connection is not None:
    #         return await connection.fetchval(
    #             f\"SELECT {proc_name}({', '.join(['$' + str(i + 1) for i in range(len(args))])})\",
    #             *args,
    #         )
    #
    #     async with db.connection() as conn:
    #         return await conn.fetchval(
    #             f\"SELECT {proc_name}({', '.join(['$' + str(i + 1) for i in range(len(args))])})\",
    #             *args,
    #         )
    # except Exception as exc:  # noqa: BLE001
    #     raise DatabaseError(f\"Error calling procedure {proc_name}: {exc}\") from exc


# === Safety-aware wrappers for toggle-like procedures ===


async def employee_group_link(employee_id: int, group_id: int) -> None:
    """
    Safely link an employee to a group.

    Under the hood, the DB function works as a toggle. In боевом режиме
    мы бы сначала проверяли наличие записи, чтобы лишний раз не снимать права.
    """
    print(f"[DB STUB] employee_group_link(employee_id={employee_id}, group_id={group_id})")
    # Боевая версия:
    # async with db.connection() as conn:
    #     exists = await conn.fetchval(
    #         '''
    #         SELECT EXISTS (
    #             SELECT 1
    #             FROM employee_group_links
    #             WHERE employee_id = $1 AND group_id = $2
    #         )
    #         ''',
    #         employee_id,
    #         group_id,
    #     )
    #     if not exists:
    #         await _call_proc("employee_group_link", employee_id, group_id, connection=conn)


async def employee_menu_add(employee_id: int, menu_id: int, grant_id: int) -> None:
    """
    Direct ADD operation (non-toggle) for menu grants.
    """
    print(
        "[DB STUB] employee_menu_add("
        f"employee_id={employee_id}, menu_id={menu_id}, grant_id={grant_id})"
    )
    # Боевая версия:
    # await _call_proc("employee_menu__add", employee_id, menu_id, grant_id)


async def employee_module_link(employee_id: int, module_id: int) -> None:
    """
    Safely link an employee to a module.

    As with groups, the underlying DB function is a toggle. В боевом режиме
    мы бы проверяли наличие записи перед вызовом toggle‑функции.
    """
    print(
        "[DB STUB] employee_module_link("
        f"employee_id={employee_id}, module_id={module_id})"
    )
    # Боевая версия:
    # async with db.connection() as conn:
    #     exists = await conn.fetchval(
    #         '''
    #         SELECT EXISTS (
    #             SELECT 1
    #             FROM employee_module_links
    #             WHERE employee_id = $1 AND module_id = $2
    #         )
    #         ''',
    #         employee_id,
    #         module_id,
    #     )
    #     if not exists:
    #         await _call_proc("employee_module_link", employee_id, module_id, connection=conn)


# === Logging helper ===


async def log_ai_request(
    *,
    user_id: int,
    prompt: str,
    tool_name: str | None,
    tool_args: dict[str, Any] | None,
    result_summary: str | None,
) -> None:
    """
    Asynchronous logging of AI-driven requests.

    Assumes that a table `ai_request_logs` exists with appropriate columns.
    """
    print(
        "[DB STUB] log_ai_request("
        f"user_id={user_id}, tool_name={tool_name}, "
        f"result_summary={result_summary}, tool_args={tool_args})"
    )
    # Боевая версия:
    # async with db.connection() as conn:
    #     await conn.execute(
    #         '''
    #         INSERT INTO ai_request_logs (user_id, prompt, tool_name, tool_args, result_summary)
    #         VALUES ($1, $2, $3, $4, $5)
    #         ''',
    #         user_id,
    #         prompt,
    #         tool_name,
    #         tool_args,
    #         result_summary,
    #     )

