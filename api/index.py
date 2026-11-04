import asyncio
import json
import logging
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message, ReplyParameters, Update


# =========================
# НАСТРОЙКИ — МЕНЯЙ ЗДЕСЬ
# =========================

BOT_TOKEN = "1234567890:ВАШ_ТОКЕН"

WEBHOOK_SECRET = "my-secret-123"

# file_id стикера
STICKER_FILE_ID = "CAACAgIAAxkBAA..."

# Твой Telegram ID (получи через @userinfobot)
OWNER_ID = 123456789

# Target username БЕЗ @ (в нижнем регистре)
TARGET_USERNAME = "someusername"

# True = отвечает на ЛЮБОЕ сообщение ЛЮБОГО человека
# False = отвечает только на сообщения от TARGET_USERNAME
ALL_USERS_MODE = False


# =========================
# КОД
# =========================

logging.basicConfig(level=logging.INFO)


async def capture_sticker_file_id(message: Message):
    await message.answer(
        "Возьми этот file_id:\n\n"
        f"<code>{message.sticker.file_id}</code>",
        parse_mode="HTML",
    )


async def reply_with_sticker(message: Message):
    if not STICKER_FILE_ID:
        return

    if not message.from_user:
        return

    # Проверка: нужно ли отвечать этому пользователю
    if not ALL_USERS_MODE:
        sender_username = (message.from_user.username or "").lower()
        if sender_username != TARGET_USERNAME:
            return

    await message.bot.send_sticker(
        chat_id=message.chat.id,
        sticker=STICKER_FILE_ID,
        message_thread_id=message.message_thread_id,
        reply_parameters=ReplyParameters(
            message_id=message.message_id,
            allow_sending_without_reply=True,
        ),
    )


async def cmd_set_target(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /set_target @username")
        return

    global TARGET_USERNAME
    TARGET_USERNAME = parts[1].strip().lstrip("@").lower()
    await message.answer(f"✅ Target установлен: @{TARGET_USERNAME}")


async def cmd_all_users(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    global ALL_USERS_MODE
    ALL_USERS_MODE = True
    await message.answer("✅ Режим: отвечаю на ВСЕ сообщения")


async def cmd_only_target(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    global ALL_USERS_MODE
    ALL_USERS_MODE = False
    await message.answer(f"✅ Режим: отвечаю только @{TARGET_USERNAME}")


async def cmd_status(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    mode = "ВСЕ" if ALL_USERS_MODE else f"только @{TARGET_USERNAME}"
    await message.answer(
        f"📊 Статус:\n"
        f"• Target: @{TARGET_USERNAME}\n"
        f"• Режим: {mode}\n"
        f"• Стикер: {'✅' if STICKER_FILE_ID else '❌'}"
    )


def create_router() -> Router:
    router = Router(name="sticker_reply")

    # Команды (в личку)
    router.message.register(cmd_set_target, Command("set_target"), F.chat.type == "private")
    router.message.register(cmd_all_users, Command("all"), F.chat.type == "private")
    router.message.register(cmd_only_target, Command("target"), F.chat.type == "private")
    router.message.register(cmd_status, Command("status"), F.chat.type == "private")
    router.message.register(cmd_status, Command("start"), F.chat.type == "private")

    # Получение file_id стикера
    router.message.register(
        capture_sticker_file_id,
        F.chat.type == "private",
        F.sticker,
    )

    # Основной обработчик в группах
    router.message.register(
        reply_with_sticker,
        F.chat.type.in_({"group", "supergroup"}),
        lambda message: message.from_user is not None and not message.from_user.is_bot,
    )

    return router


async def process_update(raw_body: bytes):
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(create_router())

    try:
        update = Update.model_validate_json(raw_body)
        await dp.feed_update(bot=bot, update=update)
    finally:
        await bot.session.close()


def respond(handler_obj, status: int, text: str, content_type: str = "text/plain"):
    payload = text.encode("utf-8")
    handler_obj.send_response(status)
    handler_obj.send_header("Content-Type", content_type)
    handler_obj.send_header("Content-Length", str(len(payload)))
    handler_obj.end_headers()
    handler_obj.wfile.write(payload)


def json_response(handler_obj, status: int, data: dict):
    respond(
        handler_obj,
        status,
        json.dumps(data, ensure_ascii=False),
        "application/json",
    )


def set_webhook(base_url: str):
    webhook_url = base_url.rstrip("/") + "/api"
    payload = {"url": webhook_url, "allowed_updates": ["message"]}
    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET

    data = json.dumps(payload).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=10) as response:
            return webhook_url, response.status, response.read().decode("utf-8")
    except HTTPError as e:
        return webhook_url, e.code, e.read().decode("utf-8")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        setup = query.get("setup", ["0"])[0].lower()
        secret = query.get("secret", [""])[0]

        if setup in {"1", "true", "yes"}:
            if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
                return json_response(self, 403, {"error": "bad secret"})

            proto = self.headers.get("x-forwarded-proto", "https").split(",")[0].strip()
            host = self.headers.get("host", "").strip()
            base_url = f"{proto}://{host}" if host else ""

            webhook_url, status, text = set_webhook(base_url)
            return json_response(
                self,
                status,
                {"webhook_url": webhook_url, "telegram_response": text},
            )

        return json_response(self, 200, {"ok": True, "service": "Telegram bot"})

    def do_POST(self):
        if WEBHOOK_SECRET:
            secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if secret != WEBHOOK_SECRET:
                return json_response(self, 403, {"error": "bad secret"})

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if body:
            try:
                asyncio.run(process_update(body))
            except Exception:
                logging.exception("Update error")

        return json_response(self, 200, {"ok": True})

    def log_message(self, format, *args):
        pass
        
