import asyncio
import logging
import os
from http.server import BaseHTTPRequestHandler

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, ReplyParameters, Update


logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

router = Router(name="vercel_sticker_reply")


# Если отправить боту стикер в личку, он пришлёт его file_id.
@router.message(F.chat.type == "private", F.sticker)
async def capture_sticker_file_id(message: Message):
    await message.answer(
        "Возьми этот file_id и вставь его в переменную окружения "
        "STICKER_FILE_ID:\n\n"
        f"<code>{message.sticker.file_id}</code>",
        parse_mode="HTML",
    )


# Основной обработчик:
# отвечает стикером на любое сообщение человека в группе/супергруппе.
@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    lambda message: message.from_user is not None and not message.from_user.is_bot,
)
async def reply_with_sticker(message: Message):
    sticker_id = os.getenv("STICKER_FILE_ID", "")

    if not sticker_id:
        logging.warning("STICKER_FILE_ID пустой. Бот не знает, какой стикер отправлять.")
        return

    await message.bot.send_sticker(
        chat_id=message.chat.id,
        sticker=sticker_id,
        message_thread_id=message.message_thread_id,
        reply_parameters=ReplyParameters(
            message_id=message.message_id,
            allow_sending_without_reply=True,
        ),
    )


async def process_update(raw_body: bytes):
    """
    Создаём Bot и Dispatcher на каждый запрос.
    Для serverless это безопаснее, чем переиспользовать один глобальный объект
    между разными event loop'ами.
    """
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    try:
        update = Update.model_validate_json(raw_body)
        await dp.feed_update(bot=bot, update=update)
    finally:
        await bot.session.close()


def send_text(handler: BaseHTTPRequestHandler, status: int, text: str):
    payload = text.encode("utf-8")

    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Проверка секретного токена вебхука.
        if WEBHOOK_SECRET:
            secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if secret != WEBHOOK_SECRET:
                return send_text(self, 403, "Forbidden: bad secret token")

        if not BOT_TOKEN:
            logging.error("BOT_TOKEN не задан в переменных окружения.")
            return send_text(self, 200, "missing BOT_TOKEN")

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b""

        if not body:
            return send_text(self, 200, "empty body")

        try:
            asyncio.run(process_update(body))
        except Exception:
            logging.exception("Ошибка при обработке обновления от Telegram.")

        # Telegram лучше возвращать 200, чтобы он не долбил повторными запросами,
        # если ошибка внутри обработки не критичная.
        return send_text(self, 200, "OK")

    def do_GET(self):
        return send_text(self, 200, "Telegram webhook endpoint is working.")

    def log_message(self, format, *args):
        # Отключаем стандартный шумный лог BaseHTTPRequestHandler.
        pass
