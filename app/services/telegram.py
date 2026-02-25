import asyncio
import logging
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import settings


logger = logging.getLogger(__name__)


async def send_telegram_message(text: str) -> None:
    """
    Отправка уведомления в Telegram о заявке на доступ.
    Использует стандартную библиотеку (без сторонних зависимостей).
    """
    token = settings.TG_BOT_TOKEN
    chat_id = settings.TG_CHAT_ID

    if not token or not chat_id:
        # Ничего не делаем, если не настроены переменные окружения
        logger.debug("Telegram notifications are disabled (no TG_BOT_TOKEN / TG_CHAT_ID)")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    data = urlencode(payload).encode("utf-8")

    def _send() -> None:
        try:
            req = Request(url, data=data)
            with urlopen(req, timeout=5) as resp:
                # просто читаем ответ, чтобы запрос завершился
                resp.read()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to send Telegram message: %s", exc)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _send)

