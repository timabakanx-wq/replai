import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, ReplyParameters
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application


# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_TOKEN")

# Публичный HTTPS-адрес твоего сервера, например:
# https://mydomain.com
# https://xxxx.ngrok-free.app
WEBHOOK_PUBLIC_URL = os.getenv("WEBHOOK_PUBLIC_URL", "https://example.com").rstrip("/")

# Путь, куда Telegram будет слать обновления
WEBHOOK_PATH = "/telegram/webhook"

# Секретный токен для проверки, что запросы приходят от Telegram
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-me-secret")

# file_id стикера, которым бот будет отвечать
# Например: "CAACAgIAAxkBAA..."
STICKER_FILE_ID = os.getenv("STICKER_FILE_ID", "")


router = Router(name="sticker_reply_bot")


# =========================
# СЛУЖЕБНЫЙ ХЕНДЛЕР
# =========================
# Если ты ещё не знаешь file_id стикера:
# отправь боту стикер в личку, а он пришлёт тебе file_id.
@router.message(F.chat.type == "private", F.sticker)
async def capture_sticker_file_id(message: Message):
    await message.answer(
        "Возьми этот file_id и вставь его в переменную STICKER_FILE_ID:\n\n"
        f"<code>{message.sticker.file_id}</code>",
        parse_mode="HTML",
    )


# =========================
# ОСНОВНОЙ ХЕНДЛЕР
# =========================
# Отвечает стикером на любое сообщение человека в группе/супергруппе.
# Сообщения от ботов игнорируются.
@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    lambda message: message.from_user is not None and not message.from_user.is_bot,
)
async def reply_with_sticker(message: Message):
    if not STICKER_FILE_ID:
        logging.warning("STICKER_FILE_ID пустой. Сначала задай стикер.")
        return

    await message.bot.send_sticker(
        chat_id=message.chat.id,
        sticker=STICKER_FILE_ID,
        # Для форумов/топиков, если они есть
        message_thread_id=message.message_thread_id,
        # Ответ реплаем на конкретное сообщение
        reply_parameters=ReplyParameters(
            message_id=message.message_id,
            allow_sending_without_reply=True,
        ),
    )


def main():
    logging.basicConfig(level=logging.INFO)

    if BOT_TOKEN == "PASTE_TOKEN":
        raise SystemExit("Укажи BOT_TOKEN в переменных окружения или в коде.")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    async def on_startup():
        webhook_url = f"{WEBHOOK_PUBLIC_URL}{WEBHOOK_PATH}"

        # Удаляем старый вебхук, если он был.
        # drop_pending_updates=True удалит сообщения, которые бот не обработал, пока был выключен.
        await bot.delete_webhook(drop_pending_updates=True)

        await bot.set_webhook(
            url=webhook_url,
            secret_token=WEBHOOK_SECRET,
        )

        logging.info("Webhook установлен: %s", webhook_url)

    dp.startup.register(on_startup)

    app = web.Application()

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    web.run_app(
        app,
        host=os.getenv("LISTEN_HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8443")),
    )


if __name__ == "__main__":
    main()
